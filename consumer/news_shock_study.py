"""S-043 study: after a cluster of geopolitical-shock headlines, does SPY keep falling? TEST ONLY. Spec frozen in Agentic-Lab
system/06-research/specs/S-043-geopolitical-shock-spy.md. Reads cached headlines and Alpaca SPY minute bars (private), writes JSON and markdown.

  python3 -m consumer.news_shock_study NEWS.jsonl SESSIONS.json OUT_DIR       (live: fetches minute bars through the Alpaca data host)
SESSIONS.json is a sorted list of trading dates (from SPY daily bars) because the built-in calendar only covers 2025 to 2027.
"""
import json
import random
import sys
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import news_events as ne

ET = ZoneInfo("America/New_York")
COST = 0.0001
HORIZONS = (30, 60)
STOP, TARGET = 0.005, 0.005


def day_bounds(d):
    a = datetime.combine(d, time(9, 30), ET).astimezone(timezone.utc)
    b = datetime.combine(d, time(16, 0), ET).astimezone(timezone.utc)
    return a, b


def fetch_day(call, d):
    """Regular-session 1-minute SPY bars for date d: [{t (utc datetime), o, h, l, c}]."""
    a, b = day_bounds(d)
    out, tok = [], None
    while True:
        q = {"symbols": "SPY", "timeframe": "1Min", "start": a.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "limit": "10000", "feed": "sip", "adjustment": "all", "sort": "asc"}
        if tok:
            q["page_token"] = tok
        j = call("GET", "/v2/stocks/bars?" + urllib.parse.urlencode(q), None, "data")
        for x in (j.get("bars") or {}).get("SPY", []):
            out.append({"t": ne.parse_ts(x["t"]), "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]), "c": float(x["c"])})
        tok = j.get("next_page_token")
        if not tok:
            return out


def session_type(t0, sessions):
    """('RTH', date) when t0 is inside 09:30 to 15:30 ET on a session; ('OTHER', None) otherwise."""
    et = t0.astimezone(ET)
    if et.date().isoformat() in sessions and time(9, 30) <= et.time() < time(15, 30):
        return "RTH", et.date()
    return "OTHER", None


def next_session(d, sessions):
    for k in range(1, 10):
        nd = d + timedelta(days=k)
        if nd.isoformat() in sessions:
            return nd
    return None


def other_entry_day(t0, sessions):
    """Next regular open at or after t0 for a headline outside RTH."""
    et = t0.astimezone(ET)
    d = et.date()
    if d.isoformat() in sessions and et.time() < time(9, 30):
        return d
    return next_session(d, sessions)


def short_returns(bars, nxt, entry_idx):
    """bars: that session's minute bars, nxt: next session's bars (or None). Entry at the OPEN of bars[entry_idx]. Returns a dict of short returns before costs."""
    if entry_idx is None or entry_idx >= len(bars):
        return None
    e = bars[entry_idx]["o"]
    out = {}
    for h in HORIZONS:
        j = entry_idx + h
        if j < len(bars):
            out[f"m{h}"] = -(bars[j]["o"] / e - 1.0)
    out["close"] = -(bars[-1]["c"] / e - 1.0)
    if nxt:
        out["next_close"] = -(nxt[-1]["c"] / e - 1.0)
    stop_px, tp_px = e * (1 + STOP), e * (1 - TARGET)
    hit = None
    for b in bars[entry_idx:]:
        if b["h"] >= stop_px:  # stop first when one bar touches both (conservative)
            hit = "STOP"
            break
        if b["l"] <= tp_px:
            hit = "TARGET"
            break
    out["bracket"] = {"STOP": -STOP, "TARGET": TARGET, None: out["close"]}[hit]
    out["bracket_hit"] = hit or "NONE"
    # volatility proxy: the largest absolute excursion over the first hour relative to entry
    first = bars[entry_idx:entry_idx + 60]
    out["abs_move_60"] = max(abs(b["h"] / e - 1.0) if b["h"] / e - 1.0 > abs(b["l"] / e - 1.0) else abs(b["l"] / e - 1.0) for b in first) if first else None
    return out


def entry_index(bars, t_after):
    for i, b in enumerate(bars):
        if b["t"] >= t_after:
            return i
    return None


def stats(vals, dates):
    st = ne.cluster_bootstrap(vals, dates)
    if st is None:
        return {"n": 0}
    s = sorted(vals)
    st.update({"median": s[len(s) // 2], "hit": sum(1 for v in vals if v - 2 * COST > 0) / len(vals)})
    return st


def run(news_path, sessions, out, get_day, baseline_days=300, seed=7):
    headlines = ne.load_jsonl(news_path)
    events = ne.shock_events(headlines)
    sess = set(sessions)
    cache = {}

    def day(d):
        k = d.isoformat()
        if k not in cache:
            cache[k] = get_day(d)
        return cache[k]

    rows = []
    for ev in events:
        t0 = ne.parse_ts(ev["t0"])
        typ, d = session_type(t0, sess)
        if typ == "OTHER":
            d = other_entry_day(t0, sess)
        if d is None or d.isoformat() not in sess:
            continue
        bars = day(d)
        if not bars:
            continue
        idx = entry_index(bars, t0 + timedelta(minutes=1)) if typ == "RTH" else 0
        nd = next_session(d, sess)
        r = short_returns(bars, day(nd) if nd else None, idx)
        if r:
            rows.append({"t0": ev["t0"], "type": typ, "date": d.isoformat(), "r": r, "headlines": ev["headlines"][:2]})
    # baseline: random sessions, same entry rule (RTH: random minute between 09:30 and 15:30; OTHER: the 09:30 open)
    rnd = random.Random(seed)
    pool = sorted(sess)
    base = {"RTH": [], "OTHER": []}
    event_days = {r["date"] for r in rows}
    for d in rnd.sample([p for p in pool if p not in event_days and p >= "2016-01-04"], min(baseline_days, len(pool))):
        dd = date.fromisoformat(d)
        bars = day(dd)
        if len(bars) < 100:
            continue
        nd = next_session(dd, sess)
        nxt = day(nd) if nd else None
        base["OTHER"].append((d, short_returns(bars, nxt, 0)))
        base["RTH"].append((d, short_returns(bars, nxt, rnd.randrange(0, min(len(bars), 360)))))
    res = {"events_detected": len(events), "events_scored": len(rows), "by_type": {}, "baseline": {}, "events": [{"t0": r["t0"], "type": r["type"], "date": r["date"], "headline": r["headlines"][0]} for r in rows]}
    for typ in ("RTH", "OTHER"):
        sel = [r for r in rows if r["type"] == typ]
        res["by_type"][typ] = {}
        res["baseline"][typ] = {}
        for k in ("m30", "m60", "close", "next_close", "bracket"):
            vs = [(r["r"][k], r["date"]) for r in sel if k in r["r"]]
            res["by_type"][typ][k] = stats([v for v, _ in vs], [d for _, d in vs])
            bs = [(r[k], d) for d, r in base[typ] if r and k in r]
            res["baseline"][typ][k] = stats([v for v, _ in bs], [d for _, d in bs])
        hits = [r["r"]["bracket_hit"] for r in sel]
        res["by_type"][typ]["bracket_hits"] = {h: hits.count(h) for h in ("STOP", "TARGET", "NONE")}
        am = [r["r"]["abs_move_60"] for r in sel if r["r"].get("abs_move_60") is not None]
        bm = [r["abs_move_60"] for _, r in base[typ] if r and r.get("abs_move_60") is not None]
        res["by_type"][typ]["mean_abs_move_60"] = sum(am) / len(am) if am else None
        res["baseline"][typ]["mean_abs_move_60"] = sum(bm) / len(bm) if bm else None
    res["verdict"] = verdict(res)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "s043_results.json").write_text(json.dumps(res, indent=1))
    (out / "s043_results.md").write_text(render(res))
    return res


def verdict(res):
    """PROMISING if RTH m60 or close has n>=25 and the interval of the mean short return (before costs) is above zero by more than costs;
    REJECTED if both point estimates are below zero; else INCONCLUSIVE. Spec wording: interval excludes zero on the positive side."""
    sel = res["by_type"]["RTH"]
    a, b = sel["m60"], sel["close"]
    ok = [x for x in (a, b) if x.get("n", 0) >= 25 and x["lo"] - 2 * COST > 0]
    if ok:
        return "PROMISING"
    if a.get("n") and b.get("n") and a["mean"] < 0 and b["mean"] < 0:
        return "REJECTED"
    return "INCONCLUSIVE"


def render(res):
    L = ["# S-043 results (TEST ONLY)", "", f"Events detected {res['events_detected']}, scored {res['events_scored']}. Short SPY, before costs (cost 0.01% per side). Verdict: **{res['verdict']}**.", "",
         "| Type | Horizon | n | mean short | interval | median | baseline mean |", "|---|---|---|---|---|---|---|"]
    for typ in ("RTH", "OTHER"):
        for k in ("m30", "m60", "close", "next_close", "bracket"):
            v, b = res["by_type"][typ][k], res["baseline"][typ][k]
            if v.get("n"):
                L.append(f"| {typ} | {k} | {v['n']} | {v['mean'] * 100:+.3f}% | {v['lo'] * 100:+.3f}% to {v['hi'] * 100:+.3f}% | {v['median'] * 100:+.3f}% | {b['mean'] * 100:+.3f}% |" if b.get("n") else f"| {typ} | {k} | {v['n']} | {v['mean'] * 100:+.3f}% | {v['lo'] * 100:+.3f}% to {v['hi'] * 100:+.3f}% | {v['median'] * 100:+.3f}% | n/a |")
    for typ in ("RTH", "OTHER"):
        L.append(f"\n{typ}: bracket first hits {res['by_type'][typ].get('bracket_hits')}; mean absolute move in first hour {res['by_type'][typ].get('mean_abs_move_60')} vs baseline {res['baseline'][typ].get('mean_abs_move_60')}.")
    return "\n".join(L) + "\n"


def main(argv=None, call=None):
    from consumer import alpaca_paper as ap
    a = sys.argv[1:] if argv is None else argv
    call = call or ap.make_client()
    import time as _t

    def get_day(d):
        _t.sleep(0.25)
        return fetch_day(call, d)

    res = run(a[0], json.loads(Path(a[1]).read_text()), a[2], get_day)
    print(json.dumps({"events": res["events_scored"], "verdict": res["verdict"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
