"""Post-trade cost analysis (TCA): what did our executions really cost, against what the backtest assumed?

Institutions measure implementation shortfall: the gap between the price when the decision was made and the price actually obtained, plus the orders that never
filled (a shortfall that ignores unfilled orders flatters the trader). Two views are reported for every filled S-042 entry:

  vs_signal_close   fill price against the signal close the strategy decided on (decision to fill; includes the overnight gap and the spread)
  vs_arrival        fill price against the open of the first one-minute IEX bar at or after the moment the order was sent (the execution cost the backtest's
                    per-side slippage number is meant to model: 9 bps in the 10:00 ET scenario)

When an order was queued before the open, the arrival price is the open of the session it filled in. Positive means worse for us. Read only. Output is aggregate basis points and counts, never a symbol or a dollar amount.
"""
import urllib.parse
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from consumer import cost_model as cm
from consumer import news_events as ne
from consumer import oversold_forward as of
from engine import benchmarks

MIN_N_FOR_VERDICT = 10
WARN_MULTIPLE, FAIL_MULTIPLE = 2.0, 3.0   # measured cost this many times the modeled cost
ET = ZoneInfo("America/New_York")


def reference_time(o):
    """When the price we measure against was set: the moment the order was sent, or the open of the session it filled in if it was queued before that open
    (orders sent after the close fill at the next open, and there is no market to measure against overnight)."""
    sent, filled = ne.parse_ts(o["submitted_at"]), ne.parse_ts(o["filled_at"] or o["submitted_at"])
    f_et = filled.astimezone(ET)
    open_utc = datetime.combine(f_et.date(), time(9, 30), tzinfo=ET).astimezone(timezone.utc)
    return max(sent, open_utc) if sent < open_utc else sent


def arrival_price(call, symbol, submitted_at):
    """Open of the first one-minute IEX bar at or after the submit time, or None (IEX prints sparsely for quiet names)."""
    t = ne.parse_ts(submitted_at).replace(second=0, microsecond=0)
    q = urllib.parse.urlencode({"symbols": symbol, "timeframe": "1Min", "start": t.isoformat().replace("+00:00", "Z"), "end": (t + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                                "limit": "1", "feed": "iex"})
    bars = (call("GET", "/v2/stocks/bars?" + q, host="data").get("bars") or {}).get(symbol) or []
    return float(bars[0]["o"]) if bars else None


def mode_of(o):
    """morning_limit (marketable limit sent about 10:00 ET) or market_at_open (the retired mode). Their costs differ by design and are judged separately."""
    return "morning_limit" if o.get("type") == "limit" else "market_at_open"


def _filled(o):
    return float(o.get("filled_qty") or 0) > 0


def entry_costs(orders, arrival=None, tag=of.TAG, stop_fraction=of.STOP_FRACTION):
    """One row per filled tagged entry. `arrival(symbol, submitted_at)` returns a price or None."""
    rows = []
    for o in orders:
        if not ((o.get("client_order_id") or "").startswith(tag + "-") and o.get("side") == "buy" and _filled(o)):
            continue
        fill = float(o["filled_avg_price"])
        stop_leg = next((l for l in (o.get("legs") or []) if l.get("side") == "sell" and l.get("stop_price")), None)
        row = {"vs_signal_close_bps": None, "vs_arrival_bps": None, "mode": mode_of(o)}
        if stop_leg:
            row["vs_signal_close_bps"] = cm.realized_slippage_bps("buy", float(stop_leg["stop_price"]) / stop_fraction, fill)
        px = arrival(o["symbol"], reference_time(o).isoformat()) if arrival and o.get("submitted_at") else None
        if px:
            row["vs_arrival_bps"] = cm.realized_slippage_bps("buy", px, fill)
        rows.append(row)
    return rows


def exit_costs(orders, arrival=None, tag=of.TAG):
    """Rule exits (time or signal) sent by us. Stop fills are the broker's, not an execution decision, and are counted apart."""
    rows = []
    for o in orders:
        if (o.get("client_order_id") or "").startswith(tag + "x-") and o.get("side") == "sell" and _filled(o):
            px = arrival(o["symbol"], reference_time(o).isoformat()) if arrival and o.get("submitted_at") else None
            rows.append({"vs_arrival_bps": cm.realized_slippage_bps("sell", px, float(o["filled_avg_price"])) if px else None, "mode": mode_of(o)})
    return rows


def fill_stats(orders, tag=of.TAG):
    """Of the orders we sent, how many filled fully, partly or not at all. Unfilled orders are an unmeasured opportunity cost, so they are shown, not hidden."""
    sent = [o for o in orders if (o.get("client_order_id") or "").startswith((tag + "-", tag + "x-"))]
    full = sum(1 for o in sent if float(o.get("filled_qty") or 0) >= float(o.get("qty") or 0) > 0)
    none = sum(1 for o in sent if float(o.get("filled_qty") or 0) == 0)
    return {"sent": len(sent), "filled": full, "partial": len(sent) - full - none, "unfilled": none, "fill_rate": round(full / len(sent), 3) if sent else None}


def verdict(entry_summary, modeled=benchmarks.S042["modeled_cost_bps_per_side"]):
    """OK / WARN / FAIL for the measured entry cost against the model. Needs enough fills to mean anything."""
    n = entry_summary.get("n", 0)
    if n < MIN_N_FOR_VERDICT:
        return "OK", f"{n} measured entries; a verdict needs {MIN_N_FOR_VERDICT}"
    m = entry_summary["mean_bps"]
    if m > FAIL_MULTIPLE * modeled:
        return "FAIL", f"mean entry cost {m:.1f} bps is more than {FAIL_MULTIPLE:.0f} times the modeled {modeled:.0f} bps: the backtest is too optimistic, owner decides on a pause"
    if m > WARN_MULTIPLE * modeled:
        return "WARN", f"mean entry cost {m:.1f} bps is more than {WARN_MULTIPLE:.0f} times the modeled {modeled:.0f} bps: refit the cost model"
    return "OK", f"mean entry cost {m:.1f} bps against a modeled {modeled:.0f} bps"


def analyze(call, now=None, days=30, tag=of.TAG):
    """Read the broker's closed orders for the last `days` and report the costs. Read only."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    closed = call("GET", "/v2/orders?status=closed&limit=500&nested=true&direction=desc")
    recent = [o for o in closed if o.get("submitted_at") and ne.parse_ts(o["submitted_at"]) >= since]
    cache = {}

    def arrival(sym, ts):
        key = (sym, ts[:16])
        if key not in cache:
            try:
                cache[key] = arrival_price(call, sym, ts)
            except Exception:
                cache[key] = None   # a data hiccup leaves that fill unmeasured; it never stops the report
        return cache[key]

    entries = entry_costs(recent, arrival, tag)
    exits = exit_costs(recent, arrival, tag)
    by_mode, order = {}, {"OK": 0, "WARN": 1, "FAIL": 2}
    for mode, modeled in benchmarks.S042["modeled_cost_bps_by_mode"].items():
        rows = [r for r in entries if r["mode"] == mode]
        summ = cm.summarize([r["vs_arrival_bps"] for r in rows if r["vs_arrival_bps"] is not None])
        st, d = verdict(summ, modeled)
        by_mode[mode] = {"entries_filled": len(rows), "entry_vs_arrival": summ, "status": st, "detail": d,
                         "entry_vs_signal_close": cm.summarize([r["vs_signal_close_bps"] for r in rows if r["vs_signal_close_bps"] is not None]),
                         "rule_exits_filled": sum(1 for r in exits if r["mode"] == mode),
                         "exit_vs_arrival": cm.summarize([r["vs_arrival_bps"] for r in exits if r["mode"] == mode and r["vs_arrival_bps"] is not None])}
    status = max((m["status"] for m in by_mode.values()), key=lambda x: order[x])
    detail = "; ".join(f"{mode}: {m['detail']}" for mode, m in by_mode.items() if m["entries_filled"]) or "no filled entries in the window"
    return {"window_days": days, "entries_filled": len(entries), "by_mode": by_mode, "fills": fill_stats(recent, tag),
            "history_may_be_truncated": len(closed) >= 500, "status": status, "detail": detail}
