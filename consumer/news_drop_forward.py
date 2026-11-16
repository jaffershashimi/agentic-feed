"""S-044 forward data collection: each day after the close, find big single-stock drops, classify them NEWS / ANALYST / NO_NEWS, and log them. Read only.

This is the learning loop for the news-drop idea. It writes one JSON line per event to a private log so the pre-registered S-044 rule can be re-tested on data
collected live (and so the 5 and 10 session outcomes can be filled in later by `outcomes`). It places no orders. Orders for S-044, if the study licenses any,
go through a separate guarded step added only after the owner-visible verdict.

Event rule (same as the frozen spec): close/prior close - 1 <= -8%, prior close >= 10 dollars, 20-day average dollar volume >= 20 million, SPY return on the day > -2%.
  python3 -m consumer.news_drop_forward scan --universe U.txt --log events.jsonl
  python3 -m consumer.news_drop_forward outcomes --log events.jsonl
"""
import argparse
import json
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import alpaca_paper as ap
from consumer import news_drop_study as nds
from consumer import news_events as ne
from consumer import oversold_forward as of
from consumer import oversold_panel as op

ET = ZoneInfo("America/New_York")
DROP = -0.08
MIN_PRICE = 10.0
MIN_ADV = 2e7


def find_drops(data, asof):
    """Drop events on the as-of session from bars (already trimmed to asof). SPY must be present."""
    spy = data.get("SPY")
    if not spy or spy["date"][-1] != asof.isoformat() or len(spy["c"]) < 2:
        raise ap.GuardError(f"refused: SPY has no bar for {asof}")
    spy_ret = spy["c"][-1] / spy["c"][-2] - 1.0
    out = []
    if spy_ret <= -0.02:
        return out, spy_ret
    for sym, d in data.items():
        if sym == "SPY" or len(d["c"]) < 30 or d["date"][-1] != asof.isoformat():
            continue
        r = d["c"][-1] / d["c"][-2] - 1.0
        dv = [c * v for c, v in zip(d["c"][-21:-1], d["v"][-21:-1])]
        adv = sum(dv) / len(dv) if dv else 0.0
        if r <= DROP and d["c"][-2] >= MIN_PRICE and adv >= MIN_ADV:
            out.append({"sym": sym, "date": asof.isoformat(), "prev": d["date"][-2], "ret": r, "close": d["c"][-1], "adv20": adv})
    return sorted(out, key=lambda e: e["ret"]), spy_ret


def news_for(call, events, asof):
    by = {}
    for e in events:
        a = datetime.fromisoformat(e["prev"]).replace(hour=16, tzinfo=ET).astimezone(timezone.utc)
        b = datetime.fromisoformat(e["date"]).replace(hour=16, tzinfo=ET).astimezone(timezone.utc)
        by[e["sym"]] = list(ne.fetch_news(call, a.strftime("%Y-%m-%dT%H:%M:%SZ"), b.strftime("%Y-%m-%dT%H:%M:%SZ"), [e["sym"]], sleep=0.2))
    return by


def scan(call, universe, now, fetch=None, news=None):
    asof = of.last_complete_session(now)
    start = asof - timedelta(days=60)
    data = of.trim((fetch or of.fetch_daily)(call, sorted(set(universe) | {"SPY"}), start, now - timedelta(minutes=20)), asof)
    events, spy_ret = find_drops(data, asof)
    items = (news or news_for)(call, events, asof)
    for e in events:
        e["group"], e["sub"] = nds.classify(e["sym"], items.get(e["sym"], []))
        e["headline"] = next((n["headline"] for n in sorted(items.get(e["sym"], []), key=lambda n: n["created_at"]) if ne.is_informative(n)), None)
    return {"asof": asof.isoformat(), "spy_ret": spy_ret, "events": events}


def append_new(log, result):
    log = Path(log)
    have = {(json.loads(x)["sym"], json.loads(x)["date"]) for x in log.read_text().splitlines() if x.strip()} if log.exists() else set()
    n = 0
    with open(log, "a", encoding="utf-8") as fh:
        for e in result["events"]:
            if (e["sym"], e["date"]) not in have:
                fh.write(json.dumps({**e, "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
                n += 1
    return n


def outcomes(call, log, now, fetch=None):
    """Fill in market-adjusted 5 and 10 session returns (open of t+1 to close of t+k, minus SPY) for logged events that are old enough."""
    rows = [json.loads(x) for x in Path(log).read_text().splitlines() if x.strip()] if Path(log).exists() else []
    need = [r for r in rows if "o1_10" not in r]
    if not need:
        return {"updated": 0, "events": len(rows)}
    first = min(r["date"] for r in need)
    start = datetime.fromisoformat(first).date() - timedelta(days=3)
    syms = sorted({r["sym"] for r in need} | {"SPY"})
    data = (fetch or of.fetch_daily)(call, syms, start, now - timedelta(minutes=20))
    spy = data["SPY"]
    upd = 0
    for r in rows:
        if "o1_10" in r or r["sym"] not in data:
            continue
        d = data[r["sym"]]
        if r["date"] not in d["date"] or r["date"] not in spy["date"]:
            continue
        i, si = d["date"].index(r["date"]), spy["date"].index(r["date"])
        if i + 10 >= len(d["c"]) or si + 10 >= len(spy["c"]):
            continue
        for k in (5, 10):
            r[f"o1_{k}"] = (d["c"][i + k] / d["o"][i + 1] - 1.0) - (spy["c"][si + k] / spy["o"][si + 1] - 1.0)
        upd += 1
    if upd:
        Path(log).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return {"updated": upd, "events": len(rows)}


def main(argv=None, call=None, now=None, fetch=None, news=None):
    p = argparse.ArgumentParser(description="S-044 forward data collection (read only).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--universe", required=True)
    s.add_argument("--log", required=True)
    o = sub.add_parser("outcomes")
    o.add_argument("--log", required=True)
    a = p.parse_args(argv)
    try:
        call = call or ap.make_client()
        now = now or datetime.now(timezone.utc)
        if a.cmd == "scan":
            res = scan(call, of.load_universe(Path(a.universe)), now, fetch, news)
            res["logged_new"] = append_new(a.log, res)
        else:
            res = outcomes(call, a.log, now, fetch)
    except ap.GuardError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
