"""Overnight (Blue Ocean) quote monitor: how wide are spreads and how thin is depth when the evening session is open? Read only.

Needed because Nasdaq and NYSE Arca plan 23x5 trading from 2026-12-06 (contingent) and the system must not assume daytime liquidity at night.
Polls Alpaca's `feed=overnight` latest quotes for a fixed basket, appends one JSON line per poll to a file you give it, and summarises spreads by
liquidity tier. Data host only (GET), no account access, no orders. Raw lines stay private (Alpaca data terms); only tier medians are published.

  python3 -m consumer.overnight_monitor poll --basket basket.json --out poll.jsonl --start 2026-11-17T01:00:00+00:00 --until 2026-11-17T09:00:00+00:00 [--interval 600]
  python3 -m consumer.overnight_monitor summary --basket basket.json --out poll.jsonl

basket.json: {"tiers": {"1-10": ["AAPL", ...], "ETF": ["SPY", ...]}}.
"""
import argparse
import json
import statistics
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from consumer import alpaca_paper as ap

MIN_INTERVAL = 60


def spread_bps(bid, ask):
    """Quoted spread in bps of the midpoint, or None for a missing, zero or crossed quote."""
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return 1e4 * (ask - bid) / ((ask + bid) / 2.0)


def poll_once(call, symbols, now):
    rows = []
    for i in range(0, len(symbols), 50):
        q = urllib.parse.urlencode({"symbols": ",".join(symbols[i:i + 50]), "feed": "overnight"})
        j = call("GET", "/v2/stocks/quotes/latest?" + q, None, "data")
        for s, qt in (j.get("quotes") or {}).items():
            rows.append({"sym": s, "bp": qt.get("bp"), "ap": qt.get("ap"), "bs": qt.get("bs"), "as": qt.get("as"), "t": qt.get("t")})
    return {"polled_at": now.isoformat(timespec="seconds"), "quotes": rows}


def poll_loop(call, symbols, out, until, interval, now=None, sleep=time.sleep, start=None):
    """Poll from `start` (if given) until `until`. Errors are recorded as lines and never stop the loop."""
    now = now or (lambda: datetime.now(timezone.utc))
    while start is not None and now() < start:
        sleep(min(60, max(1, (start - now()).total_seconds())))
    interval = max(int(interval), MIN_INTERVAL)
    n = 0
    while now() < until:
        t = now()
        try:
            line = poll_once(call, symbols, t)
        except Exception as exc:
            line = {"polled_at": t.isoformat(timespec="seconds"), "error": str(exc)[:160]}
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        n += 1
        sleep(interval)
    return n


def summarize(lines, tiers):
    """Per tier: median and 90th percentile spread over all polls, share of polls with no usable two-sided quote, median top-of-book size."""
    sym_tier = {s: t for t, syms in tiers.items() for s in syms}
    acc = {t: {"spreads": [], "empty": 0, "total": 0, "size": []} for t in tiers}
    polls = errors = 0
    for ln in lines:
        if "error" in ln:
            errors += 1
            continue
        polls += 1
        seen = set()
        for q in ln.get("quotes", []):
            t = sym_tier.get(q["sym"])
            if t is None:
                continue
            seen.add(q["sym"])
            a = acc[t]
            a["total"] += 1
            sp = spread_bps(q.get("bp"), q.get("ap"))
            if sp is None:
                a["empty"] += 1
            else:
                a["spreads"].append(sp)
                a["size"].append(min(q.get("bs") or 0, q.get("as") or 0))
        for s, t in sym_tier.items():  # symbols the API returned nothing for count as empty
            if s not in seen:
                acc[t]["total"] += 1
                acc[t]["empty"] += 1
    out = {"polls": polls, "errors": errors, "tiers": {}}
    for t, a in acc.items():
        sp = sorted(a["spreads"])
        out["tiers"][t] = {
            "quotes": a["total"], "usable": len(sp), "no_usable_quote_share": round(a["empty"] / a["total"], 3) if a["total"] else None,
            "median_bps": round(statistics.median(sp), 2) if sp else None, "p90_bps": round(sp[int(0.9 * (len(sp) - 1))], 2) if sp else None,
            "median_top_size_shares": statistics.median(a["size"]) if a["size"] else None}
    return out


def read_lines(path):
    p = Path(path)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def main(argv=None, call=None, sleep=time.sleep, now=None):
    ap_ = argparse.ArgumentParser(description="Read-only overnight quote monitor.")
    sub = ap_.add_subparsers(dest="cmd", required=True)
    for name in ("poll", "summary"):
        s = sub.add_parser(name)
        s.add_argument("--basket", required=True)
        s.add_argument("--out", required=True)
        if name == "poll":
            s.add_argument("--until", required=True)
            s.add_argument("--interval", type=int, default=600)
            s.add_argument("--start", default=None, help="wait until this time (with offset) before the first poll")
    a = ap_.parse_args(argv)
    tiers = json.loads(Path(a.basket).read_text(encoding="utf-8"))["tiers"]
    if a.cmd == "summary":
        print(json.dumps(summarize(read_lines(a.out), tiers), indent=1))
        return 0
    until = datetime.fromisoformat(a.until)
    start = datetime.fromisoformat(a.start) if a.start else None
    if until.tzinfo is None or (start is not None and start.tzinfo is None):
        print(json.dumps({"error": "--until needs a timezone offset, for example 2026-11-17T09:00:00+00:00"}))
        return 2
    symbols = sorted({s for syms in tiers.values() for s in syms})
    n = poll_loop(call or ap.make_client(), symbols, a.out, until, a.interval, now=now, sleep=sleep, start=start)
    print(json.dumps({"polls": n}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
