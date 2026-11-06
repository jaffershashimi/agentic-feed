"""Strategy lab: daily-bar portfolio backtests of several strategy families against buy-and-hold (TEST ONLY).

Standard library only, offline, no broker, no orders. Never writes the paper ledger. Data classes: REPLAY-DAILY for
stock strategies, SYNTHETIC for the option proxy. Never pooled with PAPER or REPLAY, never counted toward go-live.

Every strategy starts with the same cash (default $1,000, the planned real start), compounds, pays costs on every
fill (default 0.05% per side) and is judged against buy-and-hold of the benchmark (default SPY) on the same dates.

Strategies (rules frozen here; change them only with a new id):
- BH    Buy and hold the benchmark from the first open.
- TF1   Trend following on a small ETF set (default SPY, QQQ, TLT, GLD, DBC). At each week's last close, hold every
        symbol whose close is above its 200-session SMA, weighted by inverse 20-session volatility (weights sum to 1
        across held symbols); the rest is cash. Rebalance at the next open.
- MR1   Relative strength rotation. At each week's last close, rank symbols above their 200-session SMA by the return
        from 126 sessions ago to 5 sessions ago. Hold the top 3 equal weight; a holding stays while it ranks in the
        top 6 and stays above its SMA200. Rebalance at the next open.
- PB1   Pullback in an uptrend. After a close above the SMA200 with 2-period RSI (Wilder) below 10, buy at the next
        open (lowest RSI first, at most 3 positions, each a third of equity at that close). Exit at the next open
        after a close above the 5-session SMA or after 10 sessions held; a stop 8% under the fill exits during the
        session (at the open if it gaps below).
- PB1I  Same as PB1 with internal bar strength (close - low) / (high - low) below 0.2 instead of RSI.
- OPT1  SYNTHETIC option proxy on PB1 signals: buy a 35-day at-the-money call priced by Black-Scholes with implied
        vol = 1.2 x the 20-session realized vol (floor 15%), 3% worse than model on entry and exit, 10% of equity per
        position, held until the PB1 exit. A rough screen only: real option quotes, skew and spreads differ.
--market SYMBOL gates new entries (MR1, PB1, PB1I, OPT1) on that symbol closing above its 200-session SMA ("+MKT").

    python3 -I -m consumer.strategy_lab --daily daily.csv [--start 2023-11-01] [--end 2026-11-06]
        [--benchmark SPY] [--market SPY] [--cash 1000] [--judge-from 2025-05-01] [--out DIR]

Judge split: results are reported for the build period and the judge period (default: the last 548 calendar days).
Pass bar (judge period only): 100+ closed trades for PB-type, profit factor 1.2+ after costs, mean trade interval
above zero, max drawdown no worse than 25%, and a Sharpe ratio above buy-and-hold's. TF1 and MR1 trade weekly, so
they rarely reach 100 trades; their verdict says so instead of passing.
"""
import argparse
import csv
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from consumer.replay_daily import load_daily

EPS = 1e-9
COST = 0.0005
TF_UNIVERSE = ("SPY", "QQQ", "TLT", "GLD", "DBC")
STRATEGIES = ("BH", "TF1", "MR1", "PB1", "PB1I", "OPT1")
TRADE_FIELDS = ("strategy", "symbol", "entry_day", "exit_day", "entry", "exit", "ret", "pnl", "reason")


# ---------- indicators ----------

def sma_at(closes, i, n):
    return sum(closes[i - n + 1:i + 1]) / n if i >= n - 1 else None


def rsi_series(closes, n=2):
    """Wilder RSI; None until n changes exist."""
    out, gain, loss = [None] * len(closes), 0.0, 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            gain += g / n
            loss += l / n
            if i < n:
                continue
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
        out[i] = 100.0 if loss <= EPS else 100 - 100 / (1 + gain / loss)
    return out


def realized_vol(closes, i, n=20):
    if i < n:
        return None
    rets = [math.log(closes[k] / closes[k - 1]) for k in range(i - n + 1, i + 1)]
    m = sum(rets) / n
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (n - 1)) * math.sqrt(252)


def bs_call(s, k, t, sigma, r=0.04):
    if t <= EPS:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + (r + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    nd = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return s * nd(d1) - k * math.exp(-r * t) * nd(d2)


# ---------- shared frame ----------

class Frame:
    """Bars aligned to the benchmark's calendar. A symbol missing a day carries its last close (no trading that day)."""

    def __init__(self, daily, benchmark):
        if benchmark not in daily:
            raise ValueError(f"benchmark {benchmark} not in daily bars")
        self.days = [b["day"] for b in daily[benchmark]]
        self.idx = {d: i for i, d in enumerate(self.days)}
        self.syms = sorted(daily)
        self.bar, self.close = {}, {}
        for s in self.syms:
            by_day = {b["day"]: b for b in daily[s]}
            bars, last = [], None
            for d in self.days:
                b = by_day.get(d)
                if b is not None:
                    last = b
                bars.append(b)
            self.bar[s] = bars
            closes, lc = [], None
            for b in bars:
                lc = b["c"] if b else lc
                closes.append(lc)
            self.close[s] = closes
        self.rsi = {s: rsi_series([c or 0.0 for c in self.close[s]]) for s in self.syms}

    def ok(self, s, i):
        return self.bar[s][i] is not None

    def above_sma200(self, s, i):
        c = self.close[s]
        if i < 199 or any(x is None for x in c[i - 199:i + 1]):
            return False
        return c[i] > sum(c[i - 199:i + 1]) / 200 + EPS

    def week_end(self, i):
        return i + 1 >= len(self.days) or self.days[i + 1].isocalendar()[:2] != self.days[i].isocalendar()[:2]


# ---------- simulators ----------

def _equity(cash, pos, f, i):
    return cash + sum(sh * f.close[s][i] for s, sh in pos.items())


def weight_sim(f, name, targets_fn, cash0, lo, hi, gate=None):
    """Rebalance to target weights at the next open after each week's last close (BH: once)."""
    cash, pos, open_t, trades, curve = cash0, {}, {}, [], []
    pending = None
    for i in range(lo, hi + 1):
        if pending is not None:
            eq = _equity(cash, pos, f, i - 1)
            for s in sorted(set(pos) | set(pending)):
                if not f.ok(s, i):
                    continue
                px = f.bar[s][i]["o"]
                want = eq * pending.get(s, 0.0) / px
                have = pos.get(s, 0.0)
                d = want - have
                if abs(d) * px < 0.01:
                    continue
                cash -= d * px + abs(d) * px * COST
                pos[s] = want
                if have <= EPS < want:
                    open_t[s] = (f.days[i], px)
                if want <= EPS < have:
                    d0, p0 = open_t.pop(s)
                    trades.append({"strategy": name, "symbol": s, "entry_day": d0.isoformat(), "exit_day": f.days[i].isoformat(),
                                   "entry": round(p0, 4), "exit": round(px, 4), "ret": round(px / p0 * (1 - COST) ** 2 - 1, 5),
                                   "pnl": round(have * (px - p0), 2), "reason": "REBALANCE_OUT"})
                    del pos[s]
            pending = None
        curve.append((f.days[i], _equity(cash, pos, f, i)))
        if i < hi and (f.week_end(i) or name == "BH"):
            t = targets_fn(f, i, set(pos))
            if t is not None and (gate is None or gate(i) or not t):
                pending = t
            elif t is not None and gate is not None and not gate(i):
                pending = {s: w for s, w in t.items() if s in pos}  # gate blocks new names only
    for s, (d0, p0) in open_t.items():
        trades.append({"strategy": name, "symbol": s, "entry_day": d0.isoformat(), "exit_day": "", "entry": round(p0, 4),
                       "exit": "", "ret": "", "pnl": "", "reason": "OPEN_AT_END"})
    return trades, curve


def bh_targets(bench):
    done = []

    def fn(f, i, held):
        if done:
            return None
        done.append(1)
        return {bench: 1.0}
    return fn


def tf1_targets(universe):
    def fn(f, i, held):
        inv = {}
        for s in universe:
            if s in f.bar and f.ok(s, i) and f.above_sma200(s, i):
                v = realized_vol(f.close[s], i)
                if v and v > EPS:
                    inv[s] = 1 / v
        tot = sum(inv.values())
        return {s: w / tot for s, w in inv.items()} if tot else {}
    return fn


def mr1_targets(universe):
    def fn(f, i, held):
        ranked = []
        for s in universe:
            c = f.close[s]
            if i < 200 or not f.ok(s, i) or c[i - 126] is None or not f.above_sma200(s, i):
                continue
            ranked.append((c[i - 5] / c[i - 126] - 1, s))
        ranked = [s for _, s in sorted(ranked, reverse=True)]
        keep = [s for s in ranked[:6] if s in held]
        hold = (keep + [s for s in ranked if s not in keep])[:3]
        return {s: 1 / len(hold) for s in hold} if hold else {}
    return fn


def signal_sim(f, name, universe, cash0, lo, hi, gate=None, ibs=False, option=False):
    """PB1, PB1I and OPT1: next-open entries, up to 3 positions."""
    cash, pos, trades, curve, pending = cash0, {}, [], [], []
    exits = set()

    def close_pos(s, i, px, reason):
        nonlocal cash
        p = pos.pop(s)
        if option:
            t_left = max(35 - (i - p["i"]) * 7 / 5, 0) / 365
            val = bs_call(px, p["k"], t_left, p["sigma"]) * 0.97
            cash += p["n"] * val
            ret = val / p["prem"] - 1
            pnl = p["n"] * (val - p["prem"])
        else:
            cash += p["n"] * px * (1 - COST)
            ret = px * (1 - COST) / p["fill"] - 1
            pnl = p["n"] * (px * (1 - COST) - p["fill"])
        trades.append({"strategy": name, "symbol": s, "entry_day": f.days[p["i"]].isoformat(), "exit_day": f.days[i].isoformat(),
                       "entry": round(p["fill"] if not option else p["prem"], 4), "exit": round(px, 4), "ret": round(ret, 5),
                       "pnl": round(pnl, 2), "reason": reason})

    for i in range(lo, hi + 1):
        for s in sorted(exits):
            if s in pos and f.ok(s, i):
                close_pos(s, i, f.bar[s][i]["o"], pos[s]["why"])
        exits = set()
        eq_prev = _equity_sig(cash, pos, f, i - 1, option)
        for s in pending:
            if len(pos) >= 3 or s in pos or not f.ok(s, i):
                continue
            o = f.bar[s][i]["o"]
            if option:
                sigma = max(0.15, 1.2 * (realized_vol(f.close[s], i - 1) or 0.15))
                prem = bs_call(o, o, 35 / 365, sigma) * 1.03
                n = eq_prev * 0.10 / prem
                cash -= n * prem
                pos[s] = {"i": i, "fill": o, "n": n, "stop": None, "k": o, "sigma": sigma, "prem": prem}
            else:
                fill = o * (1 + COST)
                n = eq_prev / 3 / fill
                cash -= n * fill
                pos[s] = {"i": i, "fill": fill, "n": n, "stop": fill * 0.92}
        pending = []
        for s in sorted(pos):
            b, p = f.bar[s][i], pos[s]
            if b is None or p["stop"] is None:
                continue
            if b["o"] <= p["stop"] + EPS and i > p["i"]:
                close_pos(s, i, b["o"], "EXIT_STOP_GAP")
            elif b["l"] <= p["stop"] + EPS:
                close_pos(s, i, p["stop"], "EXIT_STOP_8PCT")
        for s, p in pos.items():
            c = f.close[s]
            s5 = sma_at(c, i, 5)
            if s5 is not None and c[i] > s5 + EPS:
                p["why"] = "EXIT_CLOSE_ABOVE_SMA5"
                exits.add(s)
            elif i - p["i"] + 1 >= 10:
                p["why"] = "EXIT_TIME_10D"
                exits.add(s)
        curve.append((f.days[i], _equity_sig(cash, pos, f, i, option)))
        if i < hi and (gate is None or gate(i)):
            cands = []
            for s in universe:
                if s in pos or not f.ok(s, i) or not f.above_sma200(s, i):
                    continue
                b = f.bar[s][i]
                if ibs:
                    rng = b["h"] - b["l"]
                    val = (b["c"] - b["l"]) / rng if rng > EPS else 0.5
                    if val < 0.2 - EPS:
                        cands.append((val, s))
                else:
                    r = f.rsi[s][i]
                    if r is not None and r < 10 - EPS:
                        cands.append((r, s))
            pending = [s for _, s in sorted(cands)]
    for s, p in pos.items():
        trades.append({"strategy": name, "symbol": s, "entry_day": f.days[p["i"]].isoformat(), "exit_day": "",
                       "entry": round(p["fill"], 4), "exit": "", "ret": "", "pnl": "", "reason": "OPEN_AT_END"})
    return trades, curve


def _equity_sig(cash, pos, f, i, option):
    tot = cash
    for s, p in pos.items():
        px = f.close[s][i]
        if option:
            t_left = max(35 - (i - p["i"]) * 7 / 5, 0) / 365
            tot += p["n"] * bs_call(px, p["k"], t_left, p["sigma"])
        else:
            tot += p["n"] * px
    return tot


# ---------- statistics ----------

def _boot_ci(xs, n_boot=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n_boot))
    return [round(means[int(0.025 * n_boot)], 5), round(means[int(0.975 * n_boot) - 1], 5)]


def curve_stats(curve):
    if len(curve) < 2:
        return {}
    eqs = [e for _, e in curve]
    rets = [b / a - 1 for a, b in zip(eqs, eqs[1:]) if a > EPS]
    peak, dd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak if peak > EPS else 0)
    years = max((curve[-1][0] - curve[0][0]).days / 365.25, 1 / 365.25)
    m = sum(rets) / len(rets) if rets else 0
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0
    total = eqs[-1] / eqs[0] - 1
    return {"start_equity": round(eqs[0], 2), "end_equity": round(eqs[-1], 2), "total_return": round(total, 4),
            "cagr": round((1 + total) ** (1 / years) - 1, 4) if total > -1 else -1.0,
            "max_drawdown": round(dd, 4), "sharpe": round(m / sd * math.sqrt(252), 2) if sd > EPS else None}


def trade_stats(trades):
    rs = [t["ret"] for t in trades if t["ret"] != ""]
    out = {"trades": len(rs), "open": sum(t["ret"] == "" for t in trades)}
    if not rs:
        return out
    wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    out.update({"mean_ret": round(sum(rs) / len(rs), 5), "mean_ret_ci95": _boot_ci(rs) if len(rs) >= 2 else None,
                "profit_factor": round(wins / losses, 2) if losses > EPS else None,
                "win_share": round(sum(r > 0 for r in rs) / len(rs), 3), "worst": round(min(rs), 4)})
    return out


def verdict(name, judge, bench_judge):
    t, c = judge["trades"], judge["curve"]
    reasons = []
    if name == "BH":
        return {"verdict": "BENCHMARK", "reasons": []}
    weekly = name in ("TF1", "MR1")
    if t.get("trades", 0) < 100:
        reasons.append(f"only {t.get('trades', 0)} closed trades (needs 100)")
    if (t.get("profit_factor") or 0) < 1.2:
        reasons.append(f"profit factor {t.get('profit_factor')} (needs 1.2)")
    ci = t.get("mean_ret_ci95")
    if not ci or ci[0] <= 0:
        reasons.append("mean trade interval not above zero")
    if c.get("max_drawdown", 1) > 0.25:
        reasons.append(f"max drawdown {c.get('max_drawdown')} (limit 0.25)")
    bs, s = bench_judge["curve"].get("sharpe"), c.get("sharpe")
    if s is None or bs is None or s <= bs:
        reasons.append(f"Sharpe {s} not above buy-and-hold {bs}")
    if not reasons:
        return {"verdict": "PASS", "reasons": []}
    if weekly and len(reasons) == 1 and reasons[0].startswith("only"):
        return {"verdict": "PASS_EXCEPT_SAMPLE", "reasons": reasons}
    return {"verdict": "FAIL", "reasons": reasons}


# ---------- runner ----------

def run_all(daily, start=None, end=None, benchmark="SPY", market=None, cash=1000.0, judge_from=None,
            tf_universe=TF_UNIVERSE):
    f = Frame(daily, benchmark)
    if market is not None and market not in daily:
        raise ValueError(f"market symbol {market} not in daily bars")
    lo = next((i for i, d in enumerate(f.days) if i >= 200 and (start is None or d >= start)), None)
    hi = max((i for i, d in enumerate(f.days) if end is None or d <= end), default=None)
    if lo is None or hi is None or hi - lo < 20:
        raise ValueError("not enough bars: need 200 sessions of history before the start and 20 after")
    judge_from = judge_from or (f.days[hi] - timedelta(days=548))
    gate = (lambda i: f.above_sma200(market, i)) if market else None
    stock_universe = [s for s in f.syms if s not in ("DBC", "TLT", "GLD")]
    tf_u = [s for s in tf_universe if s in daily]
    plan = {"BH": lambda: weight_sim(f, "BH", bh_targets(benchmark), cash, lo, hi),
            "TF1": lambda: weight_sim(f, "TF1", tf1_targets(tf_u), cash, lo, hi),
            "MR1": lambda: weight_sim(f, "MR1", mr1_targets(stock_universe), cash, lo, hi, gate),
            "PB1": lambda: signal_sim(f, "PB1", stock_universe, cash, lo, hi, gate),
            "PB1I": lambda: signal_sim(f, "PB1I", stock_universe, cash, lo, hi, gate, ibs=True),
            "OPT1": lambda: signal_sim(f, "OPT1", stock_universe, cash, lo, hi, gate, option=True)}
    results, all_trades = {}, []
    for name in STRATEGIES:
        trades, curve = plan[name]()
        all_trades += trades

        def part(cond_t, cond_c):
            sub_c = [x for x in curve if cond_c(x[0])]
            return {"trades": trade_stats([t for t in trades if t["exit_day"] and cond_t(t)]), "curve": curve_stats(sub_c)}
        jf = judge_from.isoformat()
        results[name] = {"data_class": "SYNTHETIC" if name == "OPT1" else "REPLAY-DAILY",
                         "all": {"trades": trade_stats(trades), "curve": curve_stats(curve)},
                         "build": part(lambda t: t["entry_day"] < jf, lambda d: d < judge_from),
                         "judge": part(lambda t: t["entry_day"] >= jf, lambda d: d >= judge_from)}
    for name in STRATEGIES:
        results[name]["judge_verdict"] = verdict(name, results[name]["judge"], results["BH"]["judge"])
    summary = {"note": "TEST ONLY. Never pooled with PAPER or REPLAY; never counts toward go-live. OPT1 is a SYNTHETIC "
                       "Black-Scholes proxy, not real option quotes.",
               "flags": ["SURVIVORSHIP_BIAS"], "benchmark": benchmark, "market_filter": market, "start_cash": cash,
               "cost_per_side": COST, "period": [f.days[lo].isoformat(), f.days[hi].isoformat()],
               "judge_from": judge_from.isoformat(), "tf_universe": tf_u, "stock_universe": stock_universe,
               "results": results}
    return summary, all_trades


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--daily", required=True)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--market")
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--judge-from", type=date.fromisoformat)
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    try:
        s, trades = run_all(load_daily(a.daily), a.start, a.end, a.benchmark, a.market, a.cash, a.judge_from)
    except ValueError as exc:
        print(json.dumps({"errors": [str(exc)]}))
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "trades_lab.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
        w.writeheader()
        w.writerows(trades)
    (out / "summary_lab.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    print(json.dumps({k: {"judge": v["judge_verdict"], "all": v["all"]["curve"]} for k, v in s["results"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
