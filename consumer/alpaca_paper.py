"""Alpaca PAPER order tool with hard guardrails (paper account only, stocks only).

Why: the desk ledger records fills the desk assumed (ask at the quote time). A broker paper account gives real
simulated fills, so slippage and missed fills become measurable. Results from this tool are a separate class
(BROKER_PAPER). They never count toward the go-live gates and never enter the paper ledger.

Guardrails the tool enforces in code (a refused order raises GuardError and nothing is sent):
  * Paper host only: the base URL host must be exactly paper-api.alpaca.markets over https and ALPACA_PAPER_TRADE must
    not be false. A live host is refused before any request is built.
  * Orders are a dry run unless --submit is passed. A dry run prints the exact request and sends nothing.
  * Stocks only: the symbol must be an active, tradable us_equity asset. No options, no shorting (side is always buy
    to open; closing uses the position close endpoint).
  * At most 3 open positions and at most 3 open (unfilled) entry orders in total, one position per symbol.
  * At most 1,000 USD per order (quantity is a whole number of shares, rounded down from the notional limit).
  * Every entry is an OTO order: market buy for the day with a stop-loss child at 20 percent below the reference
    price (the R2 stop). Entries are refused if the stop cannot be placed (price unknown).
  * Kill switch: a file named HALT in the state directory, or `halt`, stops every order command; `halt` also cancels
    open orders and (with --flatten) closes positions. `resume` removes the file.
  * Every request that changes anything is appended to a local JSONL log (no keys).

Commands (all print JSON, none prints a key): status, preflight, buy, positions, orders, fills, close, halt, resume.
Credentials come from the environment (ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER_BASE_URL). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAPER_HOST = "paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
MAX_OPEN_POSITIONS = 3
MAX_OPEN_ENTRY_ORDERS = 3
MAX_ORDER_USD = 1000.0
STOP_FRACTION = 0.20
SYMBOL = re.compile(r"^[A-Z]{1,5}$")


class GuardError(Exception):
    """A guardrail refused the action. Nothing was sent."""


def paper_base(env=os.environ) -> str:
    base = (env.get("ALPACA_PAPER_BASE_URL") or f"https://{PAPER_HOST}").strip().rstrip("/")
    if base.endswith("/v2"):
        base = base[:-3]
    parts = urllib.parse.urlparse(base)
    if parts.scheme != "https" or parts.hostname != PAPER_HOST or parts.port not in (None, 443) or parts.path not in ("", "/"):
        raise GuardError("refused: the base URL must be exactly https://paper-api.alpaca.markets (paper account only)")
    if str(env.get("ALPACA_PAPER_TRADE", "true")).strip().lower() in ("false", "0", "no"):
        raise GuardError("refused: ALPACA_PAPER_TRADE is false; this tool only trades the paper account")
    return base


def make_client(env=os.environ):
    """Return call(method, path, body=None, host='paper') -> parsed JSON. Only the paper and data hosts are reachable."""
    key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
    if not key or not secret or key.startswith("PASTE_"):
        raise GuardError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in the environment")
    base = paper_base(env)
    data_base = (env.get("ALPACA_DATA_BASE_URL") or DATA_URL).rstrip("/")
    if urllib.parse.urlparse(data_base).hostname != "data.alpaca.markets":
        raise GuardError("refused: ALPACA_DATA_BASE_URL must be https://data.alpaca.markets")
    cafile = "/root/.ccr/ca-bundle.crt"
    ctx = ssl.create_default_context(cafile=cafile if os.path.exists(cafile) else None)
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json"}

    def call(method: str, path: str, body=None, host: str = "paper"):
        if method not in ("GET", "POST", "DELETE"):
            raise GuardError("refused: unsupported method")
        if host == "data" and method != "GET":
            raise GuardError("refused: the data host is read only")
        url = (base if host == "paper" else data_base) + path
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace")[:300]
                if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise GuardError(f"HTTP {exc.code} from {host} API: {text}") from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if method == "GET" and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise GuardError(f"network error: {exc}") from None
        raise GuardError("unreachable")
    return call


def halt_file(state_dir: Path) -> Path:
    return Path(state_dir) / "HALT"


def check_not_halted(state_dir: Path) -> None:
    if halt_file(state_dir).exists():
        raise GuardError("refused: HALT is set (kill switch); run `resume` after you have looked at why")


def log_event(state_dir: Path, event: dict) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with open(Path(state_dir) / "orders.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def build_entry(symbol: str, ref_price: float, max_usd: float = MAX_ORDER_USD) -> dict:
    symbol = symbol.strip().upper()
    if not SYMBOL.match(symbol):
        raise GuardError(f"refused: {symbol!r} is not a plain stock ticker")
    if not (ref_price and ref_price > 0 and math.isfinite(ref_price)):
        raise GuardError("refused: a positive reference price is required (it sets the quantity and the stop)")
    if max_usd <= 0 or max_usd > MAX_ORDER_USD:
        raise GuardError(f"refused: the per-order limit cannot exceed {MAX_ORDER_USD:.0f} USD")
    qty = math.floor(max_usd / ref_price)
    if qty < 1:
        raise GuardError(f"refused: one share of {symbol} at {ref_price:.2f} costs more than {max_usd:.0f} USD")
    stop = round(ref_price * (1 - STOP_FRACTION), 2)
    return {"symbol": symbol, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "day",
            "order_class": "oto", "stop_loss": {"stop_price": f"{stop:.2f}"}}


def guard_entry(call, symbol: str) -> None:
    clock = call("GET", "/v2/clock")
    if not clock.get("is_open"):
        raise GuardError("refused: the market is closed (a market order would queue for the next open)")
    asset = call("GET", f"/v2/assets/{symbol}")
    if asset.get("class") != "us_equity" or not asset.get("tradable") or asset.get("status") != "active":
        raise GuardError(f"refused: {symbol} is not an active, tradable us_equity asset")
    positions = call("GET", "/v2/positions")
    if len(positions) >= MAX_OPEN_POSITIONS:
        raise GuardError(f"refused: {len(positions)} positions are open (limit {MAX_OPEN_POSITIONS})")
    if any(p.get("symbol") == symbol for p in positions):
        raise GuardError(f"refused: a {symbol} position is already open")
    orders = call("GET", "/v2/orders?status=open&limit=100")
    entries = [o for o in orders if o.get("side") == "buy" and o.get("parent_order_id") in (None, "")]
    if len(positions) + len(entries) >= MAX_OPEN_POSITIONS or len(entries) >= MAX_OPEN_ENTRY_ORDERS:
        raise GuardError("refused: open positions plus unfilled entry orders would exceed the limit")
    if any(o.get("symbol") == symbol for o in entries):
        raise GuardError(f"refused: an unfilled {symbol} entry order is already open")


def latest_price(call, symbol: str) -> float:
    q = call("GET", f"/v2/stocks/{symbol}/trades/latest?feed=iex", host="data")
    return float(q["trade"]["p"])


def cmd_buy(call, args, state_dir: Path) -> dict:
    check_not_halted(state_dir)
    ref = args.ref_price if args.ref_price else latest_price(call, args.symbol.strip().upper())
    order = build_entry(args.symbol, ref, args.max_usd)
    guard_entry(call, order["symbol"])
    out = {"submit": bool(args.submit), "reference_price": ref, "request": order}
    if not args.submit:
        out["note"] = "dry run: nothing was sent; add --submit to place this paper order"
        return out
    resp = call("POST", "/v2/orders", order)
    log_event(state_dir, {"action": "buy", "request": order, "reference_price": ref, "order_id": resp.get("id"), "status": resp.get("status")})
    out["order_id"] = resp.get("id")
    out["status"] = resp.get("status")
    return out


def summarize_order(o: dict) -> dict:
    keys = ("id", "symbol", "side", "type", "order_class", "qty", "filled_qty", "filled_avg_price", "status",
            "submitted_at", "filled_at", "stop_price", "parent_order_id")
    s = {k: o.get(k) for k in keys}
    s["legs"] = [summarize_order(x) for x in (o.get("legs") or [])]
    return s


def cmd_status(call, args, state_dir: Path) -> dict:
    acct = call("GET", "/v2/account")
    clock = call("GET", "/v2/clock")
    return {"account": {k: acct.get(k) for k in ("status", "currency", "trading_blocked", "account_blocked", "shorting_enabled")},
            "paper_base": "paper-api.alpaca.markets", "market_open": clock.get("is_open"), "next_open": clock.get("next_open"),
            "halted": halt_file(state_dir).exists()}


def cmd_preflight(call, args, state_dir: Path) -> dict:
    out = cmd_status(call, args, state_dir)
    positions = call("GET", "/v2/positions")
    orders = call("GET", "/v2/orders?status=open&limit=100")
    out["open_positions"] = len(positions)
    out["open_orders"] = len(orders)
    out["room_for_entries"] = max(0, MAX_OPEN_POSITIONS - len(positions) - len([o for o in orders if o.get("side") == "buy" and not o.get("parent_order_id")]))
    out["limits"] = {"max_open": MAX_OPEN_POSITIONS, "usd_per_order": MAX_ORDER_USD, "stop_pct": STOP_FRACTION * 100}
    return out


def cmd_positions(call, args, state_dir: Path) -> dict:
    return {"held": [{k: p.get(k) for k in ("symbol", "qty", "avg_entry_price", "current_price", "unrealized_pl")}
                          for p in call("GET", "/v2/positions")]}


def cmd_orders(call, args, state_dir: Path) -> dict:
    status = args.status if args.status in ("open", "closed", "all") else "all"
    return {"orders": [summarize_order(o) for o in call("GET", f"/v2/orders?status={status}&limit=100&nested=true&direction=desc")]}


def cmd_fills(call, args, state_dir: Path) -> dict:
    """Filled orders with the data needed to compare against the desk's assumed fill (slippage report)."""
    rows = []
    for o in call("GET", "/v2/orders?status=closed&limit=100&nested=true&direction=asc"):
        for x in [o] + list(o.get("legs") or []):
            if x.get("filled_at"):
                rows.append({k: x.get(k) for k in ("id", "symbol", "side", "filled_qty", "filled_avg_price", "submitted_at", "filled_at", "order_class", "type")})
    return {"fills": rows}


def cmd_close(call, args, state_dir: Path) -> dict:
    check_not_halted(state_dir)
    symbol = args.symbol.strip().upper()
    if not SYMBOL.match(symbol):
        raise GuardError(f"refused: {symbol!r} is not a plain stock ticker")
    if not args.submit:
        return {"submit": False, "note": f"dry run: would close the {symbol} position and cancel its orders"}
    resp = call("DELETE", f"/v2/positions/{symbol}")
    log_event(state_dir, {"action": "close", "symbol": symbol, "order_id": resp.get("id")})
    return {"submit": True, "order_id": resp.get("id"), "status": resp.get("status")}


def cmd_halt(call, args, state_dir: Path) -> dict:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    halt_file(state_dir).write_text(args.reason or "halted\n", encoding="utf-8")
    cancelled = call("DELETE", "/v2/orders")
    out = {"halted": True, "orders_cancel_response": len(cancelled) if isinstance(cancelled, list) else cancelled}
    if args.flatten:
        out["flatten_response_count"] = len(call("DELETE", "/v2/positions?cancel_orders=true"))
    log_event(state_dir, {"action": "halt", "flatten": bool(args.flatten), "reason": args.reason or ""})
    return out


def cmd_resume(call, args, state_dir: Path) -> dict:
    f = halt_file(state_dir)
    existed = f.exists()
    if existed:
        f.unlink()
    log_event(state_dir, {"action": "resume"})
    return {"halted": False, "was_halted": existed}


COMMANDS = dict(status=cmd_status, preflight=cmd_preflight, buy=cmd_buy, positions=cmd_positions, orders=cmd_orders,
                fills=cmd_fills, close=cmd_close, halt=cmd_halt, resume=cmd_resume)


def main(argv=None, call=None) -> int:
    p = argparse.ArgumentParser(description="Alpaca PAPER order tool with guardrails (paper only, stocks only).")
    p.add_argument("--state-dir", default=os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("preflight")
    b = sub.add_parser("buy")
    b.add_argument("symbol")
    b.add_argument("--ref-price", type=float, default=0.0, help="reference price (default: latest IEX trade)")
    b.add_argument("--max-usd", type=float, default=MAX_ORDER_USD)
    b.add_argument("--submit", action="store_true", help="actually send the paper order (default: dry run)")
    sub.add_parser("positions")
    o = sub.add_parser("orders")
    o.add_argument("--status", default="all")
    sub.add_parser("fills")
    c = sub.add_parser("close")
    c.add_argument("symbol")
    c.add_argument("--submit", action="store_true")
    h = sub.add_parser("halt")
    h.add_argument("--reason", default="")
    h.add_argument("--flatten", action="store_true", help="also close all positions")
    sub.add_parser("resume")
    args = p.parse_args(argv)
    try:
        if call is None:
            call = make_client()
        result = COMMANDS[args.cmd](call, args, Path(args.state_dir))
    except GuardError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
