"""Daily reconciliation: does what the broker holds match what our own order records say we should hold?

A trading desk reconciles positions and cash against external records every day and works the breaks by age. Our books are the tagged orders we sent (the
client_order_id prefixes in engine.health.OUR_PREFIXES); the external record is the broker's positions. Stop-leg fills count as sells. Breaks:

  QTY_BREAK            held quantity differs from tagged buys minus tagged sells                      FAIL
  CAP_BREACH           more S-042 positions held than the position cap                                FAIL
  UNEXPLAINED_POSITION a position with no order of ours behind it (a manual trade or a lost record)    WARN
  MISSING_POSITION     our records say we hold it, the broker does not (closed outside our tools)     WARN
  OVERDUE_EXIT         an S-042 position held past the time stop plus a margin                        WARN
  HISTORY_TRUNCATED    the broker returned a full page of orders, so older trades may be missing      WARN

Read only. Output is counts by type and the oldest break's age in days; symbols stay out of the log (this runs in a GitHub log).
"""
import math
from datetime import datetime, timezone

from consumer import news_events as ne
from consumer import oversold_forward as of
from consumer.alpaca_options import is_option
from engine import limits
from engine.health import OUR_PREFIXES

FAIL_TYPES = {"QTY_BREAK", "CAP_BREACH"}
OVERDUE_DAYS = math.ceil(limits.get("S042_HOLD_SESSIONS") * 7 / 5) + 3   # calendar days: the time stop, weekends, and a holiday margin


def _tagged(o):
    cid = o.get("client_order_id") or ""
    return any(cid.startswith((p + "-", p + "x-", p + "s-")) for p in OUR_PREFIXES)   # s- is a stop-guard stop


def net_from_orders(closed):
    """symbol -> (net filled quantity, ISO time of the latest tagged buy fill). Filled stop legs are sells."""
    net, last_buy = {}, {}
    for o in closed:
        sym = o.get("symbol")
        if _tagged(o):
            q = float(o.get("filled_qty") or 0)
            if q > 0:
                net[sym] = net.get(sym, 0.0) + (q if o.get("side") == "buy" else -q)
                if o.get("side") == "buy":
                    last_buy[sym] = max(last_buy.get(sym, ""), o.get("filled_at") or "")
        for leg in (o.get("legs") or []):
            if leg.get("side") == "sell" and float(leg.get("filled_qty") or 0) > 0 and _tagged(o):
                net[sym] = net.get(sym, 0.0) - float(leg["filled_qty"])
    return net, last_buy


def reconcile(positions, closed, now=None):
    now = now or datetime.now(timezone.utc)
    net, last_buy = net_from_orders(closed)
    truncated = len(closed) >= 500
    breaks = []

    def add(kind, sym, detail, age=None):
        breaks.append({"type": kind, "sym": sym, "detail": detail, "age_days": age})

    held = {p["symbol"]: float(p["qty"]) for p in positions if not is_option(p["symbol"])}   # option spreads (S-052) are checked by their own report
    for sym, qty in held.items():
        age = None
        if last_buy.get(sym):
            age = max(0, (now - ne.parse_ts(last_buy[sym])).days)
        if sym not in net and sym not in last_buy:
            add("UNEXPLAINED_POSITION", sym, "no order of ours explains this position")
        elif abs(net.get(sym, 0.0) - qty) > 0.5:
            add("QTY_BREAK", sym, f"held {qty:g}, records say {net.get(sym, 0.0):g}", age)
        elif age is not None and age > OVERDUE_DAYS and any((o.get("client_order_id") or "").startswith(of.TAG + "-") and o.get("symbol") == sym for o in closed):
            add("OVERDUE_EXIT", sym, f"held {age} days; the time stop is {limits.get('S042_HOLD_SESSIONS')} sessions", age)
    if not truncated:
        for sym, q in net.items():
            if q > 0.5 and sym not in held:
                add("MISSING_POSITION", sym, f"records say {q:g} held, the broker holds none", (now - ne.parse_ts(last_buy[sym])).days if last_buy.get(sym) else None)
    s042 = [s for s in held if any((o.get("client_order_id") or "").startswith(of.TAG + "-") and o.get("symbol") == s for o in closed)]
    cap = limits.get("S042_POSITION_CAP")
    if len(s042) > cap:
        add("CAP_BREACH", "*", f"{len(s042)} S-042 positions held, cap {cap}")
    if truncated:
        add("HISTORY_TRUNCATED", "*", "500 closed orders returned; older trades are not checked")
    counts = {}
    for b in breaks:
        counts[b["type"]] = counts.get(b["type"], 0) + 1
    status = "FAIL" if any(b["type"] in FAIL_TYPES for b in breaks) else ("WARN" if breaks else "OK")
    ages = [b["age_days"] for b in breaks if b["age_days"] is not None]
    return {"status": status, "positions_checked": len(held), "break_counts": counts, "oldest_break_days": max(ages) if ages else None, "breaks": breaks}


def analyze(call, now=None):
    closed = call("GET", "/v2/orders?status=closed&limit=500&nested=true&direction=desc")
    return reconcile(call("GET", "/v2/positions"), closed, now)


def summary(result):
    """Counts-only line for logs and the desk pack (the full break list, with symbols, is for the owner's private use)."""
    if not result["breaks"]:
        return f"{result['positions_checked']} positions checked, no breaks"
    parts = ", ".join(f"{k} x{v}" for k, v in sorted(result["break_counts"].items()))
    return f"{result['positions_checked']} positions checked, breaks: {parts}" + (f"; oldest {result['oldest_break_days']} days" if result["oldest_break_days"] is not None else "")
