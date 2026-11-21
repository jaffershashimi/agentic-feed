"""S-052: S-042 signals expressed as call debit spreads on the Alpaca PAPER account. A forward test, never pooled with the stock results.

Owner decisions 2026-11-17: copy the S-042 stock entries (not B55M first), one call debit spread per entry, at most 500 USD at risk per spread, at most 10 open.
Frozen rules (spec in the private hub, system/06-research/specs/S-052-s042-options-forward.md):
  * Same entries as the S-042 stock run that morning, only when the pre-trade gate said PASS. Same 09:50 to 11:30 ET window.
  * Expiry: the listed expiry nearest 37 days out, from 30 to 45 days. Long call: first listed strike at or above the stock price. Short call: first listed strike
    at or above 1.06 times the stock price. Contracts: as many as fit in 500 USD of net debit (1 to 10). If one contract costs more than 500 USD, the entry is skipped.
  * Quotes: every leg needs a two-sided quote with a bid-ask spread of at most 20 percent of the mid (consumer.alpaca_options.build_plan). Limits sit 25 percent of
    the half-spread past the mid, day orders, one multi-leg order.
  * Exits, checked every trading morning: lost half the premium, premium doubled, 5 trading days held (the S-042 time stop), or 5 days before expiry. Closing legs go out as limit orders.
  * Halt: no new spreads while the strategy's total result (closed plus open) is at or below the halt line (-1,500 USD, set by the owner).
Paper account only. Nothing here sends an order unless --submit is given. The log prints counts; names stay in the JSON file you redirect to.

  python3 -m consumer.s042_options run --plan PLAN.json [--submit]
  python3 -m consumer.s042_options manage [--submit]
  python3 -m consumer.s042_options report
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import alpaca_options as ao
from consumer import alpaca_paper as ap
from consumer import oversold_forward as of
from consumer.alpaca_paper import GuardError

ET = ZoneInfo("America/New_York")
TAG_PREFIX = "debit-vertical-o42-"      # starts with the structure name so consumer.alpaca_options.structure_tags still maps the legs
CLOSE_PREFIX = "close-o42-"
MAX_RISK_USD = 500.0                    # net debit per spread (owner decision 2026-11-17)
MAX_OPEN = 10                           # open spreads at once (owner decision 2026-11-17)
MAX_CONTRACTS = 10
TARGET_DTE = 37
DTE_RANGE = (30, 45)
SHORT_STRIKE_MULT = 1.06
HOLD_SESSIONS = of.HOLD_DAYS            # 5, same time stop as the stock strategy
HALT_USD = -1500.0                      # confirmed by the owner 2026-11-17: 30 percent of the 5,000 USD most that can be at risk
START_DATE = date(2026, 11, 20)         # inactive before this date, so the first live stock run is not disturbed by a second change
MAX_PREMIUM_NOTE = ao.MAX_TOTAL_PREMIUM_FRAC


def _why(exc: Exception, n: int = 24) -> str:
    """Short upper-case reason code from a GuardError message ('refused: premium ...' becomes 'PREMIUM_...')."""
    msg = str(exc).split(":", 1)[-1].strip()
    return msg[:n].upper().replace(" ", "_").replace(",", "")


def tag_for(root: str, day: date) -> str:
    return f"{TAG_PREFIX}{root}-{day.strftime('%y%m%d')}"


def is_ours(cid: str) -> bool:
    return (cid or "").startswith(TAG_PREFIX)


def entry_date(tag: str) -> date | None:
    try:
        return datetime.strptime(tag.rsplit("-", 1)[1], "%y%m%d").date()
    except (ValueError, IndexError):
        return None


def sessions_held(start: date, today: date) -> int:
    """Weekdays after `start` up to and including `today` (a holiday counts as a session, so an exit can come a day early, never late)."""
    n, d = 0, start
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def pick_strikes(contracts: list, spot: float, today: date) -> tuple | None:
    """contracts: option contract dicts (calls). Returns (expiry, long_strike, short_strike) or None."""
    by_exp = {}
    for c in contracts:
        if c.get("type") != "call" or c.get("tradable") is False:
            continue
        d = date.fromisoformat(c["expiration_date"])
        if DTE_RANGE[0] <= (d - today).days <= DTE_RANGE[1]:
            by_exp.setdefault(d, set()).add(float(c["strike_price"]))
    for d in sorted(by_exp, key=lambda e: (abs((e - today).days - TARGET_DTE), e)):
        strikes = sorted(by_exp[d])
        long_k = next((k for k in strikes if k >= spot), None)
        short_k = next((k for k in strikes if k >= spot * SHORT_STRIKE_MULT), None)
        if long_k is not None and short_k is not None and short_k > long_k:
            return d, long_k, short_k
    return None


def spot_price(call, root: str) -> float:
    return float(call("GET", f"/v2/stocks/{root}/trades/latest?feed=iex", host="data")["trade"]["p"])


def contracts_for(call, root: str, spot: float, today: date) -> list:
    q = {"underlying_symbols": root, "status": "active", "type": "call",
         "expiration_date_gte": (today + timedelta(days=DTE_RANGE[0])).isoformat(), "expiration_date_lte": (today + timedelta(days=DTE_RANGE[1])).isoformat(),
         "strike_price_gte": f"{spot * 0.97:.2f}", "strike_price_lte": f"{spot * 1.15:.2f}", "limit": "1000"}
    return call("GET", "/v2/options/contracts?" + urllib.parse.urlencode(q)).get("option_contracts", [])


def plan_spread(call, root: str, today: date, equity: float):
    """Build the spread plan for one underlying. Returns (plan, None) or (None, reason)."""
    spot = spot_price(call, root)
    pick = pick_strikes(contracts_for(call, root, spot, today), spot, today)
    if not pick:
        return None, "NO_LISTED_SPREAD"
    expiry, long_k, short_k = pick
    quotes_fn = lambda s: ao.get_quotes(call, s)
    try:
        one = ao.build_plan("debit-vertical", root, expiry, [long_k, short_k], 1, quotes_fn, today, 0, equity)
    except GuardError as exc:
        msg = str(exc)
        return None, "WIDE_OR_NO_QUOTE" if ("wider" in msg or "quote" in msg) else "REFUSED_" + _why(exc, 30)
    per = one["premium_usd"]
    qty = min(MAX_CONTRACTS, math.floor(MAX_RISK_USD / per)) if per > 0 else 0
    if qty < 1:
        return None, "TOO_EXPENSIVE"
    return ao.build_plan("debit-vertical", root, expiry, [long_k, short_k], qty, quotes_fn, today, 0, equity), None


def our_groups(call, state: dict | None = None):
    state = state or ao.book_state(call)
    tags = ao.structure_tags(call)
    groups = {}
    for p in state["held"]:
        if ao.is_option(p.get("symbol")) and is_ours(tags.get(p["symbol"], "")):
            groups.setdefault(tags[p["symbol"]], []).append(p)
    return state, groups


def open_pending_tags(state: dict) -> set:
    out = set()
    for o in state["orders"]:
        cid = o.get("client_order_id") or ""
        if is_ours(cid):
            out.add(cid)
    return out


def total_result_usd(call, groups: dict) -> float:
    """Closed result (from the broker's own orders) plus open unrealized."""
    realized = sum(t["pl_usd"] for t in closed_spreads(call))
    open_pl = sum(float(p.get("unrealized_pl") or 0) for g in groups.values() for p in g)
    return round(realized + open_pl, 2)


def closed_spreads(call) -> list:
    """One row per fully closed spread: opening net debit and closing proceeds, from filled orders."""
    orders = call("GET", "/v2/orders?status=closed&limit=500&nested=true&direction=desc")
    opens, closes = {}, {}
    for o in orders:
        cid = o.get("client_order_id") or ""
        if float(o.get("filled_qty") or 0) <= 0:
            continue
        if cid.startswith(TAG_PREFIX):
            opens[cid] = o
        elif cid.startswith(CLOSE_PREFIX):
            key = TAG_PREFIX + "-".join(cid[len(CLOSE_PREFIX):].split("-")[:2])
            closes.setdefault(key, []).append(o)
    rows = []
    for tag, o in opens.items():
        legs = closes.get(tag, [])
        if len(legs) < 2:
            continue
        qty = float(o["filled_qty"])
        debit = float(o.get("filled_avg_price") or 0) * 100 * qty
        proceeds = sum((1 if x.get("side") == "sell" else -1) * float(x.get("filled_avg_price") or 0) * 100 * float(x.get("filled_qty") or 0) for x in legs)
        limit_debit = float(o.get("limit_price") or 0) * 100 * qty
        rows.append({"tag": tag, "debit_usd": round(debit, 2), "pl_usd": round(proceeds - debit, 2), "ret": round((proceeds - debit) / debit, 4) if debit else None,
                     "fill_vs_limit_usd": round(limit_debit - debit, 2)})
    return rows


def run(call, plan: dict, now: datetime, state_dir: Path, submit: bool, entries_allowed: bool = True) -> dict:
    ap.check_not_halted(state_dir)
    today = now.astimezone(ET).date()
    out = {"asof": today.isoformat(), "submit": bool(submit), "considered": 0, "opened": 0, "skipped": {}, "open_now": 0, "halted": False, "orders": []}
    if today < START_DATE:
        out["note"] = f"S-052 starts {START_DATE.isoformat()}: nothing done"
        return out
    state, groups = our_groups(call)
    pending = open_pending_tags(state)
    out["open_now"] = len(groups) + len(pending - set(groups))
    result = total_result_usd(call, groups)
    out["total_result_usd"] = result
    if result <= HALT_USD:
        out["halted"] = True
        out["note"] = f"halt line {HALT_USD:,.0f} USD reached: no new spreads"
        return out
    if not entries_allowed:
        out["note"] = "entries not allowed this run"
        return out
    if not of.in_morning_window(now):
        out["note"] = "outside the 09:50 to 11:30 ET window"
        return out
    clock = call("GET", "/v2/clock")
    if submit and not clock.get("is_open"):
        raise GuardError("refused: the market is closed; option quotes are stale")
    held_roots = {ao.parse_occ(p["symbol"])[0] for g in groups.values() for p in g}
    for e in plan.get("entries", []):
        root = e.get("symbol") or e.get("sym")
        out["considered"] += 1
        skip = lambda why: out["skipped"].__setitem__(why, out["skipped"].get(why, 0) + 1)
        if not root or not ao.TICKER.match(root):
            skip("BAD_SYMBOL")
            continue
        if out["open_now"] >= MAX_OPEN:
            skip("AT_MAX_OPEN")
            continue
        tag = tag_for(root, today)
        if tag in pending or root in held_roots:
            skip("ALREADY_HAVE_ONE")
            continue
        try:
            spread, why = plan_spread(call, root, today, state["equity"])
            if spread is None:
                skip(why)
                continue
            ao.guard_open(spread, state, ao.structure_tags(call))
        except GuardError as exc:
            skip("GUARD_" + _why(exc))
            continue
        body = ao.order_body(spread, tag)
        rec = {"tag": tag, "debit_usd": spread["premium_usd"], "qty": spread["qty"], "expiry": spread["expiry"]}
        if submit:
            resp = call("POST", "/v2/orders", body)
            ap.log_event(state_dir, {"action": "s052-open", "tag": tag, "plan": spread, "order_id": resp.get("id"), "status": resp.get("status")})
            rec.update(order_id=resp.get("id"), status=resp.get("status"))
        else:
            rec["request"] = body
        out["orders"].append(rec)
        out["opened"] += 1
        out["open_now"] += 1
        state["orders"] = list(state["orders"]) + [{"client_order_id": tag, "symbol": None, "legs": [{"symbol": lg["symbol"]} for lg in spread["legs"]]}]
    return out


def decide_close(group: list, quotes: dict, tag: str, today: date) -> dict:
    d = ao.decide(group, quotes, today)
    start = entry_date(tag)
    if d["action"] == "HOLD" and start is not None and sessions_held(start, today) >= HOLD_SESSIONS:
        return {"action": "CLOSE", "reason": f"{HOLD_SESSIONS} trading days held (time stop)", "pl_pct": d.get("pl_pct")}
    return d


def manage(call, now: datetime, state_dir: Path, submit: bool) -> dict:
    ap.check_not_halted(state_dir)
    today = now.astimezone(ET).date()
    out = {"asof": today.isoformat(), "submit": bool(submit), "open": 0, "closing": 0, "orders": [], "skipped": 0}
    if today < START_DATE:
        out["note"] = f"S-052 starts {START_DATE.isoformat()}: nothing done"
        return out
    state, groups = our_groups(call)
    out["open"] = len(groups)
    if not groups:
        return out
    quotes = ao.get_quotes(call, [p["symbol"] for g in groups.values() for p in g])
    clock = call("GET", "/v2/clock")
    live = {o.get("client_order_id") for o in state["orders"]}
    halted = of.halt_status(call)["halted"]   # owner decision 2026-11-21: the S-042 halt also closes this strategy's spreads, and nothing else
    out["halted"] = halted
    for tag, group in sorted(groups.items()):
        d = decide_close(group, quotes, tag, today)
        if halted and d["action"] != "CLOSE":
            d = {"action": "CLOSE", "reason": "S042_HALT", "pl_pct": d.get("pl_pct")}
        if d["action"] != "CLOSE":
            continue
        out["closing"] += 1
        for p in group:
            qty = int(abs(float(p["qty"])))
            side = "sell" if float(p["qty"]) > 0 else "buy"
            q = quotes.get(p["symbol"], {})
            cid = f"{CLOSE_PREFIX}{tag[len(TAG_PREFIX):]}-{'L' if side == 'sell' else 'S'}-{today.strftime('%m%d')}"
            if cid in live:
                continue
            if not (q.get("bid") and q.get("ask")):
                out["skipped"] += 1
                continue
            half = q["ask"] - q["bid"]
            px = round(q["mid"] - 0.25 * half if side == "sell" else q["mid"] + 0.25 * half, 2)
            body = {"symbol": p["symbol"], "qty": str(qty), "side": side, "type": "limit", "limit_price": f"{px:.2f}", "time_in_force": "day",
                    "position_intent": "sell_to_close" if side == "sell" else "buy_to_close", "client_order_id": cid}
            rec = {"tag": tag, "reason": d["reason"], "side": side}
            if submit and clock.get("is_open"):
                resp = call("POST", "/v2/orders", body)
                ap.log_event(state_dir, {"action": "s052-close", "tag": tag, "reason": d["reason"], "order_id": resp.get("id")})
                rec.update(order_id=resp.get("id"), status=resp.get("status"))
            else:
                rec["request"] = body
            out["orders"].append(rec)
    return out


def report(call) -> dict:
    state, groups = our_groups(call)
    rows = closed_spreads(call)
    n = len(rows)
    pl = sum(r["pl_usd"] for r in rows)
    rets = [r["ret"] for r in rows if r["ret"] is not None]
    return {"closed_spreads": n, "open_spreads": len(groups), "closed_pl_usd": round(pl, 2), "open_unrealized_usd": round(sum(float(p.get("unrealized_pl") or 0) for g in groups.values() for p in g), 2),
            "wins": sum(1 for r in rows if r["pl_usd"] > 0), "mean_return_on_premium_pct": round(100 * sum(rets) / len(rets), 2) if rets else None,
            "mean_fill_vs_limit_usd": round(sum(r["fill_vs_limit_usd"] for r in rows) / n, 2) if n else None, "halt_line_usd": HALT_USD, "start_date": START_DATE.isoformat()}


def entries_from(plan: dict, sent: dict | None) -> dict:
    """The names to copy. After a real S-042 run: only the stock buys that were actually sent (a stock skipped for a wide quote gets no spread).
    In a dry run (no sent file): the planned entries."""
    if sent is None:
        return plan
    return {"entries": [{"symbol": o["sym"]} for o in sent.get("orders", []) if o.get("side") == "buy" and o.get("sym")]}


def counts(result: dict) -> dict:
    """What goes in a GitHub log: counts only, no names."""
    keys = ("asof", "submit", "considered", "opened", "skipped", "open_now", "halted", "open", "closing", "note")
    return {k: result[k] for k in keys if k in result}


def main(argv=None, call=None, now=None) -> int:
    p = argparse.ArgumentParser(description="S-052: S-042 signals as call debit spreads (paper forward test).")
    p.add_argument("--state-dir", default=os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper"))
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--sent", help="result of the S-042 execution step: copy only the stock buys that were sent")
    r.add_argument("--submit", action="store_true")
    r.add_argument("--out", help="write the full result (it names stocks) to this file and print counts only")
    m = sub.add_parser("manage")
    m.add_argument("--submit", action="store_true")
    m.add_argument("--out")
    sub.add_parser("report")
    a = p.parse_args(argv)
    try:
        call = call or ap.make_client()
        now = now or datetime.now(timezone.utc)
        if a.cmd == "report":
            result = report(call)
        elif a.cmd == "manage":
            result = manage(call, now, Path(a.state_dir), a.submit)
        else:
            sent = json.loads(Path(a.sent).read_text()) if a.sent else None
            result = run(call, entries_from(json.loads(Path(a.plan).read_text()), sent), now, Path(a.state_dir), a.submit)
    except GuardError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    if getattr(a, "out", None):
        Path(a.out).write_text(json.dumps(result, indent=1, default=str))
        print(json.dumps(counts(result), sort_keys=True, default=str))
    else:
        print(json.dumps(result, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
