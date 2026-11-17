"""S-048 study: daily breakouts on the S-042 panel, run through the as-implemented engine. TEST ONLY. Spec frozen in Agentic-Lab
system/06-research/specs/S-048-daily-breakout.md (merged before any signal was computed).

  python3 -m consumer.breakout_study PANEL_DIR OUT_DIR

Writes s048_results.json and s048_results.md (aggregate numbers only; no symbols).
"""
import json
import random
import sys
from pathlib import Path

from consumer import oversold_panel as op
from engine import backtest
from engine.portfolio import Portfolio
from engine.strategies import breakout as bo
from engine.strategies.oversold_v3 import OversoldV3

RESAMPLES, SEED = 5000, 7
LO_Q, HI_Q = 0.00625, 0.99375          # 98.75 percent interval (four variants, Bonferroni)
MIN_MEAN_PCT = 0.30
MIN_POOLED, MIN_EACH = 300, 100
# cost scenarios, per side, in basis points: (slip, entry_gap, exit_gap). Primary is the 10:00 ET scenario of docs/ENGINE.md.
SCENARIOS = {"primary_9bps_with_drift": (9.0, 0.5, -5.8), "sensitivity_5bps": (5.0, 0.0, 0.0), "sensitivity_20bps": (20.0, 0.0, 0.0)}


def mean_interval(rets, weeks, resamples=RESAMPLES, seed=SEED):
    """Mean of rets (percent) with a week-clustered bootstrap interval. None when empty."""
    if not rets:
        return None
    groups = {}
    for r, w in zip(rets, weeks):
        g = groups.setdefault(w, [0.0, 0])
        g[0] += r
        g[1] += 1
    cl = list(groups.values())
    rnd, m, means = random.Random(seed), len(cl), []
    for _ in range(resamples):
        s = c = 0
        for __ in range(m):
            x = cl[rnd.randrange(m)]
            s += x[0]
            c += x[1]
        means.append(s / c)
    means.sort()
    return {"n": len(rets), "weeks": m, "mean": sum(rets) / len(rets), "lo": means[int(LO_Q * resamples)], "hi": means[int(HI_Q * resamples) - 1]}


def spy_return(spy, entry_date, exit_date):
    """SPY open-to-open between the trade's entry and exit dates (market-adjustment benchmark)."""
    idx = {d: i for i, d in enumerate(spy["date"])}
    a, b = idx.get(entry_date), idx.get(exit_date)
    return None if a is None or b is None else spy["o"][b] / spy["o"][a] - 1.0


def evaluate(trades, spy):
    """trades: engine Trade objects (or dicts with entry_date, exit_date, ret, how). Returns windows, pooled, bars and the pre-registered verdict."""
    rows = []
    for t in trades:
        g = (lambda k: t[k]) if isinstance(t, dict) else (lambda k: getattr(t, k))
        s = spy_return(spy, g("entry_date"), g("exit_date"))
        rows.append({"entry": g("entry_date"), "ret": 100.0 * g("ret"), "adj": None if s is None else 100.0 * (g("ret") - s), "how": g("how"), "week": op.week_key(g("entry_date"))})

    def pick(lo, hi):
        return [r for r in rows if lo <= r["entry"] <= hi]
    wins = {w: pick(*op.WINDOWS[w]) for w in op.WINDOWS}
    pooled = wins["Judge"] + wins["Final"]
    out = {"windows": {w: mean_interval([r["ret"] for r in rs], [r["week"] for r in rs]) for w, rs in wins.items()}}
    out["pooled_judge_final"] = mean_interval([r["ret"] for r in pooled], [r["week"] for r in pooled])
    adj = [r["adj"] for r in pooled if r["adj"] is not None]
    out["pooled_market_adjusted_mean"] = sum(adj) / len(adj) if adj else None
    out["stop_share"] = sum(1 for r in rows if r["how"] == "STOP") / len(rows) if rows else None
    p, j, f = out["pooled_judge_final"], out["windows"]["Judge"], out["windows"]["Final"]
    bars = {
        "1_pooled_mean_at_least_0.30_pct_and_interval_above_zero": bool(p and p["mean"] >= MIN_MEAN_PCT and p["lo"] > 0),
        "2_positive_in_judge_and_final": bool(j and f and j["mean"] > 0 and f["mean"] > 0),
        "3_pooled_market_adjusted_above_zero": bool(out["pooled_market_adjusted_mean"] is not None and out["pooled_market_adjusted_mean"] > 0),
        "4_trade_counts_300_pooled_100_each": bool(p and j and f and p["n"] >= MIN_POOLED and j["n"] >= MIN_EACH and f["n"] >= MIN_EACH),
    }
    out["bars"] = bars
    out["verdict"] = "PASS" if all(bars.values()) else ("REJECTED" if p and (p["mean"] <= 0 or p["hi"] < 0) else "INCONCLUSIVE")
    return out


def correlation(a, b):
    """Pearson correlation of two dicts of daily values over the union of their dates (missing = 0). None if either has no variation."""
    days = sorted(set(a) | set(b))
    x, y = [a.get(d, 0.0) for d in days], [b.get(d, 0.0) for d in days]
    n = len(days)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = sum((v - mx) ** 2 for v in x) ** 0.5
    sy = sum((v - my) ** 2 for v in y) ** 0.5
    return None if sx == 0 or sy == 0 else sum((p - mx) * (q - my) for p, q in zip(x, y)) / (sx * sy)


def run_scenario(strategy, data, universe, prepared, scenario):
    slip, eg, xg = SCENARIOS[scenario]
    return backtest.run(strategy, Portfolio(max_positions=20 if strategy.name != "s042v3" else 10), data, universe, entry_gap=eg / 1e4, slip=slip / 1e4, slip_out=slip / 1e4, exit_gap=xg / 1e4, prepared=prepared)


def render(res):
    lines = ["# S-048 results: daily breakouts", "", "Net return per trade in percent, week-clustered 98.75 percent interval, primary cost scenario (9 bps per side with drift). Verdicts are from the pre-registered bar.", ""]
    for v, r in res["variants"].items():
        p = r["primary"]
        lines += [f"## {v}: {p['verdict']}", "", "| Window | Trades | Mean | 98.75% interval |", "|---|---|---|---|"]
        for w, x in p["windows"].items():
            lines.append(f"| {w} | {x['n']} | {x['mean']:+.2f} | {x['lo']:+.2f} to {x['hi']:+.2f} |" if x else f"| {w} | 0 | n/a | n/a |")
        x = p["pooled_judge_final"]
        if x:
            lines.append(f"| Judge+Final | {x['n']} | {x['mean']:+.2f} | {x['lo']:+.2f} to {x['hi']:+.2f} |")
        lines += ["", f"Market-adjusted pooled mean: {p['pooled_market_adjusted_mean']}; stop share: {p['stop_share']}", "Bars: " + "; ".join(f"{k}: {'met' if ok else 'MISSED'}" for k, ok in p["bars"].items()),
                  "Sensitivity (pooled mean %): " + ", ".join(f"{k} {((r[k]['pooled_judge_final'] or {}).get('mean')):+.2f}" for k in r if k.startswith("sensitivity")),
                  f"Correlation of daily P&L with S-042: {r['corr_with_s042']}", ""]
    return "\n".join(lines)


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 2:
        print(__doc__)
        return 2
    panel, out = Path(a[0]), Path(a[1])
    out.mkdir(parents=True, exist_ok=True)
    data, universe, digest = op.load_panel(panel)
    prepared = backtest.prepare_market(data, universe)
    base = run_scenario(OversoldV3(), data, universe, prepared, "primary_9bps_with_drift")
    res = {"panel_sha256": digest, "variants": {}}
    for v in bo.VARIANTS:
        entry = {}
        for sc in SCENARIOS:
            r = run_scenario(bo.Breakout(v), data, universe, prepared, sc)
            entry["primary" if sc.startswith("primary") else sc] = evaluate(r["trades"], data["SPY"])
            if sc.startswith("primary"):
                entry["corr_with_s042"] = correlation(r["daily_pnl"], base["daily_pnl"])
        res["variants"][v] = entry
        print(v, entry["primary"]["verdict"], flush=True)
    (out / "s048_results.json").write_text(json.dumps(res, indent=1))
    (out / "s048_results.md").write_text(render(res))
    print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
