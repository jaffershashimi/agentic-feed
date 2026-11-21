"""Broker adapter for the SECOND Alpaca paper account, and the account check. The factory refuses anything but the paper host (consumer.alpaca_paper.make_client) and refuses
to run if the new keys belong to the same account as the existing desk's."""
from __future__ import annotations

import time

from consumer import alpaca_paper as ap

from . import structures as S


def new_env(env) -> dict:
    """Map the NEW secret names onto the names make_client reads. The existing account's names are never used to send anything."""
    key, secret = _first_line(env.get("ALPACA_OPT_API_KEY", "")), _first_line(env.get("ALPACA_OPT_SECRET_KEY", ""))
    if not key or not secret:
        raise ap.GuardError("ALPACA_OPT_API_KEY and ALPACA_OPT_SECRET_KEY must be set (new secrets for the second paper account)")
    return {"ALPACA_API_KEY": key, "ALPACA_SECRET_KEY": secret, "ALPACA_PAPER_TRADE": "true"}


def _first_line(v: str) -> str:
    for ln in (v or "").splitlines():
        if ln.strip():
            return "".join(ln.split())
    return ""


def account_check(call_new, call_old=None) -> dict:
    """Booleans and levels only: never a balance. multi_leg_ok is true when the effective options level is 3 or more."""
    acct = call_new("GET", "/v2/account")
    approved = acct.get("options_approved_level")
    trading = acct.get("options_trading_level")
    out = {"status": acct.get("status"), "options_approved_level": approved, "options_trading_level": trading,
           "multi_leg_ok": bool(trading is not None and int(trading) >= 3), "has_options_buying_power": acct.get("options_buying_power") is not None}
    if call_old is not None:
        old = call_old("GET", "/v2/account")
        out["distinct_from_existing_account"] = bool(acct.get("id") and old.get("id") and acct["id"] != old["id"] and acct.get("account_number") != old.get("account_number"))
    return out


class Broker:
    def __init__(self, call, sleep=time.sleep):
        self.call, self.sleep = call, sleep

    def positions(self) -> list:
        return self.call("GET", "/v2/positions")

    def body(self, kind: str, legs, qty: int, net_limit: float, closing: bool, tag: str) -> dict:
        side_of = lambda lg: ("sell" if lg.side == "buy" else "buy") if closing else lg.side
        intent = lambda lg: ("buy_to_close" if lg.side == "sell" else "sell_to_close") if closing else ("buy_to_open" if lg.side == "buy" else "sell_to_open")
        if len(legs) == 1:
            lg = legs[0]
            return {"symbol": lg.symbol, "qty": str(qty), "side": side_of(lg), "type": "limit", "limit_price": f"{abs(net_limit):.2f}", "time_in_force": "day", "position_intent": intent(lg),
                    "client_order_id": tag}
        return {"order_class": "mleg", "qty": str(qty), "type": "limit", "limit_price": f"{net_limit:.2f}", "time_in_force": "day", "client_order_id": tag,
                "legs": [{"symbol": lg.symbol, "ratio_qty": str(lg.ratio), "side": side_of(lg), "position_intent": intent(lg)} for lg in legs]}

    def send_and_wait(self, kind, legs, qty, net_limit, closing, tag, wait_s=90, step_s=10) -> dict:
        resp = self.call("POST", "/v2/orders", self.body(kind, legs, qty, net_limit, closing, tag))
        oid = resp.get("id")
        status, avg = resp.get("status"), resp.get("filled_avg_price")
        waited = 0
        while status not in ("filled", "canceled", "expired", "rejected") and waited < wait_s:
            self.sleep(step_s)
            waited += step_s
            o = self.call("GET", f"/v2/orders/{oid}")
            status, avg = o.get("status"), o.get("filled_avg_price")
        if status != "filled":
            try:
                self.call("DELETE", f"/v2/orders/{oid}")
            except ap.GuardError:
                pass
            return {"order_id": oid, "status": status, "filled": False, "broker_net": None}
        broker_net = None
        if avg not in (None, ""):
            sign = 1 if net_limit >= 0 else -1
            broker_net = sign * abs(float(avg))
        return {"order_id": oid, "status": status, "filled": True, "broker_net": broker_net}
