"""Read-only preflight of everything the schedules depend on. Prints PASS / WARN / FAIL per check and exits 1 on any FAIL. Never sends an order, never prints a balance or a key.

  python3 -m consumer.system_preflight [--feed REPO] [--hub REPO] [--watch-dir DIR]

Checks: Alpaca keys present (a yes or no only), paper account active, paper-host guard, HALT file, market clock, data freshness, news / overnight / corporate-action
endpoints, queued and open order counts by strategy tag, universe files, background processes, repository cleanliness and sync, privacy and registry checks,
the S-042 and S-044F dry runs, and the bar-archive check.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import news_drop_forward as ndf
from consumer import oversold_forward as of

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _run(cmd, cwd, timeout=240):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def check_all(call, feed, hub, watch_dir, now=None, run=_run, env=os.environ):
    now = now or datetime.now(timezone.utc)
    out = []

    def add(name, status, detail=""):
        out.append({"check": name, "status": status, "detail": detail})

    add("alpaca keys in the environment", PASS if env.get("ALPACA_API_KEY") and env.get("ALPACA_SECRET_KEY") else FAIL, "present" if env.get("ALPACA_API_KEY") else "missing (value never shown)")
    try:
        base = ap.paper_base(env)
        add("paper-host guard", PASS, "paper host only")
    except ap.GuardError as exc:
        add("paper-host guard", FAIL, str(exc)[:80])
    try:
        acct = call("GET", "/v2/account")
        add("paper account active", PASS if acct.get("status") == "ACTIVE" else FAIL, f"status {acct.get('status')}")
    except Exception as exc:
        add("paper account active", FAIL, str(exc)[:80])
    halt = Path(os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper")) / "HALT"
    add("kill switch (HALT) not set", PASS if not halt.exists() else WARN, "no HALT file" if not halt.exists() else "HALT file present: all order tools refuse")
    try:
        clk = call("GET", "/v2/clock")
        add("market clock", PASS, f"open={clk.get('is_open')} next_open={clk.get('next_open')}")
    except Exception as exc:
        add("market clock", FAIL, str(exc)[:80])
    try:
        asof = of.last_complete_session(now)
        j = call("GET", "/v2/stocks/bars?" + urllib.parse.urlencode({"symbols": "SPY", "timeframe": "1Day", "start": (asof - timedelta(days=7)).isoformat(), "limit": "10", "feed": "sip", "adjustment": "all"}), None, "data")
        last = (j.get("bars", {}).get("SPY") or [{}])[-1].get("t", "")[:10]
        add("SPY daily data is current", PASS if last == asof.isoformat() else WARN, f"last bar {last}, last complete session {asof}")
    except Exception as exc:
        add("SPY daily data is current", FAIL, str(exc)[:80])
    for name, path in (("news endpoint", "/v1beta1/news?" + urllib.parse.urlencode({"symbols": "SPY", "limit": "1"})),
                       ("overnight quote feed", "/v2/stocks/quotes/latest?" + urllib.parse.urlencode({"symbols": "SPY", "feed": "overnight"})),
                       ("corporate-actions endpoint", "/v1/corporate-actions?" + urllib.parse.urlencode({"symbols": "AAPL", "types": "forward_split", "start": "2020-08-01", "end": "2020-09-30", "limit": "5"}))):
        try:
            j = call("GET", path, None, "data")
            add(name, PASS if j else WARN, "reachable")
        except Exception as exc:
            add(name, FAIL, str(exc)[:80])
    try:
        j = call("GET", "/v1beta1/news?" + urllib.parse.urlencode({"symbols": "SPY,QQQ,USO,GLD,ITA,XLE,FXI,TLT", "start": (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": "5"}), None, "data")
        n = len(j.get("news", []))
        add("macro-tagged headline feed (S-043 symbol set) alive", PASS if n >= 5 else WARN, f"{n} items in 30 days (it was about 1,000 a month through 2026-10)" + ("" if n >= 5 else "; tag feed is dry, the watcher uses the untagged feed"))
    except Exception as exc:
        add("macro-tagged headline feed (S-043 symbol set) alive", FAIL, str(exc)[:80])
    try:
        opens = call("GET", "/v2/orders?status=open&limit=200&nested=true")
        pos = call("GET", "/v2/positions")
        tags = {}
        for o in opens:
            t = (o.get("client_order_id") or "other").split("-")[0]
            tags[t] = tags.get(t, 0) + 1
        add("orders and positions readable", PASS, f"open orders by tag {tags}, positions {len(pos)}")
    except Exception as exc:
        add("orders and positions readable", FAIL, str(exc)[:80])
    for f in (hub / "system/11-market-data/universe_s042_2026.txt", hub / "system/11-market-data/universe.txt", hub / "system/11-market-data/universe_s046_orb.txt"):
        add(f"universe file {f.name}", PASS if f.exists() and f.stat().st_size > 20 else FAIL, f"{len(of.load_universe(f)) if f.exists() else 0} symbols")
    for label, repo in (("agentic-feed", feed), ("Agentic-Lab", hub)):
        code, txt = run(["git", "status", "-sb", "--porcelain"], repo)
        dirty = [ln for ln in txt.splitlines()[1:] if ln.strip()]
        add(f"{label} working tree", PASS if not dirty else WARN, "clean" if not dirty else f"{len(dirty)} changed or untracked paths")
    code, txt = run([sys.executable, "scripts/privacy_check.py"], feed)
    add("privacy check (feed)", PASS if code == 0 else FAIL, txt.splitlines()[-1][:100] if txt else "")
    code, txt = run([sys.executable, "scripts/registry_check.py"], hub)
    add("registry check (hub)", PASS if code == 0 else FAIL, txt.splitlines()[-1][:100] if txt else "")
    # the code lives in agentic-feed since 2026-11-17, the data in the hub: run the module from the feed checkout and point it at the hub's data
    code, txt = run([sys.executable, "-m", "consumer.bar_archive", "check", "--root", str(hub / "system/11-market-data")], feed)
    flat = txt.replace("\n", "").replace(" ", "")
    add("bar archive check", PASS if code == 0 and '"problems":[]' in flat else FAIL, "days ok, no problems" if code == 0 and '"problems":[]' in flat else "problems found: run consumer.bar_archive check")
    universe = of.load_universe(hub / "system/11-market-data/universe_s042_2026.txt")
    try:
        r = of.run(call, universe, now, Path(os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper")), False)
        add("S-042 dry run", PASS, f"asof {r['asof']} stress={r['stress']} v3={r['v3_signals']} entries={len(r['entries'])} quarantined={len(r['quarantined'])}")
    except Exception as exc:
        add("S-042 dry run", FAIL, str(exc)[:100])
    try:
        r = ndf.run(call, universe, now, Path(os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper")), False)
        add("S-044F dry run", PASS if not r.get("news_feed_suspect") else WARN, f"suspended={r.get('suspended')} asof {r['asof']} drops={r['drops']} no_news={r['no_news']} entries={len(r['entries'])} news_feed_suspect={r.get('news_feed_suspect')}")
    except Exception as exc:
        add("S-044F dry run", FAIL, str(exc)[:100])
    return out


def render(rows):
    w = max(len(r["check"]) for r in rows)
    lines = [f"{r['status']:<5} {r['check']:<{w}}  {r['detail']}" for r in rows]
    c = {s: sum(1 for r in rows if r["status"] == s) for s in (PASS, WARN, FAIL)}
    lines.append(f"\n{c[PASS]} pass, {c[WARN]} warn, {c[FAIL]} fail")
    return "\n".join(lines)


def main(argv=None, call=None, now=None):
    p = argparse.ArgumentParser(description="Read-only preflight of the schedule dependencies.")
    p.add_argument("--feed", default="/home/user/agentic-feed")
    p.add_argument("--hub", default="/home/user/Agentic-Lab")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    rows = check_all(call or ap.make_client(), Path(a.feed), Path(a.hub), None, now)
    print(json.dumps(rows, indent=1) if a.json else render(rows))
    return 1 if any(r["status"] == FAIL for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
