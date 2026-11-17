"""S-046 study: opening-range breakout day trades on liquid US stocks, per the frozen spec in Agentic-Lab system/06-research/specs/S-046-opening-range-breakout.md.
TEST ONLY. Reads cached Alpaca 5-minute SIP bars (private), writes JSON and markdown. Places no order.

  python3 -m consumer.orb_study fetch SYMBOLS.txt CACHE_DIR            # downloads 2023-01-03 to 2026-11-13 5-minute bars per symbol
  python3 -m consumer.orb_study run SYMBOLS.txt CACHE_DIR OUT_DIR
"""
import json
import math
import sys
import time
import urllib.parse
from datetime import datetime, time as dtime, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import news_events as ne
from consumer import oversold_panel as op

ET = ZoneInfo("America/New_York")
START, END = "2023-01-03", "2026-11-13"
WINDOWS = {"Development": ("2023-01-03", "2024-12-31"), "Judge": ("2025-01-02", "2025-12-31"), "Final": ("2026-01-02", "2026-11-13")}
SLIP = 0.0002
RANGE_BARS = 3
LAST_TRIGGER_IDX = 71      # the 15:25 bar closes at 15:30
LAST_EXIT_IDX = 76         # the 15:50 bar closes at 15:55
MIN_RISK_PCT = 0.0005
VARIANTS = {"V1": {"r": 1.0, "vol": None}, "V2": {"r": 2.0, "vol": None}, "V3": {"r": 1.0, "vol": 1.0}}


def floor_cents(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))


def round_cents(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def sessions(bars):
    """{date: [78 bars]} for full regular sessions only (09:30 to 16:00 ET, 78 five-minute bars on a 5-minute grid)."""
    by = {}
    for b in bars:
        t = ne.parse_ts(b["t"]).astimezone(ET)
        mins = t.hour * 60 + t.minute - 570
        if 0 <= mins < 390 and mins % 5 == 0:
            by.setdefault(t.date().isoformat(), {})[mins // 5] = b
    return {d: [v[i] for i in range(78)] for d, v in by.items() if len(v) == 78}


def _trade(day, j, rng_hi, rng_lo, r_mult):
    """Enter at the open of bar j (plus slippage), stop at the range midpoint, target r_mult R, flat at the close of the 15:50 bar. Returns dict or None."""
    entry = float(day[j]["o"]) * (1 + SLIP)
    stop = floor_cents((rng_hi + rng_lo) / 2)
    risk = entry - stop
    if risk <= 0 or risk < MIN_RISK_PCT * entry:
        return None
    target = round_cents(entry + r_mult * risk)
    for k in range(j, LAST_EXIT_IDX + 1):
        b = day[k]
        o, h, l = float(b["o"]), float(b["h"]), float(b["l"])
        if l <= stop:  # stop first when a bar touches both; a gap through it fills at the open
            px, how = min(stop, o), "STOP"
            break
        if h >= target:
            px, how = target, "TARGET"
            break
    else:
        px, how = float(day[LAST_EXIT_IDX]["c"]), "FLAT"
    net = px * (1 - SLIP)
    return {"entry": entry, "stop": stop, "target": target, "risk": risk, "exit": net, "how": how, "r": (net - entry) / risk, "j": j}


def breakout(day, vol_min=None):
    """First bar from 09:45 to 15:25 whose close is above the range high (and, with vol_min, whose volume is at least vol_min times the range bars' average). Returns (index, hi, lo)."""
    hi = max(float(b["h"]) for b in day[:RANGE_BARS])
    lo = min(float(b["l"]) for b in day[:RANGE_BARS])
    vavg = sum(float(b["v"]) for b in day[:RANGE_BARS]) / RANGE_BARS
    for i in range(RANGE_BARS, LAST_TRIGGER_IDX + 1):
        if float(day[i]["c"]) > hi and (vol_min is None or float(day[i]["v"]) >= vol_min * vavg):
            return i, hi, lo
    return None, hi, lo


def evaluate_day(day, variant):
    v = VARIANTS[variant]
    i, hi, lo = breakout(day, v["vol"])
    if i is None:
        return None
    t = _trade(day, i + 1, hi, lo, v["r"])
    if t:
        t["trigger_close"] = float(day[i]["c"])
    return t


def control_day(day):
    hi = max(float(b["h"]) for b in day[:RANGE_BARS])
    lo = min(float(b["l"]) for b in day[:RANGE_BARS])
    return _trade(day, RANGE_BARS, hi, lo, 1.0)


def window_of(d):
    for w, (a, b) in WINDOWS.items():
        if a <= d <= b:
            return w
    return None


def summarize(rows):
    rs = [r["r"] for r in rows]
    if not rs:
        return {"n": 0}
    st = ne.cluster_bootstrap(rs, [r["date"] for r in rows])
    gain = sum(x for x in rs if x > 0)
    loss = -sum(x for x in rs if x < 0)
    st.update({"win": sum(1 for x in rs if x > 0) / len(rs), "pf": gain / loss if loss > 0 else None,
               "stop": sum(1 for r in rows if r["how"] == "STOP") / len(rs), "target": sum(1 for r in rows if r["how"] == "TARGET") / len(rs)})
    return st


def verdict(v_all):
    jf = v_all["Judge+Final"]
    if not jf.get("n"):
        return "INCONCLUSIVE"
    if jf["mean"] <= 0:
        return "REJECTED"
    if jf["n"] >= 300 and jf["lo"] > 0 and (v_all["Judge"].get("mean") or 0) > 0.05 and (v_all["Final"].get("mean") or 0) > 0.05:
        return "PROMISING"
    return "INCONCLUSIVE"


def run(symbols, cache, out):
    cache, out = Path(cache), Path(out)
    rows = {v: [] for v in VARIANTS}
    ctrl, spy_ctx = [], {}
    days = 0
    spy_days = sessions(json.loads((cache / "SPY.json").read_text())) if (cache / "SPY.json").exists() else {}
    for s in symbols:
        f = cache / f"{s}.json"
        if not f.exists():
            continue
        for d, day in sorted(sessions(json.loads(f.read_text())).items()):
            if not (START <= d <= END):
                continue
            days += 1
            for v in VARIANTS:
                t = evaluate_day(day, v)
                if t:
                    t.update({"sym": s, "date": d, "win": window_of(d)})
                    rows[v].append(t)
            c = control_day(day)
            if c:
                c.update({"sym": s, "date": d, "win": window_of(d)})
                ctrl.append(c)
    res = {"symbol_days": days, "variants": {}, "control": {}, "verdict": {}, "holm_p_pooled": {}}
    for v, rs in rows.items():
        by = {w: summarize([r for r in rs if r["win"] == w]) for w in WINDOWS}
        by["Judge+Final"] = summarize([r for r in rs if r["win"] in ("Judge", "Final")])
        by["trigger_close_entry_Judge+Final"] = summarize([dict(r, r=(r["exit"] - r["trigger_close"]) / (r["trigger_close"] - r["stop"])) for r in rs
                                                           if r["win"] in ("Judge", "Final") and r["trigger_close"] > r["stop"]])
        res["variants"][v] = by
        res["verdict"][v] = verdict(by)
    ps = [res["variants"][v]["Judge+Final"].get("p_le_zero", 1.0) for v in VARIANTS]
    res["holm_p_pooled"] = dict(zip(VARIANTS, op.holm(ps)))
    res["control"] = {w: summarize([r for r in ctrl if r["win"] == w]) for w in WINDOWS}
    res["control"]["Judge+Final"] = summarize([r for r in ctrl if r["win"] in ("Judge", "Final")])
    if spy_days:
        spy_ret = {d: float(day[LAST_EXIT_IDX]["c"]) / float(day[RANGE_BARS]["o"]) - 1.0 for d, day in spy_days.items()}
        for v in VARIANTS:
            vals = [spy_ret[r["date"]] for r in rows[v] if r["win"] in ("Judge", "Final") and r["date"] in spy_ret]
            res["variants"][v]["spy_open0945_to_1550_mean_on_trade_days"] = sum(vals) / len(vals) if vals else None
    out.mkdir(parents=True, exist_ok=True)
    (out / "s046_results.json").write_text(json.dumps(res, indent=1))
    (out / "s046_results.md").write_text(render(res))
    return res


def render(res):
    L = ["# S-046 results (TEST ONLY)", "", f"{res['symbol_days']:,} symbol-days. Mean R per trade, entry at the next bar open plus 0.02% slippage, 0.02% slippage on exits. Verdicts: " +
         ", ".join(f"{v} {x}" for v, x in res["verdict"].items()) + ".", "",
         "| Variant | Window | n | mean R | 95% interval | win | stop | target |", "|---|---|---|---|---|---|---|---|"]
    for v, by in res["variants"].items():
        for w in ("Development", "Judge", "Final", "Judge+Final"):
            x = by[w]
            if x.get("n"):
                L.append(f"| {v} | {w} | {x['n']} | {x['mean']:+.3f} | {x['lo']:+.3f} to {x['hi']:+.3f} | {x['win']:.0%} | {x['stop']:.0%} | {x['target']:.0%} |")
    L += ["", "Control (buy at the 09:45 open every symbol-day, same stop and 1R target):", "", "| Window | n | mean R | 95% interval |", "|---|---|---|---|"]
    for w in ("Development", "Judge", "Final", "Judge+Final"):
        x = res["control"][w]
        if x.get("n"):
            L.append(f"| {w} | {x['n']} | {x['mean']:+.3f} | {x['lo']:+.3f} to {x['hi']:+.3f} |")
    L += ["", "Holm-adjusted one-sided p (pooled Judge+Final, mean R above zero): " + ", ".join(f"{v} {p:.3f}" for v, p in res["holm_p_pooled"].items()) + "."]
    return "\n".join(L) + "\n"


def fetch(symbols, cache, call=None):
    from consumer import alpaca_paper as ap
    call = call or ap.make_client()
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    for s in symbols:
        f = cache / f"{s}.json"
        if f.exists():
            continue
        out, tok = [], None
        while True:
            q = {"symbols": s, "timeframe": "5Min", "start": START + "T00:00:00Z", "end": "2026-11-14T00:00:00Z", "limit": "10000", "feed": "sip", "adjustment": "all", "sort": "asc"}
            if tok:
                q["page_token"] = tok
            for attempt in range(7):
                try:
                    time.sleep(0.35)
                    j = call("GET", "/v2/stocks/bars?" + urllib.parse.urlencode(q), None, "data")
                    break
                except Exception as exc:
                    if "429" not in str(exc) or attempt == 6:
                        raise
                    time.sleep(5 * 2 ** attempt)
            out += [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]} for b in (j.get("bars") or {}).get(s, [])]
            tok = j.get("next_page_token")
            if not tok:
                break
        # keep only regular-session bars to keep the cache small
        keep = []
        for b in out:
            t = ne.parse_ts(b["t"]).astimezone(ET)
            if dtime(9, 30) <= t.time() < dtime(16, 0):
                keep.append(b)
        f.write_text(json.dumps(keep))
        print(s, len(keep), flush=True)


def read_symbols(path):
    return [ln.split("#")[0].strip() for ln in Path(path).read_text().splitlines() if ln.split("#")[0].strip() and not ln.startswith("#")]


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if a[0] == "fetch":
        syms = read_symbols(a[1]) + ["SPY"]
        fetch(syms, a[2])
        return 0
    res = run(read_symbols(a[1]), a[2], a[3])
    print(json.dumps({"symbol_days": res["symbol_days"], "verdict": res["verdict"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
