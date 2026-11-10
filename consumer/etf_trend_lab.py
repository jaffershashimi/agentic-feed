"""S-037 ETF time-series momentum lab (TEST ONLY, REPLAY-DAILY). Standard library only, offline, no orders.

Implements the frozen spec Agentic-Lab system/06-research/specs/S-037-etf-tsmom.md exactly: monthly decisions at the
close, 252-day (or variant) trailing total-return filter, inverse-volatility base weights over ALL eligible ETFs,
unheld weight is cash, trades at the next adjusted open, 0.05% per side, circular block bootstrap on paired daily
returns. Never writes the paper ledger. Never pooled with PAPER, REPLAY or SYNTHETIC.

    python3 -I -m consumer.etf_trend_lab --daily daily_tr.csv --out results.json [--commit SHA]

Input columns: symbol,begins_at,open,high,low,close,volume (dividend-adjusted).
"""
import argparse
import csv
import hashlib
import json
import math
import random
import sys
from datetime import date
from pathlib import Path

UNIVERSE = ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ", "DBC")
COST = 0.0005
MIN_BARS = 253
VOL_N = 60
WINDOWS = {"A": (date(2001, 1, 2), date(2006, 12, 29)), "B": (date(2007, 10, 1), date(2026, 11, 9)),
           "FULL": (date(2001, 1, 2), date(2026, 11, 9))}
VARIANTS = {"V0": {"lookback": 252, "weighting": "invvol"}, "V1": {"lookback": 126, "weighting": "invvol"},
            "V2": {"lookback": 189, "weighting": "invvol"}, "V3": {"lookback": 252, "weighting": "equal"}}


def load(path, universe=UNIVERSE):
    by = {s: {} for s in universe}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            s = r["symbol"].strip()
            if s in by:
                by[s][date.fromisoformat(r["begins_at"][:10])] = (float(r["open"]), float(r["close"]))
    days = sorted(by["SPY"])
    idx = {d: i for i, d in enumerate(days)}
    o, c, first = {}, {}, {}
    for s in universe:
        o[s] = [None] * len(days)
        c[s] = [None] * len(days)
        for d, (op, cl) in by[s].items():
            if d in idx:
                o[s][idx[d]], c[s][idx[d]] = op, cl
        first[s] = next((i for i, x in enumerate(c[s]) if x is not None), None)
        if first[s] is not None:  # a missing day carries the last price (no trade that day)
            for i in range(first[s] + 1, len(days)):
                if c[s][i] is None:
                    c[s][i] = o[s][i] = c[s][i - 1]
    return {"days": days, "open": o, "close": c, "first": first, "universe": [s for s in universe if first[s] is not None]}


def eligible(d, s, i):
    """An ETF is eligible at index i after MIN_BARS bars, with a price today and no gaps needed for the signals."""
    f = d["first"][s]
    return f is not None and i - f + 1 >= MIN_BARS and d["close"][s][i] is not None


def month_end_indices(days):
    return [i for i in range(len(days)) if i + 1 == len(days) or days[i + 1].month != days[i].month]


def sigma(d, s, i):
    c = d["close"][s]
    rets = [c[k] / c[k - 1] - 1 for k in range(i - VOL_N + 1, i + 1)]
    m = sum(rets) / VOL_N
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (VOL_N - 1)) * math.sqrt(252)


def base_weights(d, i, weighting):
    el = [s for s in d["universe"] if eligible(d, s, i)]
    if not el:
        return {}
    if weighting == "equal":
        return {s: 1 / len(el) for s in el}
    inv = {s: 1 / max(sigma(d, s, i), 1e-9) for s in el}
    tot = sum(inv.values())
    return {s: v / tot for s, v in inv.items()}


def targets(d, i, lookback, weighting, trend=True):
    """Target weights decided at the close of index i, using data up to i only."""
    base = base_weights(d, i, weighting)
    if not trend:
        return base
    out = {}
    for s, w in base.items():
        prior = d["close"][s][i - lookback]
        if prior is not None and d["close"][s][i] / prior - 1 > 0:
            out[s] = w
    return out


def simulate(d, start, end, lookback=252, weighting="invvol", trend=True, cost=COST, spy_only=False):
    """Daily returns list (one per day from the first trade day) and per-rebalance turnover list."""
    days = d["days"]
    i0 = next(i for i, x in enumerate(days) if x >= start)
    i1 = max(i for i, x in enumerate(days) if x <= end)
    rebal = {i0} | {i for i in month_end_indices(days) if i0 < i < i1}
    shares, cash = {}, 1.0
    equity_prev, rets, turns = 1.0, [], []
    pending = None
    for i in range(i0, i1 + 1):
        if pending is not None:
            eq_open = cash + sum(sh * d["open"][s][i] for s, sh in shares.items())
            tgt_val = {s: w * eq_open for s, w in pending.items()}
            cur_val = {s: sh * d["open"][s][i] for s, sh in shares.items()}
            traded = sum(abs(tgt_val.get(s, 0.0) - cur_val.get(s, 0.0)) for s in set(tgt_val) | set(cur_val))
            eq_after = eq_open - cost * traded
            shares = {s: w * eq_after / d["open"][s][i] for s, w in pending.items()}
            cash = eq_after - sum(w * eq_after for w in pending.values())
            turns.append(traded / eq_open)
            pending = None
        eq = cash + sum(sh * d["close"][s][i] for s, sh in shares.items())
        if i > i0 or shares:
            rets.append(eq / equity_prev - 1)
        elif i == i0:
            rets.append(0.0)
        equity_prev = eq
        if i in rebal and i < i1:
            pending = {"SPY": 1.0} if spy_only else targets(d, i, lookback, weighting, trend)
            if spy_only and i != i0:
                pending = None
    return rets, turns


def stats(rets, turns, years_span):
    n = len(rets)
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (n - 1))
    cagr = eq ** (1 / years_span) - 1 if eq > 0 else -1.0
    return {"cagr": round(cagr, 4), "vol": round(sd * math.sqrt(252), 4), "sharpe": round(m / sd * math.sqrt(252), 3) if sd > 0 else None,
            "max_drawdown": round(dd, 4), "calmar": round(cagr / dd, 3) if dd > 0 else None,
            "annual_turnover": round(sum(turns) / years_span, 2), "end_equity": round(eq, 4)}


def worst_year(rets, days_slice):
    by, cur = {}, 1.0
    for r, dt in zip(rets, days_slice):
        by[dt.year] = by.get(dt.year, 1.0) * (1 + r)
    return round(min(by.values()) - 1, 4)


def sharpe_from_sums(s1, s2, n):
    m = s1 / n
    var = (s2 - n * m * m) / (n - 1)
    return m / math.sqrt(var) * math.sqrt(252) if var > 0 else 0.0


def boot_sharpe_diff(a, b, resamples=5000, block=21, seed=7):
    """95% interval for Sharpe(a) - Sharpe(b); circular block bootstrap on paired days."""
    n = len(a)
    pa = [0.0]
    pb = [0.0]
    qa = [0.0]
    qb = [0.0]
    for x, y in zip(a + a, b + b):
        pa.append(pa[-1] + x)
        qa.append(qa[-1] + x * x)
        pb.append(pb[-1] + y)
        qb.append(qb[-1] + y * y)
    rng = random.Random(seed)
    diffs = []
    nb = -(-n // block)
    for _ in range(resamples):
        sa = sa2 = sb = sb2 = 0.0
        cnt = 0
        for _k in range(nb):
            st = rng.randrange(n)
            ln = min(block, n - cnt)
            sa += pa[st + ln] - pa[st]
            sa2 += qa[st + ln] - qa[st]
            sb += pb[st + ln] - pb[st]
            sb2 += qb[st + ln] - qb[st]
            cnt += ln
        diffs.append(sharpe_from_sums(sa, sa2, cnt) - sharpe_from_sums(sb, sb2, cnt))
    diffs.sort()
    return [round(diffs[int(0.025 * resamples)], 3), round(diffs[int(0.975 * resamples) - 1], 3)]


def verdict(res):
    """Apply the frozen pass bar P1 to P5. res: dict with per-window V0, SPY stats at 0.05% and 0.10% and the A interval."""
    a, b = res["A"], res["B"]
    p = {"P1": a["V0"]["sharpe"] > a["SPY"]["sharpe"] and b["V0"]["sharpe"] > b["SPY"]["sharpe"],
         "P2": a["V0"]["max_drawdown"] <= 0.25 and b["V0"]["max_drawdown"] <= 0.25,
         "P3": a["V0_c10"]["sharpe"] > a["SPY"]["sharpe"] and b["V0_c10"]["sharpe"] > b["SPY"]["sharpe"],
         "P4": a["boot_V0_minus_C0"][0] > 0,
         "P5": b["V0"]["cagr"] >= b["SPY"]["cagr"] - 0.02}
    if all(p.values()):
        v = "PASS"
    elif p["P1"] and p["P2"] and p["P3"] and p["P5"]:
        v = "INCONCLUSIVE"
    else:
        v = "REJECTED"
    return v, p


def run(path, commit="unknown"):
    d = load(path)
    out = {"spec": "S-037", "data_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "code_commit": commit,
           "universe": d["universe"], "cost_per_side": COST, "results": {}}
    days = d["days"]
    for wname, (st, en) in WINDOWS.items():
        i0 = next(i for i, x in enumerate(days) if x >= st)
        i1 = max(i for i, x in enumerate(days) if x <= en)
        yrs = (days[i1] - days[i0]).days / 365.25
        sl = days[i0:i1 + 1]
        series, rows = {}, {}
        runs = {"SPY": dict(spy_only=True), "C0": dict(trend=False), "C0_c10": dict(trend=False, cost=0.001), "C0_c20": dict(trend=False, cost=0.002),
                "V0_c10": dict(cost=0.001), "V0_c20": dict(cost=0.002), "V0_c0": dict(cost=0.0), "C0_c0": dict(trend=False, cost=0.0)}
        for vn, kw in VARIANTS.items():
            runs[vn] = dict(kw)
        runs["V0"] = dict(VARIANTS["V0"])
        runs["V0_c10"].update(VARIANTS["V0"])
        runs["V0_c20"].update(VARIANTS["V0"])
        runs["V0_c0"].update(VARIANTS["V0"])
        for name, kw in runs.items():
            r, t = simulate(d, st, en, **kw)
            series[name] = r
            rows[name] = stats(r, t, yrs)
            rows[name]["worst_year"] = worst_year(r, sl[-len(r):])
        rows["V0"]["cost_drag_cagr"] = round(rows["V0_c0"]["cagr"] - rows["V0"]["cagr"], 4)
        rows["C0"]["cost_drag_cagr"] = round(rows["C0_c0"]["cagr"] - rows["C0"]["cagr"], 4)
        rows["boot_V0_minus_C0"] = boot_sharpe_diff(series["V0"], series["C0"])
        rows["boot_V0_minus_SPY"] = boot_sharpe_diff(series["V0"], series["SPY"])
        out["results"][wname] = rows
    v, p = verdict(out["results"])
    out["verdict"], out["pass_bar"] = v, p
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--daily", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--commit", default="unknown")
    a = ap.parse_args(argv)
    res = run(a.daily, a.commit)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({"verdict": res["verdict"], "pass_bar": res["pass_bar"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
