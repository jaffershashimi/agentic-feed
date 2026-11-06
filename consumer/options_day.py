"""OD1: premarket-confirmation intraday option replay on real option bars (TEST ONLY, data class REPLAY-OPTIONS).

Standard library only, offline, no broker, no orders, never writes the paper ledger, never pooled with PAPER or
other REPLAY classes. Answers one question: does buying a near-the-money call (or put) after a confirmed premarket
move, exited the same day by target, stop or time, make money after realistic fills?

OD1 rules (frozen; a change needs a new id). Times are America/Los_Angeles.
- Prior close: close of the last regular-session 5-minute bar of the prior session.
- Premarket move: last premarket bar that begins 05:00 to 06:25, its close vs the prior close. Bull if +1.0% or more,
  bear if -1.0% or less, else no trade.
- Confirmation: the 06:30, 06:35 and 06:40 regular bars. Bull: close(06:40) above open(06:30) and at or above the
  premarket close. Bear: the mirror. Otherwise no trade.
- Contract: calls take the lowest strike above spot (spot = close 06:40); puts the highest strike below spot. Expiry:
  the nearest listed expiry after the trade day (never 0DTE). Chosen by the data step from listed contracts.
- Entry: the option bar that begins 06:45, at its open plus half-spread H(p) = max(0.03, 0.015 x p).
- Exits on later bars, first that triggers (stop checked before target in one bar): open at/through a level exits at
  that open; else low at/below the stop or high at/above the target exits at the level. A bar that begins 12:30
  exits at its open. Hourly variant (--entry 07:00 --time-exit 12:00): Robinhood keeps real 5-minute option bars
  for only about 8 sessions, hourly bars for months; with hourly bars a bar touching both levels counts as the stop. Every exit pays the half-spread. Fees $0.05 per contract per side.
- Grid: targets +20%, +30%, +50% x stops -15%, -25%, -35% (9 tests, Holm-adjusted, judged per test).
- $1,000 account view: a trade counts only if one contract costs at most half the account.

Inputs (CSV, from the data step):
- underlying.csv: symbol, begins_at (UTC), open, high, low, close, session ('pre', 'reg', 'post'), extended bars.
- options.csv: day, symbol, side ('call'/'put'), occ, strike, expiry, begins_at (UTC), open, high, low, close.

    python3 -m consumer.options_day signals --underlying underlying.csv      (prints the days that need a contract)
    python3 -m consumer.options_day replay --underlying underlying.csv --options options.csv [--out DIR]
"""
import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
EPS = 1e-9
GAP = 0.01
FEE = 0.05
TARGETS = (0.20, 0.30, 0.50)
STOPS = (-0.15, -0.25, -0.35)
TIME_EXIT = time(12, 30)


def _pt(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PT)


def half_spread(p):
    return max(0.03, 0.015 * p)


def load_underlying(path):
    out = defaultdict(lambda: defaultdict(list))
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = _pt(r["begins_at"])
            out[r["symbol"]][t.date()].append({"t": t.time(), "o": float(r["open"]), "h": float(r["high"]),
                                                "l": float(r["low"]), "c": float(r["close"]),
                                                "s": (r.get("session") or "reg").strip()})
    return {s: {d: sorted(bs, key=lambda b: b["t"]) for d, bs in days.items()} for s, days in out.items()}


def signals(under):
    """One record per symbol-day that passes OD1's premarket and confirmation rules."""
    out = []
    for sym in sorted(under):
        days = sorted(under[sym])
        for prev, day in zip(days, days[1:]):
            reg_prev = [b for b in under[sym][prev] if b["s"] == "reg"]
            bars = under[sym][day]
            pre = [b for b in bars if b["s"] == "pre" and time(5, 0) <= b["t"] <= time(6, 25)]
            first = {b["t"]: b for b in bars if b["s"] == "reg"}
            if not reg_prev or not pre or not all(time(6, m) in first for m in (30, 35, 40)):
                continue
            pc, pm = reg_prev[-1]["c"], pre[-1]["c"]
            move = pm / pc - 1
            o, c = first[time(6, 30)]["o"], first[time(6, 40)]["c"]
            if move >= GAP - EPS and c > o + EPS and c >= pm - EPS:
                side = "call"
            elif move <= -GAP + EPS and c < o - EPS and c <= pm + EPS:
                side = "put"
            else:
                continue
            out.append({"day": day.isoformat(), "symbol": sym, "side": side, "spot": round(c, 4),
                        "prior_close": pc, "premarket_move": round(move, 5)})
    return out


def load_options(path):
    out = defaultdict(list)
    meta = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            key = (r["day"], r["symbol"], r["side"])
            meta[key] = {"occ": r["occ"], "strike": float(r["strike"]), "expiry": r["expiry"]}
            out[key].append({"t": _pt(r["begins_at"]).time(), "o": float(r["open"]), "h": float(r["high"]),
                             "l": float(r["low"]), "c": float(r["close"])})
    return {k: sorted(v, key=lambda b: b["t"]) for k, v in out.items()}, meta


def simulate(bars, target, stop, entry=time(6, 45), time_exit=TIME_EXIT):
    """Entry at the open of the bar that begins at `entry` (06:45 for 5-minute bars, 07:00 for hourly). Returns (entry, exit, reason) per contract share, or None without an entry bar."""
    entry_bar = next((i for i, b in enumerate(bars) if b["t"] == entry), None)
    if entry_bar is None:
        return None
    o = bars[entry_bar]["o"]
    fill = o + half_spread(o)
    tp, sl = fill * (1 + target), fill * (1 + stop)
    for b in bars[entry_bar + 1:]:
        if b["t"] >= time_exit:
            return fill, b["o"], "EXIT_TIME"
        if b["o"] <= sl + EPS:
            return fill, b["o"], "EXIT_STOP_GAP"
        if b["o"] >= tp - EPS:
            return fill, b["o"], "EXIT_TARGET_GAP"
        if b["l"] <= sl + EPS:
            return fill, sl, "EXIT_STOP"
        if b["h"] >= tp - EPS:
            return fill, tp, "EXIT_TARGET"
    last = bars[-1]
    return fill, last["c"], "EXIT_LAST_BAR"


def _boot(xs, n=2000, seed=11):
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    p = sum(m <= 0 for m in means) / n
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)], p


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = round(run, 4)
    return adj


def replay(sigs, opt_bars, opt_meta, account=1000.0, entry=time(6, 45), time_exit=TIME_EXIT):
    tests, rows = [], []
    for tg in TARGETS:
        for st in STOPS:
            trades = []
            for s in sigs:
                key = (s["day"], s["symbol"], s["side"])
                if key not in opt_bars:
                    continue
                got = simulate(opt_bars[key], tg, st, entry, time_exit)
                if got is None:
                    continue
                fill, px, reason = got
                exit_px = max(px - half_spread(px), 0.0)
                pnl = (exit_px - fill) * 100 - 2 * FEE
                trades.append({"test": f"T{int(tg * 100)}_S{int(-st * 100)}", **s, **opt_meta[key],
                               "fill": round(fill, 4), "exit": round(exit_px, 4), "reason": reason,
                               "ret": round(pnl / (fill * 100), 5), "pnl_1ct": round(pnl, 2),
                               "affordable_1k": fill * 100 <= account / 2})
            rows += trades
            rs = [t["ret"] for t in trades]
            st_out = {"test": f"T{int(tg * 100)}_S{int(-st * 100)}", "target": tg, "stop": st, "trades": len(rs)}
            if rs:
                ci, p = _boot(rs) if len(rs) >= 2 else (None, 1.0)
                wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
                aff = [t for t in trades if t["affordable_1k"]]
                st_out.update({"mean_ret": round(sum(rs) / len(rs), 4), "mean_ret_ci95": ci, "p_mean_le_0": p,
                               "win_share": round(sum(r > 0 for r in rs) / len(rs), 3),
                               "profit_factor": round(wins / losses, 2) if losses > EPS else None,
                               "pnl_1ct_total": round(sum(t["pnl_1ct"] for t in trades), 2),
                               "affordable_1k_trades": len(aff),
                               "affordable_1k_pnl": round(sum(t["pnl_1ct"] for t in aff), 2)})
            else:
                st_out["p_mean_le_0"] = 1.0
            tests.append(st_out)
    for t, a in zip(tests, holm([t["p_mean_le_0"] for t in tests])):
        t["p_holm"] = a
        t["verdict"] = ("insufficient_evidence" if t["trades"] < 100 else
                        "edge" if a < 0.05 and (t.get("profit_factor") or 0) >= 1.2 else "no_edge")
    return tests, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("signals", "replay"))
    ap.add_argument("--underlying", required=True)
    ap.add_argument("--options")
    ap.add_argument("--entry", default="06:45", help="entry bar start, PT (07:00 for hourly bars)")
    ap.add_argument("--time-exit", default="12:30", help="exit at the open of the bar starting at or after this, PT")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    entry, texit = time.fromisoformat(a.entry), time.fromisoformat(a.time_exit)
    sigs = signals(load_underlying(a.underlying))
    if a.mode == "signals":
        print(json.dumps(sigs, indent=1))
        return 0
    if not a.options:
        print(json.dumps({"errors": ["--options is required for replay"]}))
        return 2
    bars, meta = load_options(a.options)
    tests, rows = replay(sigs, bars, meta, entry=entry, time_exit=texit)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if rows:
        with open(out / "trades_od1.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    s = {"data_class": "REPLAY-OPTIONS", "rules": "OD1", "entry": a.entry, "time_exit": a.time_exit, "signals": len(sigs),
         "signals_with_option_bars": sum((x["day"], x["symbol"], x["side"]) in bars for x in sigs),
         "note": "TEST ONLY. Real option 5-minute bars (trade or mark prices, no quotes); fills pay a modeled half-spread. "
                 "Never pooled; never counts toward go-live.", "tests": tests}
    (out / "summary_od1.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
