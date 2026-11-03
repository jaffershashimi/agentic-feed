"""Daily-bar backtest of the R2 idea (data class REPLAY-DAILY, stock only). Standard library only, offline, no broker,
no orders. TEST ONLY research tool: never writes the paper ledger, never pooled with PAPER or REPLAY.

Why: the 5-minute R2 engine needs intraday bars, so it can only replay a few sessions at a time. This tool runs a
daily-bar version of the same rules over years of history to ask one question fast: does the R2 idea show any edge
at all? Its rules (RD1, below) are a stand-in for R2, not R2 itself, so its numbers never count toward the go-live
gate.

RD1 rules (daily stand-ins for R2; same names so results line up with score_parts):
- Signal after the close of day S, entry at the open of the next session E. Only bars through S are used.
- Trigger (required): close(S) above the highest high of the 4 sessions before S (daily BREAK_1H_HIGH).
- F1 ADV20 (20 sessions through S) at least 1,000,000 shares. F2 close(S) at least $10.
- F3 close(S) above the 200-session SMA (needs 221 closes through S).
- F4 room: at least 2% from P = close(S) to the next resistance R = the lowest level above P among the high of the
  session before S and every confirmed swing high (above the 2 highs on each side) in the 60 sessions before S.
- F5 not extended: z = (P - EMA20) / ATR14 between -0.5 and +2.5 (daily stand-in for the VWAP drift band).
- F6 earnings: if --events lists earnings, no signal on a day the symbol reports after that close.
- Score (entry needs 6+): context (+1 close above EMA20, +1 SMA200 rising over 20 sessions); trigger (1, +1 if
  close above EMA21 and volume(S) at least ADV20); liquidity (2 at ADV20 5M+, 1 at 1M+); room ((R-P)/(P-I) with
  I = lowest low of the last 4 sessions: 2 at 2.0+ or no R, 1 at 1.0+); risk (0 if earnings in E..E+2 sessions,
  1 if a macro date is in it, else 2; without earnings data risk is 1 and the summary says EARNINGS_UNKNOWN).
- Size $500 at score 6 to 8, $750 at 9 or 10, per symbol, one open position per symbol, not compounded.
- Fill: open(E) x 1.0005. Stop fill x 0.80 rounded up to the cent; target fill x 1.30. No fees (stock).
- Exits, checked each session from E on, first that triggers: open at or below the stop (gap) exits at that open;
  low at or below the stop exits at the stop; high at or above the target exits at the target (stop first when both
  print in one session); after the close, close below EMA20 (stand-in for the 4h EMA20 rule) or the 5th session held
  exits at the next open. Every exit price x 0.9995. No next session = UNRESOLVED, excluded from the stats.
- Not modeled: the fading-momentum rule, intraday VWAP, option arms, p2 portfolio caps across symbols.

    python3 -I -m consumer.replay_daily --daily daily.csv [--start 2023-11-01] [--end 2026-10-30]
        [--events events.json] [--out DIR]

daily.csv columns: symbol, begins_at, open, high, low, close, volume[, interpolated]. Writes trades_daily.csv and
summary_daily.json: trades, net expectancy per trade with a seeded bootstrap 95% interval, profit factor, max
drawdown, worst trade, exit reasons, per-year and first-half/second-half splits (the same answer in both halves is
the first sign an edge is not a fluke).
"""
import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

RULES = "RD1"
DATA_CLASS = "REPLAY-DAILY"
EPS = 1e-9
CRITERIA = ("context", "trigger", "liquidity", "room", "risk")
TRADE_FIELDS = ("trade_id", "symbol", "signal_day", "entry_day", "exit_day", "score", "size", "fill", "shares",
                "stop", "target", "exit_price", "exit_reason", "pnl", "context", "trigger", "liquidity", "room", "risk")


def load_daily(path):
    out = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("interpolated") or "").strip().lower() in ("true", "1", "yes"):
                continue
            out[r["symbol"].strip()].append({"day": date.fromisoformat(r["begins_at"][:10]), "o": float(r["open"]),
                                             "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]),
                                             "v": float(r["volume"])})
    return {s: sorted(ds, key=lambda d: d["day"]) for s, ds in out.items()}


def load_events(path):
    if not path:
        return None, set()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    earnings = defaultdict(dict)
    for e in data.get("earnings", []):
        earnings[e["symbol"]][date.fromisoformat(e["date"])] = e.get("timing", "pm")
    return earnings, {date.fromisoformat(d) for d in data.get("macro", [])}


def ema(values, n):
    out, alpha = [], 2 / (n + 1)
    for x in values:
        out.append(x if not out else out[-1] + alpha * (x - out[-1]))
    return out


def atr(bars, i, n=14):
    """Simple mean of the n true ranges ending at bar i, or None."""
    if i < n:
        return None
    return sum(max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"]))
               for a, b in zip(bars[i - n:i], bars[i - n + 1:i + 1])) / n


def resistance(prior, price):
    """Lowest level above price: the last prior high and confirmed swing highs of the last 60 prior sessions."""
    levels = [prior[-1]["h"]] if prior else []
    window = prior[-60:]
    for i in range(2, len(window) - 2):
        h = window[i]["h"]
        if all(h > window[j]["h"] for j in (i - 2, i - 1, i + 1, i + 2)):
            levels.append(h)
    above = [x for x in levels if x > price + EPS]
    return min(above) if above else None


def evaluate(bars, s, closes, e20, e21, earnings, macro):
    """Signal check after the close of bar s. Returns (score, parts, failed) or None without a trigger."""
    if s < 220 or bars[s]["c"] <= max(b["h"] for b in bars[s - 4:s]) + EPS:
        return None
    b, p = bars[s], bars[s]["c"]
    adv20 = sum(x["v"] for x in bars[s - 19:s + 1]) / 20
    sma200 = sum(closes[s - 199:s + 1]) / 200
    sma200_back = sum(closes[s - 219:s - 19]) / 200
    r = resistance(bars[:s], p)
    a = atr(bars, s)
    failed = []
    if adv20 < 1_000_000:
        failed.append("F1_LIQUIDITY")
    if p < 10:
        failed.append("F2_PRICE")
    if p <= sma200 + EPS:
        failed.append("F3_TREND")
    if r is not None and (r - p) / p < 0.02 - EPS:
        failed.append("F4_ROOM")
    z = (p - e20[s]) / a if a else None
    if z is None or not -0.5 - EPS <= z <= 2.5 + EPS:
        failed.append("F5_EXTENDED")
    if earnings is not None and earnings.get(b["day"]) == "pm":
        failed.append("F6_EARNINGS_DAY")
    inv = min(x["l"] for x in bars[s - 3:s + 1])
    ratio = None if r is None or p - inv <= EPS else (r - p) / (p - inv)
    window = [x["day"] for x in bars[s + 1:s + 4]]
    if earnings is None:
        risk = 1
    elif any(d in earnings for d in window):
        risk = 0
    else:
        risk = 1 if any(d in macro for d in window) else 2
    parts = {"context": (p > e20[s] + EPS) + (sma200 > sma200_back + EPS),
             "trigger": 1 + (p > e21[s] + EPS and b["v"] >= adv20),
             "liquidity": 2 if adv20 >= 5_000_000 else 1 if adv20 >= 1_000_000 else 0,
             "room": 2 if r is None or (ratio is not None and ratio >= 2.0 - EPS) else 1 if ratio is not None and ratio >= 1.0 - EPS else 0,
             "risk": risk}
    return sum(parts.values()), parts, failed


def _up_cent(x):
    return math.ceil(round(x * 100, 6)) / 100


def run_exit(bars, e, fill, stop, target, e20):
    """Walk sessions from entry bar e. Returns (exit_index, price, reason) or (None, None, 'UNRESOLVED')."""
    for j in range(e, len(bars)):
        b = bars[j]
        if j > e and b["o"] <= stop + EPS:
            return j, b["o"] * 0.9995, "EXIT_STOP_GAP"
        if b["l"] <= stop + EPS:
            return j, stop * 0.9995, "EXIT_STOP_20PCT"
        if b["h"] >= target - EPS:
            return j, target * 0.9995, "EXIT_TARGET_30PCT"
        reason = "EXIT_CLOSE_UNDER_EMA20" if b["c"] < e20[j] - EPS else "EXIT_TIME_STOP_5D" if j - e + 1 >= 5 else None
        if reason:
            if j + 1 >= len(bars):
                return None, None, "UNRESOLVED"
            return j + 1, bars[j + 1]["o"] * 0.9995, reason
    return None, None, "UNRESOLVED"


def backtest(daily, start=None, end=None, earnings=None, macro=frozenset()):
    """All RD1 trades across symbols, in signal order. Same inputs always give the same output."""
    trades, skips = [], defaultdict(int)
    for sym in sorted(daily):
        bars = daily[sym]
        closes = [b["c"] for b in bars]
        e20, e21 = ema(closes, 20), ema(closes, 21)
        sym_earn = None if earnings is None else earnings.get(sym, {})
        free_from = 0
        for s in range(len(bars) - 1):
            e = s + 1
            if e < free_from or (start and bars[e]["day"] < start) or (end and bars[e]["day"] > end):
                continue
            got = evaluate(bars, s, closes, e20, e21, sym_earn, macro)
            if got is None:
                continue
            score, parts, failed = got
            for f in failed:
                skips[f] += 1
            if failed:
                continue
            if score < 6:
                skips["SCORE_UNDER_6"] += 1
                continue
            size = 750 if score >= 9 else 500
            fill = bars[e]["o"] * 1.0005
            stop, target = _up_cent(fill * 0.80), fill * 1.30
            x, price, reason = run_exit(bars, e, fill, stop, target, e20)
            shares = size / fill
            t = {"trade_id": f"RD1-{sym}-{bars[e]['day']:%Y%m%d}", "symbol": sym, "signal_day": bars[s]["day"].isoformat(),
                 "entry_day": bars[e]["day"].isoformat(), "exit_day": bars[x]["day"].isoformat() if x is not None else "",
                 "score": score, "size": size, "fill": round(fill, 4), "shares": round(shares, 6), "stop": stop,
                 "target": round(target, 4), "exit_price": round(price, 4) if price is not None else "",
                 "exit_reason": reason, "pnl": round(shares * (price - fill), 2) if price is not None else "", **parts}
            trades.append(t)
            free_from = x + 1 if x is not None else len(bars)
    return trades, dict(sorted(skips.items()))


def _boot_ci(pnls, n_boot=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(pnls) for _ in pnls) / len(pnls) for _ in range(n_boot))
    return round(means[int(0.025 * n_boot)], 2), round(means[int(0.975 * n_boot) - 1], 2)


def stats(trades):
    closed = sorted((t for t in trades if t["exit_reason"] != "UNRESOLVED"), key=lambda t: (t["exit_day"], t["trade_id"]))
    pnls = [t["pnl"] for t in closed]
    out = {"trades": len(closed), "unresolved": len(trades) - len(closed)}
    if not pnls:
        return out
    wins, losses = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
    peak = eq = dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    out.update({"net_pnl": round(sum(pnls), 2), "expectancy": round(sum(pnls) / len(pnls), 2),
                "expectancy_ci95": list(_boot_ci(pnls)) if len(pnls) >= 2 else None,
                "profit_factor": round(wins / losses, 2) if losses else None, "max_drawdown": round(dd, 2),
                "worst_trade": round(min(pnls), 2), "win_share": round(sum(p > 0 for p in pnls) / len(pnls), 3),
                "exit_reasons": dict(sorted(defaultdict(int, {r: sum(t["exit_reason"] == r for t in closed)
                                                               for r in {t["exit_reason"] for t in closed}}).items()))})
    return out


def summary(trades, skips, earnings_known):
    closed = sorted((t for t in trades if t["exit_reason"] != "UNRESOLVED"), key=lambda t: t["entry_day"])
    years = defaultdict(list)
    for t in closed:
        years[t["entry_day"][:4]].append(t)
    half = len(closed) // 2
    flags = [] if earnings_known else ["EARNINGS_UNKNOWN"]
    if len(closed) < 30:
        flags.append("UNDER_30_TRADES")
    return {"data_class": DATA_CLASS, "rules": RULES, "note": "TEST ONLY. Daily stand-in for R2; never counts toward the go-live gate.",
            "flags": flags, "all": stats(trades), "first_half": stats(closed[:half]), "second_half": stats(closed[half:]),
            "by_year": {y: stats(ts) for y, ts in sorted(years.items())}, "skips": skips}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--daily", required=True)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--events")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    earnings, macro = load_events(a.events)
    trades, skips = backtest(load_daily(a.daily), a.start, a.end, earnings, macro)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "trades_daily.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
        w.writeheader()
        w.writerows(trades)
    s = summary(trades, skips, earnings is not None)
    (out / "summary_daily.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
