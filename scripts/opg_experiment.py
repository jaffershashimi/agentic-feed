"""Opening-auction (OPG) paper experiment: how does the paper account fill opening-auction orders?

Question: S-042 loses its edge at market-order opening costs (about 32 bps) but keeps it if fills land at the opening
auction price. Paper fills are simulated, so this cannot measure real slippage; it only shows whether OPG orders are
accepted (including OPG parent + broker-side stop) and what price the paper engine gives versus the official open.

  place  submit four one-share orders; only valid between 7:00 PM and 9:28 AM ET (Alpaca's OPG window)
  read   after the open: print fills versus the opening auction print, then sell the shares back (market, day)

Paper host only, one share each, tag xlab-. Never touches S-042 or S-044 orders. Writes no account data to the repo;
results go to stdout and the caller decides what to record (fill prices relative to the open, no balances).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from consumer import alpaca_paper as ap  # noqa: E402

STATE = Path(".alpaca_paper")
# (symbol, variant). Variants test: plain OPG market, OPG limit (marketable), and OPG parent with a broker-side stop.
PLAN = [("AAPL", "opg_market"), ("F", "opg_market"), ("MSFT", "opg_oto_stop"), ("KO", "opg_limit")]


def order_body(symbol: str, variant: str, ref: float) -> dict:
    cid = f"xlab-{symbol.lower()}-{variant}"
    body = {"symbol": symbol, "qty": "1", "side": "buy", "type": "market", "time_in_force": "opg", "client_order_id": cid}
    if variant == "opg_oto_stop":
        body.update({"order_class": "oto", "stop_loss": {"stop_price": f"{round(ref * 0.90, 2):.2f}"}})
    if variant == "opg_limit":
        body.update({"type": "limit", "limit_price": f"{round(ref * 1.03, 2):.2f}"})  # 3% through: marketable
    return body


def place(call) -> int:
    ap.check_not_halted(STATE)
    held = {p["symbol"] for p in call("GET", "/v2/positions")}
    openo = {o["symbol"] for o in call("GET", "/v2/orders?status=open&limit=100")}
    for sym, variant in PLAN:
        if sym in held or sym in openo:
            print(f"skip {sym}: already held or has an open order")
            continue
        ref = ap.latest_price(call, sym)
        body = order_body(sym, variant, ref)
        try:
            o = call("POST", "/v2/orders", body)
            print(f"ACCEPTED {sym} {variant}: status={o.get('status')} id={o.get('id')[:8]}")
            ap.log_event(STATE, {"event": "xlab_place", "symbol": sym, "variant": variant, "status": o.get("status")})
        except ap.GuardError as exc:
            print(f"REJECTED {sym} {variant}: {exc}")
            ap.log_event(STATE, {"event": "xlab_reject", "symbol": sym, "variant": variant, "error": str(exc)[:200]})
    return 0


def read(call) -> int:
    today = call("GET", "/v2/orders?status=all&limit=100&direction=desc")
    mine = [o for o in today if (o.get("client_order_id") or "").startswith("xlab-") and o.get("side") == "buy"]
    if not mine:
        print("no xlab orders found")
        return 1
    for o in mine:
        sym = o["symbol"]
        day = (o.get("filled_at") or o.get("submitted_at") or "")[:10]
        bars = call("GET", f"/v2/stocks/{sym}/bars?timeframe=1Min&start={day}T14:30:00Z&end={day}T14:40:00Z&feed=iex&limit=10", host="data")
        first = (bars.get("bars") or [{}])[0]
        fill = float(o["filled_avg_price"]) if o.get("filled_avg_price") else None
        opn = first.get("o")
        rel = f"{(fill / opn - 1) * 1e4:+.1f} bps vs first IEX minute open" if fill and opn else "n/a"
        print(f"{sym} {o['client_order_id']}: status={o['status']} filled_at={o.get('filled_at')} fill={fill} "
              f"first_minute_open={opn} -> {rel}")
        legs = [x["status"] + ":" + x["type"] for x in (o.get("legs") or [])]
        if legs:
            print(f"   legs: {legs}")
    held = {p["symbol"]: p for p in call("GET", "/v2/positions")}
    for o in mine:
        if o["symbol"] in held and o["status"] == "filled":
            for leg in call("GET", f"/v2/orders?status=open&symbols={o['symbol']}"):
                call("DELETE", f"/v2/orders/{leg['id']}")
            call("POST", "/v2/orders", {"symbol": o["symbol"], "qty": "1", "side": "sell", "type": "market",
                                        "time_in_force": "day", "client_order_id": f"xlab-{o['symbol'].lower()}-close"})
            print(f"closing {o['symbol']} (1 share, market)")
    return 0


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv or argv[0] not in ("place", "read"):
        print(__doc__)
        return 2
    call = ap.make_client()
    return place(call) if argv[0] == "place" else read(call)


if __name__ == "__main__":
    raise SystemExit(main())
