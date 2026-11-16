"""Live news watcher and learning loop. Read only: it logs headline events and what SPY does afterwards. No orders.

What it does each cycle (default 60 seconds)
  1. Reads new Alpaca (Benzinga) headlines for the S-043 symbol set since the last cursor.
  2. Runs the frozen S-043 shock detector (3 shock headlines in 90 minutes, 24 hour refractory) on a rolling buffer.
  3. When an event fires: writes it to events.jsonl with the SPY quote at that moment, then records SPY quotes at +5, +15, +30 and +60 minutes into reactions.jsonl.
  4. `summary` turns the logs into a plain table: how far and which way SPY moved after each event, so the next hypothesis rests on data collected live.
Logs are private (Alpaca data terms) and stay in the state directory; only aggregates are published.

  python3 -m consumer.news_watch run --dir DIR --until 2026-11-17T21:00:00+00:00 [--interval 60]
  python3 -m consumer.news_watch summary --dir DIR
"""
import argparse
import json
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import news_events as ne

SYMBOLS = ["SPY", "QQQ", "USO", "GLD", "ITA", "XLE", "FXI", "TLT"]
OFFSETS_MIN = (5, 15, 30, 60)
BUFFER_HOURS = 6
MIN_INTERVAL = 30


def spy_mid(call):
    j = call("GET", "/v2/stocks/quotes/latest?" + urllib.parse.urlencode({"symbols": "SPY", "feed": "sip"}), None, "data")
    q = (j.get("quotes") or {}).get("SPY") or {}
    bp, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
    return round((bp + ask) / 2, 4) if bp > 0 and ask >= bp else None


def append(path, obj):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


def read(path):
    p = Path(path)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def cycle(call, state_dir, now, buffer, cursor, pending):
    """One poll. Returns (new_cursor, events_fired). `buffer` and `pending` are mutated lists."""
    state_dir = Path(state_dir)
    fired = []
    try:
        start = (cursor or now - timedelta(hours=BUFFER_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new = list(ne.fetch_news(call, start, (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), SYMBOLS, sleep=0))
        known = {n["id"] for n in buffer}
        buffer.extend(n for n in new if n["id"] not in known)
        cut = (now - timedelta(hours=BUFFER_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        buffer[:] = [n for n in buffer if n["created_at"] >= cut]
        if new:
            cursor = max(ne.parse_ts(n["created_at"]) for n in new)
        done = {e["t0"] for e in read(state_dir / "events.jsonl")}
        for ev in ne.shock_events(buffer):
            if ev["t0"] in done or ne.parse_ts(ev["t0"]) < now - timedelta(hours=BUFFER_HOURS):
                continue
            # the 24 hour refractory rule must also see earlier logged events, which the six hour buffer cannot
            last = max((ne.parse_ts(e["t0"]) for e in read(state_dir / "events.jsonl")), default=None)
            if last is not None and ne.parse_ts(ev["t0"]) - last < timedelta(hours=24):
                continue
            ev["spy_mid"] = spy_mid(call)
            ev["logged_at"] = now.isoformat(timespec="seconds")
            append(state_dir / "events.jsonl", ev)
            for m in OFFSETS_MIN:
                pending.append({"t0": ev["t0"], "due": (ne.parse_ts(ev["t0"]) + timedelta(minutes=m)).isoformat(), "min": m})
            fired.append(ev)
    except Exception as exc:
        append(state_dir / "errors.jsonl", {"at": now.isoformat(timespec="seconds"), "error": str(exc)[:200]})
    # reaction snapshots that are due
    still = []
    for p in pending:
        if ne.parse_ts(p["due"]) <= now:
            try:
                append(state_dir / "reactions.jsonl", {"t0": p["t0"], "min": p["min"], "at": now.isoformat(timespec="seconds"), "spy_mid": spy_mid(call)})
            except Exception as exc:
                append(state_dir / "errors.jsonl", {"at": now.isoformat(timespec="seconds"), "error": str(exc)[:200]})
        else:
            still.append(p)
    pending[:] = still
    return cursor, fired


def summarize(state_dir):
    events = read(Path(state_dir) / "events.jsonl")
    reacts = read(Path(state_dir) / "reactions.jsonl")
    by = {}
    for r in reacts:
        by.setdefault(r["t0"], {})[r["min"]] = r["spy_mid"]
    rows = []
    for e in events:
        base = e.get("spy_mid")
        row = {"t0": e["t0"], "headline": e["headlines"][0] if e.get("headlines") else "", "spy_mid": base}
        for m in OFFSETS_MIN:
            px = by.get(e["t0"], {}).get(m)
            row[f"m{m}_pct"] = round((px / base - 1) * 100, 3) if base and px else None
        rows.append(row)
    done = [r["m60_pct"] for r in rows if r["m60_pct"] is not None]
    return {"events": len(rows), "with_60m": len(done), "mean_60m_pct": round(sum(done) / len(done), 3) if done else None,
            "share_down_60m": round(sum(1 for v in done if v < 0) / len(done), 3) if done else None, "rows": rows}


def run(call, state_dir, until, interval, now_fn=None, sleep=time.sleep):
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    interval = max(int(interval), MIN_INTERVAL)
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    buffer, pending, cursor, n = [], [], None, 0
    # resume pending reactions after a restart: events from the last hour with missing snapshots
    seen = {(r["t0"], r["min"]) for r in read(Path(state_dir) / "reactions.jsonl")}
    for e in read(Path(state_dir) / "events.jsonl"):
        for m in OFFSETS_MIN:
            if (e["t0"], m) not in seen:
                pending.append({"t0": e["t0"], "due": (ne.parse_ts(e["t0"]) + timedelta(minutes=m)).isoformat(), "min": m})
    while now_fn() < until:
        cursor, _ = cycle(call, state_dir, now_fn(), buffer, cursor, pending)
        n += 1
        sleep(interval)
    return n


def main(argv=None, call=None, sleep=time.sleep, now_fn=None):
    p = argparse.ArgumentParser(description="Read-only headline watcher and learning loop.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dir", required=True)
    r.add_argument("--until", required=True)
    r.add_argument("--interval", type=int, default=60)
    s = sub.add_parser("summary")
    s.add_argument("--dir", required=True)
    a = p.parse_args(argv)
    if a.cmd == "summary":
        print(json.dumps(summarize(a.dir), indent=1))
        return 0
    until = datetime.fromisoformat(a.until)
    if until.tzinfo is None:
        print(json.dumps({"error": "--until needs a timezone offset"}))
        return 2
    n = run(call or ap.make_client(), a.dir, until, a.interval, now_fn, sleep)
    print(json.dumps({"cycles": n}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
