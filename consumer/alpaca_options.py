"""Defined-risk option structures on the Alpaca PAPER account, with hard guardrails (paper host only).

Builds on consumer/alpaca_paper.py (same paper-host check, kill switch and log). Results are BROKER_PAPER: a separate class that
never pools with PAPER, REPLAY or SYNTHETIC and never counts toward go-live.

Structures (one set of legs, qty sets the multiple):
  long-call, long-put     buy one option
  straddle                buy a call and a put at one strike and expiry
  strangle                buy a put below and a call above spot, one expiry
  debit-vertical          buy one strike, sell another of the same type and expiry (call: buy low, sell high; put: buy high, sell low)
  covered-call            sell a call against 100 shares per contract already held
  cash-secured-put        sell a put with strike x 100 x qty of cash set aside

Guardrails enforced in code (a refusal raises GuardError and nothing is sent):
  * Defined risk: long call, long put, straddle, strangle and debit vertical cost at most 1,000 USD in total premium (that is the
    maximum loss). Covered calls and cash-secured puts are capital trades: capital (100 shares or strike x 100) at most 25 percent
    of account equity per trade, 50 percent in total. Their loss is not capped at 1,000; it is stated in the plan.
  * No naked short options, no credit spreads, no 0DTE: expiry between 7 and 60 days out.
  * Liquidity: every leg needs a bid and an ask, and bid-ask spread at most 20 percent of the mid.
  * Total premium at risk across open long structures at most 5 percent of equity. At most 6 open structures.
  * Limit orders only, day orders, limit inside the bid-ask range (nearer the mid than the far side).
  * Underlying must be a plain ticker whose contracts are listed and tradable.
Commands print JSON and never a key: chain, open, manage, book. `open` and `manage` are dry runs unless --submit.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import sys
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer.alpaca_paper import GuardError

MAX_LOSS_USD = 1000.0
MAX_CAPITAL_FRAC = 0.25
MAX_TOTAL_CAPITAL_FRAC = 0.50
MAX_TOTAL_PREMIUM_FRAC = 0.05
MAX_OPEN_STRUCTURES = 6
MIN_DTE, MAX_DTE = 7, 60
MAX_SPREAD_PCT = 0.20
STOP_LOSS_PCT = -0.50       # debit structures: close at a 50 percent loss of premium
TARGET_PCT = 1.00           # debit structures: close at +100 percent of premium
SHORT_STOP_MULT = 2.5       # short options: buy to close when the option costs 2.5x the credit
SHORT_TARGET_FRAC = 0.20    # short options: buy to close when it costs 20 percent of the credit
CLOSE_DTE_LONG, CLOSE_DTE_SHORT = 5, 3
STRUCTURES = ("long-call", "long-put", "straddle", "strangle", "debit-vertical", "covered-call", "cash-secured-put")
OCC = re.compile(r"^([A-Z]{1,5})(\d{6})([CP])(\d{8})$")
TICKER = re.compile(r"^[A-Z]{1,5}$")


def parse_occ(sym: str):
    m = OCC.match(sym)
    if not m:
        raise GuardError(f"refused: {sym!r} is not an option symbol")
    root, ymd, cp, k = m.groups()
    return root, datetime.strptime(ymd, "%y%m%d").date(), cp, int(k) / 1000.0


def occ(root: str, expiry: date, cp: str, strike: float) -> str:
    return f"{root}{expiry.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


def dte(sym: str, today: date) -> int:
    return (parse_occ(sym)[1] - today).days


def get_quotes(call, symbols) -> dict:
    out = {}
    syms = sorted(set(symbols))
    for i in range(0, len(syms), 100):
        j = call("GET", "/v1beta1/options/snapshots?" + urllib.parse.urlencode({"symbols": ",".join(syms[i:i + 100])}), host="data")
        for s, snap in (j.get("snapshots") or {}).items():
            q = snap.get("latestQuote") or {}
            bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
            g = snap.get("greeks") or {}
            out[s] = {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 4) if bid and ask else 0.0,
                      "delta": g.get("delta"), "iv": snap.get("impliedVolatility")}
    return out


def leg_limit(q: dict, side: str) -> float:
    """Limit price inside the quote: 25 percent of the way from the mid toward the far side."""
    bid, ask = q["bid"], q["ask"]
    if not (bid > 0 and ask > 0 and ask >= bid):
        raise GuardError("refused: a leg has no two-sided quote")
    mid = (bid + ask) / 2
    if (ask - bid) / mid > MAX_SPREAD_PCT:
        raise GuardError(f"refused: bid-ask spread {100 * (ask - bid) / mid:.0f}% of mid is wider than {MAX_SPREAD_PCT * 100:.0f}%")
    px = mid + 0.25 * (ask - bid) if side == "buy" else mid - 0.25 * (ask - bid)
    return round(px + 1e-9, 2)


def build_plan(structure: str, root: str, expiry: date, strikes: list, qty: int, quotes_fn, today: date, shares_held: int = 0, equity: float = 0.0) -> dict:
    if structure not in STRUCTURES:
        raise GuardError(f"refused: unknown structure {structure!r}")
    if not TICKER.match(root):
        raise GuardError(f"refused: {root!r} is not a plain ticker")
    if not (isinstance(qty, int) and 1 <= qty <= 100):
        raise GuardError("refused: qty must be a whole number from 1 to 100")
    days = (expiry - today).days
    if not MIN_DTE <= days <= MAX_DTE:
        raise GuardError(f"refused: expiry is {days} days out; allowed {MIN_DTE} to {MAX_DTE}")
    need = {"long-call": (1, ["C"]), "long-put": (1, ["P"]), "straddle": (1, ["C", "P"]), "strangle": (2, ["P", "C"]),
            "debit-vertical": (2, None), "covered-call": (1, ["C"]), "cash-secured-put": (1, ["P"])}[structure]
    if len(strikes) != need[0]:
        raise GuardError(f"refused: {structure} needs {need[0]} strike(s)")
    kind = None
    if structure == "debit-vertical":
        kind = "C" if strikes[0] < strikes[1] else "P"
        if strikes[0] == strikes[1]:
            raise GuardError("refused: the two strikes must differ")
        legs = [(occ(root, expiry, kind, strikes[0]), "buy"), (occ(root, expiry, kind, strikes[1]), "sell")]
    elif structure == "straddle":
        legs = [(occ(root, expiry, "C", strikes[0]), "buy"), (occ(root, expiry, "P", strikes[0]), "buy")]
    elif structure == "strangle":
        if not strikes[0] < strikes[1]:
            raise GuardError("refused: a strangle needs the put strike below the call strike (give put strike first)")
        legs = [(occ(root, expiry, "P", strikes[0]), "buy"), (occ(root, expiry, "C", strikes[1]), "buy")]
    elif structure in ("covered-call", "cash-secured-put"):
        legs = [(occ(root, expiry, need[1][0], strikes[0]), "sell")]
    else:
        legs = [(occ(root, expiry, need[1][0], strikes[0]), "buy")]
    quotes = quotes_fn([s for s, _ in legs])
    limits = []
    for s, side in legs:
        if s not in quotes:
            raise GuardError(f"refused: no quote for {s}")
        limits.append(leg_limit(quotes[s], side))
    net = sum(p if side == "buy" else -p for (s, side), p in zip(legs, limits))
    plan = {"structure": structure, "underlying": root, "expiry": expiry.isoformat(), "qty": qty,
            "legs": [{"symbol": s, "side": side, "limit": p} for (s, side), p in zip(legs, limits)], "net_limit": round(net, 2)}
    if structure in ("covered-call", "cash-secured-put"):
        if net >= 0:
            raise GuardError("refused: a short option must bring in a credit")
        strike = strikes[0]
        capital = 100.0 * qty * (strike if structure == "cash-secured-put" else 0.0)
        if structure == "covered-call":
            if shares_held < 100 * qty:
                raise GuardError(f"refused: a covered call needs {100 * qty} shares held, found {shares_held}")
        else:
            if not equity or capital > MAX_CAPITAL_FRAC * equity:
                raise GuardError(f"refused: cash set aside {capital:,.0f} exceeds {MAX_CAPITAL_FRAC * 100:.0f}% of equity")
        plan.update(credit_usd=round(-net * 100 * qty, 2), capital_usd=round(capital, 2),
                    max_loss_usd="not capped at 1,000: " + ("shares can fall; stop on the shares" if structure == "covered-call" else "stock can fall to zero below the strike, offset by the credit"))
    else:
        cost = net * 100 * qty
        if net <= 0:
            raise GuardError("refused: a debit structure must have a positive net cost")
        if cost > MAX_LOSS_USD:
            raise GuardError(f"refused: premium {cost:,.0f} USD is more than the {MAX_LOSS_USD:,.0f} USD maximum loss")
        plan.update(max_loss_usd=round(cost, 2), premium_usd=round(cost, 2))
        if structure == "debit-vertical":
            width = abs(strikes[1] - strikes[0])
            if net >= width:
                raise GuardError("refused: the debit is at least the strike width, so there is no profit potential")
            plan["max_profit_usd"] = round((width - net) * 100 * qty, 2)
    return plan


def order_body(plan: dict, tag: str) -> dict:
    legs = plan["legs"]
    intent = lambda side: "buy_to_open" if side == "buy" else "sell_to_open"
    if len(legs) == 1:
        lg = legs[0]
        return {"symbol": lg["symbol"], "qty": str(plan["qty"]), "side": lg["side"], "type": "limit", "limit_price": f"{lg['limit']:.2f}",
                "time_in_force": "day", "position_intent": intent(lg["side"]), "client_order_id": tag}
    return {"order_class": "mleg", "qty": str(plan["qty"]), "type": "limit", "limit_price": f"{plan['net_limit']:.2f}", "time_in_force": "day",
            "client_order_id": tag,
            "legs": [{"symbol": lg["symbol"], "ratio_qty": "1", "side": lg["side"], "position_intent": intent(lg["side"])} for lg in legs]}


def new_tag(structure: str, root: str, today: date) -> str:
    return f"{structure}-{root}-{today.strftime('%y%m%d')}-{secrets.token_hex(2)}"[:48]


def is_option(sym: str) -> bool:
    return bool(OCC.match(sym or ""))


def book_state(call) -> dict:
    acct = call("GET", "/v2/account")
    positions = call("GET", "/v2/positions")
    orders = call("GET", "/v2/orders?status=open&limit=100&nested=true")
    return {"equity": float(acct.get("equity") or 0), "held": positions, "orders": orders}


def structure_tags(call) -> dict:
    """Map option symbol -> tag of the filled opening order that created it (client_order_id survives on the broker)."""
    tags = {}
    for o in call("GET", "/v2/orders?status=closed&limit=500&nested=true&direction=desc"):
        cid = o.get("client_order_id") or ""
        if not any(cid.startswith(s + "-") for s in STRUCTURES):
            continue
        for lg in (o.get("legs") or [o]):
            if lg.get("symbol") and lg["symbol"] not in tags and float(lg.get("filled_qty") or 0) > 0:
                tags[lg["symbol"]] = cid
    return tags


def guard_open(plan: dict, state: dict, tags: dict) -> None:
    equity = state["equity"]
    open_tags = {tags.get(p["symbol"]) for p in state["held"] if is_option(p.get("symbol"))} - {None}
    open_orders = {o.get("client_order_id") for o in state["orders"] if is_option((o.get("symbol") or (o.get("legs") or [{}])[0].get("symbol", "")))}
    if len(open_tags | open_orders) >= MAX_OPEN_STRUCTURES:
        raise GuardError(f"refused: {len(open_tags | open_orders)} structures are already open (limit {MAX_OPEN_STRUCTURES})")
    if any(p["symbol"] in {lg["symbol"] for lg in plan["legs"]} for p in state["held"]):
        raise GuardError("refused: a leg of this structure is already held")
    premium = sum(abs(float(p.get("cost_basis") or 0)) for p in state["held"] if is_option(p.get("symbol")) and float(p.get("qty") or 0) > 0)
    if "premium_usd" in plan and premium + plan["premium_usd"] > MAX_TOTAL_PREMIUM_FRAC * equity:
        raise GuardError(f"refused: total premium at risk would be {premium + plan['premium_usd']:,.0f} USD, over {MAX_TOTAL_PREMIUM_FRAC * 100:.0f}% of equity")
    if "capital_usd" in plan:
        held = sum(abs(float(p.get("market_value") or 0)) for p in state["held"] if not is_option(p.get("symbol")))
        if plan["capital_usd"] and plan["capital_usd"] + held > MAX_TOTAL_CAPITAL_FRAC * equity:
            raise GuardError("refused: capital in use would exceed the total limit")
        if plan["structure"] == "covered-call" and plan["capital_usd"] > MAX_CAPITAL_FRAC * equity:
            raise GuardError("refused: capital too large")


def cmd_chain(call, args, state_dir: Path, today: date) -> dict:
    root = args.symbol.strip().upper()
    if not TICKER.match(root):
        raise GuardError("refused: not a plain ticker")
    lo, hi = args.min_dte or MIN_DTE, args.max_dte or MAX_DTE
    spot = float(call("GET", f"/v2/stocks/{root}/trades/latest?feed=iex", host="data")["trade"]["p"]) if not args.spot else args.spot
    q = {"underlying_symbols": root, "status": "active", "expiration_date_gte": date.fromordinal(today.toordinal() + lo).isoformat(),
         "expiration_date_lte": date.fromordinal(today.toordinal() + hi).isoformat(), "strike_price_gte": f"{spot * (1 - args.near):.2f}",
         "strike_price_lte": f"{spot * (1 + args.near):.2f}", "limit": "1000"}
    cs = call("GET", "/v2/options/contracts?" + urllib.parse.urlencode(q)).get("option_contracts", [])
    quotes = get_quotes(call, [c["symbol"] for c in cs][:300])
    rows = [{"symbol": c["symbol"], "expiry": c["expiration_date"], "type": c["type"], "strike": float(c["strike_price"]), **quotes.get(c["symbol"], {})}
            for c in cs]
    return {"underlying": root, "spot": spot, "contracts": sorted(rows, key=lambda r: (r["expiry"], r["strike"], r["type"]))[:args.limit]}


def cmd_open(call, args, state_dir: Path, today: date) -> dict:
    ap.check_not_halted(state_dir)
    root = args.symbol.strip().upper()
    state = book_state(call)
    shares = 0
    for p in state["held"]:
        if p.get("symbol") == root:
            shares = int(float(p.get("qty") or 0))
    plan = build_plan(args.structure, root, date.fromisoformat(args.expiry), [float(k) for k in args.strikes.split(",")], args.qty,
                      lambda s: get_quotes(call, s), today, shares, state["equity"])
    guard_open(plan, state, structure_tags(call))
    clock = call("GET", "/v2/clock")
    tag = new_tag(args.structure, root, today)
    body = order_body(plan, tag)
    out = {"submit": bool(args.submit), "market_open": bool(clock.get("is_open")), "plan": plan, "tag": tag, "request": body}
    if not args.submit:
        out["note"] = "dry run: nothing was sent"
        return out
    if not clock.get("is_open"):
        raise GuardError("refused: the market is closed; options quotes are stale, so no order is sent")
    resp = call("POST", "/v2/orders", body)
    ap.log_event(state_dir, {"action": "opt-open", "tag": tag, "plan": plan, "order_id": resp.get("id"), "status": resp.get("status")})
    out.update(order_id=resp.get("id"), status=resp.get("status"))
    return out


def decide(group: list, quotes: dict, today: date) -> dict:
    """Pure decision for one structure: group = open positions sharing a tag. Returns {action, reason, pl_pct}."""
    longs = [p for p in group if float(p["qty"]) > 0]
    shorts = [p for p in group if float(p["qty"]) < 0]
    min_dte = min(dte(p["symbol"], today) for p in group)
    if shorts and not longs:
        credit = sum(abs(float(p["avg_entry_price"])) * abs(float(p["qty"])) for p in shorts)
        cost_now = sum(quotes.get(p["symbol"], {}).get("mid", 0) * abs(float(p["qty"])) for p in shorts)
        if not cost_now:
            return {"action": "HOLD", "reason": "no quote", "pl_pct": None}
        pl = (credit - cost_now) / credit
        if cost_now >= SHORT_STOP_MULT * credit:
            return {"action": "CLOSE", "reason": "short option at 2.5x the credit", "pl_pct": round(pl, 3)}
        if cost_now <= SHORT_TARGET_FRAC * credit:
            return {"action": "CLOSE", "reason": "kept 80 percent of the credit", "pl_pct": round(pl, 3)}
        if min_dte <= CLOSE_DTE_SHORT:
            return {"action": "CLOSE", "reason": f"{min_dte} days to expiry on a short option", "pl_pct": round(pl, 3)}
        return {"action": "HOLD", "reason": "inside the rules", "pl_pct": round(pl, 3)}
    paid = sum(abs(float(p["avg_entry_price"])) * float(p["qty"]) for p in longs) - sum(abs(float(p["avg_entry_price"])) * abs(float(p["qty"])) for p in shorts)
    now = sum(quotes.get(p["symbol"], {}).get("mid", 0) * float(p["qty"]) for p in longs) - sum(quotes.get(p["symbol"], {}).get("mid", 0) * abs(float(p["qty"])) for p in shorts)
    if paid <= 0 or not any(quotes.get(p["symbol"], {}).get("mid") for p in group):
        return {"action": "HOLD", "reason": "no usable quote", "pl_pct": None}
    pl = (now - paid) / paid
    if pl <= STOP_LOSS_PCT:
        return {"action": "CLOSE", "reason": "lost half the premium", "pl_pct": round(pl, 3)}
    if pl >= TARGET_PCT:
        return {"action": "CLOSE", "reason": "premium doubled", "pl_pct": round(pl, 3)}
    if min_dte <= CLOSE_DTE_LONG:
        return {"action": "CLOSE", "reason": f"{min_dte} days to expiry", "pl_pct": round(pl, 3)}
    return {"action": "HOLD", "reason": "inside the rules", "pl_pct": round(pl, 3)}


def cmd_manage(call, args, state_dir: Path, today: date) -> dict:
    ap.check_not_halted(state_dir)
    state = book_state(call)
    tags = structure_tags(call)
    opt_pos = [p for p in state["held"] if is_option(p.get("symbol"))]
    quotes = get_quotes(call, [p["symbol"] for p in opt_pos]) if opt_pos else {}
    groups = {}
    for p in opt_pos:
        groups.setdefault(tags.get(p["symbol"], "UNTAGGED-" + p["symbol"]), []).append(p)
    clock = call("GET", "/v2/clock")
    out = {"submit": bool(args.submit), "market_open": bool(clock.get("is_open")), "structures": []}
    for tag, group in sorted(groups.items()):
        d = decide(group, quotes, today)
        item = {"tag": tag, "legs": [p["symbol"] for p in group], **d, "orders": []}
        if d["action"] == "CLOSE":
            for p in group:
                qty = abs(float(p["qty"]))
                side = "sell" if float(p["qty"]) > 0 else "buy"
                q = quotes.get(p["symbol"], {})
                if not (q.get("bid") and q.get("ask")):
                    item["orders"].append({"symbol": p["symbol"], "skipped": "no two-sided quote"})
                    continue
                px = round(q["mid"] - 0.25 * (q["ask"] - q["bid"]) if side == "sell" else q["mid"] + 0.25 * (q["ask"] - q["bid"]), 2)
                body = {"symbol": p["symbol"], "qty": str(int(qty)), "side": side, "type": "limit", "limit_price": f"{px:.2f}", "time_in_force": "day",
                        "position_intent": "sell_to_close" if side == "sell" else "buy_to_close"}
                entry = {"request": body}
                if args.submit and clock.get("is_open"):
                    resp = call("POST", "/v2/orders", body)
                    entry["order_id"], entry["status"] = resp.get("id"), resp.get("status")
                    ap.log_event(state_dir, {"action": "opt-close", "tag": tag, "reason": d["reason"], "order_id": resp.get("id")})
                item["orders"].append(entry)
        out["structures"].append(item)
    return out


def cmd_book(call, args, state_dir: Path, today: date) -> dict:
    state = book_state(call)
    tags = structure_tags(call)
    rows = [{"symbol": p["symbol"], "qty": p.get("qty"), "avg_entry": p.get("avg_entry_price"), "tag": tags.get(p["symbol"]),
             "unrealized_pl": p.get("unrealized_pl")} for p in state["held"]]
    return {"open_positions": rows, "open_orders": [{"tag": o.get("client_order_id"), "status": o.get("status"), "symbol": o.get("symbol")} for o in state["orders"]]}


COMMANDS = dict(chain=cmd_chain, open=cmd_open, manage=cmd_manage, book=cmd_book)


def main(argv=None, call=None, today: date | None = None) -> int:
    p = argparse.ArgumentParser(description="Defined-risk option structures on the Alpaca PAPER account.")
    p.add_argument("--state-dir", default=os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper"))
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chain")
    c.add_argument("symbol")
    c.add_argument("--min-dte", type=int, default=0)
    c.add_argument("--max-dte", type=int, default=0)
    c.add_argument("--near", type=float, default=0.06, help="strike window as a fraction of spot")
    c.add_argument("--spot", type=float, default=0.0)
    c.add_argument("--limit", type=int, default=60)
    o = sub.add_parser("open")
    o.add_argument("structure", choices=STRUCTURES)
    o.add_argument("symbol")
    o.add_argument("--expiry", required=True)
    o.add_argument("--strikes", required=True, help="comma separated, in the order the structure needs")
    o.add_argument("--qty", type=int, default=1)
    o.add_argument("--submit", action="store_true")
    m = sub.add_parser("manage")
    m.add_argument("--submit", action="store_true")
    sub.add_parser("book")
    args = p.parse_args(argv)
    try:
        if call is None:
            call = ap.make_client()
        result = COMMANDS[args.cmd](call, args, Path(args.state_dir), today or datetime.now(timezone.utc).date())
    except GuardError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(result, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
