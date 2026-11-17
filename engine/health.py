"""Health checks for the failures that have actually happened in this system. Read only: they report, they never change anything.

  stops_present     every open position has a live sell stop covering its quantity (a position with no stop is the worst silent failure a paper or real account can have)
  orphan_orders     open orders whose client_order_id is not one of ours
  stale_pending     entries that have been waiting for more than one session
  news_source       the news feed still carries the sources and freshness the news code was built for (Benzinga went away on 2026-11 without any error)
  data_fresh        SPY has a bar for the last completed session
"""
import urllib.parse
from datetime import datetime, timedelta, timezone

from consumer import news_events as ne
from consumer import oversold_forward as of
from consumer.alpaca_options import is_option

OK, WARN, FAIL = "OK", "WARN", "FAIL"
OUR_PREFIXES = ("s042v3", "s044n", "s048b55m", "xlab")
OPTION_PREFIXES = ("debit-vertical-o42-", "close-o42-")   # S-052 option spread orders
EXPECTED_NEWS_SOURCES = {"benzinga"}


def stops_present(call):
    pos = call("GET", "/v2/positions")
    opens = call("GET", "/v2/orders?status=open&limit=500&nested=true")
    cover = {}
    for o in opens:
        for x in [o] + (o.get("legs") or []):
            if x.get("side") == "sell" and x.get("type") in ("stop", "stop_limit", "trailing_stop") and x.get("status") in ("new", "accepted", "held", "pending_new"):
                cover[x["symbol"]] = cover.get(x["symbol"], 0) + float(x.get("qty") or 0)
    # option spreads (S-052) are defined-risk and have no stop order: their exits are the morning manage step, so they are not stock stops
    bad = [p["symbol"] for p in pos if not is_option(p["symbol"]) and cover.get(p["symbol"], 0) < float(p["qty"]) - 1e-9]
    return (OK if not bad else FAIL), (f"{len(pos)} positions, all covered by a stop" if not bad else f"NO FULL STOP on: {', '.join(sorted(bad))}")


def orphan_orders(call):
    opens = call("GET", "/v2/orders?status=open&limit=500&nested=false")
    held = {p["symbol"] for p in call("GET", "/v2/positions")}
    # the stop attached to one of our entries carries an auto-generated id, so a sell stop on a symbol we hold is not an orphan
    odd = [o.get("symbol") or "?" for o in opens if not (o.get("client_order_id") or "").startswith(OUR_PREFIXES + OPTION_PREFIXES)
           and not (o.get("side") == "sell" and o.get("type") == "stop" and o.get("symbol") in held)]
    return (OK if not odd else WARN), ("no unknown open orders" if not odd else f"orders not placed by our tools: {', '.join(sorted(set(odd)))}")


def stale_pending(call, now):
    opens = call("GET", "/v2/orders?status=open&limit=500&nested=false")
    old = [o.get("symbol") or "?" for o in opens if o.get("side") == "buy" and o.get("submitted_at") and now - ne.parse_ts(o["submitted_at"]) > timedelta(hours=40)]
    return (OK if not old else WARN), ("no entries waiting more than a session" if not old else f"entries waiting more than a session: {', '.join(sorted(old))}")


def news_source(call, now, market_hours):
    j = call("GET", "/v1beta1/news?" + urllib.parse.urlencode({"limit": "50", "sort": "desc"}), None, "data")
    items = j.get("news", [])
    if not items:
        return FAIL, "the news feed returned nothing"
    sources = {(n.get("source") or "").lower() for n in items}
    age_min = (now - max(ne.parse_ts(n["created_at"]) for n in items)).total_seconds() / 60
    if not (sources & EXPECTED_NEWS_SOURCES):
        return WARN, f"expected source {sorted(EXPECTED_NEWS_SOURCES)} is absent; the feed now carries {sorted(sources)}. News strategies and the headline watcher are blind"
    if market_hours and age_min > 30:
        return WARN, f"newest headline is {age_min:.0f} minutes old during market hours"
    return OK, f"sources {sorted(sources)}, newest {age_min:.0f} minutes old"


def data_fresh(call, now):
    asof = of.last_complete_session(now)
    j = call("GET", "/v2/stocks/bars?" + urllib.parse.urlencode({"symbols": "SPY", "timeframe": "1Day", "start": (asof - timedelta(days=7)).isoformat(), "limit": "10", "feed": "sip", "adjustment": "all"}), None, "data")
    last = (j.get("bars", {}).get("SPY") or [{}])[-1].get("t", "")[:10]
    return (OK if last >= asof.isoformat() else FAIL), f"SPY last bar {last}, last complete session {asof}"


def check_all(call, now=None):
    now = now or datetime.now(timezone.utc)
    et_hour = (now - timedelta(hours=5)).hour  # coarse: only used to decide whether a stale headline matters
    market_hours = now.weekday() < 5 and 14 <= now.hour < 21
    rows = []
    for name, fn in (("stops present", lambda: stops_present(call)), ("orphan orders", lambda: orphan_orders(call)), ("stale pending entries", lambda: stale_pending(call, now)),
                     ("news source", lambda: news_source(call, now, market_hours)), ("data fresh", lambda: data_fresh(call, now))):
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = FAIL, f"check itself failed: {str(exc)[:80]}"
        rows.append({"check": name, "status": status, "detail": detail})
    return rows


NAMED_CHECKS = {"stops present", "orphan orders", "stale pending entries"}   # their details can list the stocks we hold or trade


def redact(row):
    """Detail with the stock names removed: 'NO FULL STOP on: A, B' becomes 'NO FULL STOP on: 2 name(s) withheld'. For logs that live on GitHub."""
    d = row["detail"]
    if row["check"] in NAMED_CHECKS and row["status"] != OK and ": " in d:
        head, names = d.rsplit(": ", 1)
        return f"{head}: {len(names.split(', '))} name(s) withheld"
    return d


def render(rows, safe=False):
    w = max(len(r["check"]) for r in rows)
    return "\n".join(f"{r['status']:<5} {r['check']:<{w}}  {redact(r) if safe else r['detail']}" for r in rows)
