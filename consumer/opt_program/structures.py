"""Defined-risk structures: legs, modelled prices, fees, max loss. No naked shorts: every builder proves the short legs are covered."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from . import policy as P

KINDS = ("long-call", "long-put", "debit-vertical-call", "debit-vertical-put", "credit-vertical-call", "credit-vertical-put",
         "iron-condor", "long-butterfly-call", "long-butterfly-put", "calendar-call", "calendar-put", "diagonal-call", "diagonal-put")
# Alpaca multi-leg support for calendars and diagonals is untested on this account, so the engine refuses to send them until a paper test passes.
ENGINE_SENDS = {"long-call", "long-put", "debit-vertical-call", "debit-vertical-put", "credit-vertical-call", "credit-vertical-put", "iron-condor",
                "long-butterfly-call", "long-butterfly-put"}


class StructureError(Exception):
    """The structure is malformed or not defined risk. Nothing is sent."""


@dataclass
class Leg:
    symbol: str          # OCC symbol
    side: str            # 'buy' or 'sell' (to open)
    ratio: int
    cp: str              # 'C' or 'P'
    strike: float
    expiry: date
    bid: float = 0.0
    ask: float = 0.0
    delta: float | None = None
    oi: int | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    def price(self, how: str, closing: bool = False) -> float:
        """Per-share price paid (positive) for the action on this leg. how: 'mid', 'natural' or 'model'."""
        buying = (self.side == "buy") != closing
        nat = self.ask if buying else self.bid
        if how == "natural":
            return nat
        if how == "mid":
            return self.mid
        return self.mid + P.FILL_FRACTION_TOWARD_NATURAL * (nat - self.mid)


def net(legs, how: str, closing: bool = False) -> float:
    """Signed net per share to OPEN (or close): positive = pay (debit), negative = receive (credit)."""
    total = 0.0
    for lg in legs:
        buying = (lg.side == "buy") != closing
        total += (1 if buying else -1) * lg.ratio * lg.price(how, closing)
    return total


def contracts(legs, qty: int = 1) -> int:
    return qty * sum(lg.ratio for lg in legs)


def fees_one_side(legs, qty: int = 1) -> float:
    return P.FEE_PER_CONTRACT_LEG_USD * contracts(legs, qty)


def friction(legs) -> float:
    """Dollars per structure (x100) lost to the spread at entry: natural net minus mid net (positive)."""
    return round(abs(net(legs, "natural") - net(legs, "mid")) * 100, 4)


def _need(cond: bool, msg: str):
    if not cond:
        raise StructureError(msg)


def _same(legs, attr):
    return len({getattr(lg, attr) for lg in legs}) == 1


def width_of(kind: str, legs) -> float:
    ks = sorted({lg.strike for lg in legs})
    if kind == "iron-condor":
        puts = sorted(lg.strike for lg in legs if lg.cp == "P")
        calls = sorted(lg.strike for lg in legs if lg.cp == "C")
        return max(puts[1] - puts[0], calls[1] - calls[0])
    return ks[-1] - ks[0] if len(ks) > 1 else 0.0


def validate(kind: str, legs) -> None:
    """Prove the structure is defined risk. Raises StructureError otherwise."""
    _need(kind in KINDS, f"unknown structure {kind}")
    _need(all(lg.ratio >= 1 for lg in legs), "ratio must be at least 1")
    if kind in ("long-call", "long-put"):
        _need(len(legs) == 1 and legs[0].side == "buy" and legs[0].cp == ("C" if kind.endswith("call") else "P"), "malformed single long")
    elif kind.startswith(("debit-vertical", "credit-vertical")):
        cp = "C" if kind.endswith("call") else "P"
        _need(len(legs) == 2 and _same(legs, "expiry") and all(lg.cp == cp and lg.ratio == 1 for lg in legs), "malformed vertical")
        buy = [lg for lg in legs if lg.side == "buy"]
        sell = [lg for lg in legs if lg.side == "sell"]
        _need(len(buy) == 1 and len(sell) == 1 and buy[0].strike != sell[0].strike, "a vertical needs one long and one short at different strikes")
        long_k, short_k = buy[0].strike, sell[0].strike
        debit = kind.startswith("debit")
        bull_call_debit = cp == "C" and debit and long_k < short_k
        bear_put_debit = cp == "P" and debit and long_k > short_k
        bear_call_credit = cp == "C" and not debit and long_k > short_k
        bull_put_credit = cp == "P" and not debit and long_k < short_k
        _need(bull_call_debit or bear_put_debit or bear_call_credit or bull_put_credit, "the long leg must sit on the covering side of the short leg")
    elif kind == "iron-condor":
        _need(len(legs) == 4 and _same(legs, "expiry") and all(lg.ratio == 1 for lg in legs), "malformed iron condor")
        puts = sorted([lg for lg in legs if lg.cp == "P"], key=lambda x: x.strike)
        calls = sorted([lg for lg in legs if lg.cp == "C"], key=lambda x: x.strike)
        _need(len(puts) == 2 and len(calls) == 2, "an iron condor needs two puts and two calls")
        _need(puts[0].side == "buy" and puts[1].side == "sell" and calls[0].side == "sell" and calls[1].side == "buy", "long wings outside, short body inside")
        _need(puts[1].strike < calls[0].strike, "the short strikes must not cross")
    elif kind.startswith("long-butterfly"):
        cp = "C" if kind.endswith("call") else "P"
        _need(len(legs) == 3 and _same(legs, "expiry") and all(lg.cp == cp for lg in legs), "malformed butterfly")
        lo, mid, hi = sorted(legs, key=lambda x: x.strike)
        _need(lo.side == "buy" and hi.side == "buy" and mid.side == "sell" and lo.ratio == 1 and hi.ratio == 1 and mid.ratio == 2, "buy 1, sell 2, buy 1")
        _need(abs((mid.strike - lo.strike) - (hi.strike - mid.strike)) < 1e-9, "the wings must be equal")
    elif kind.startswith(("calendar", "diagonal")):
        cp = "C" if kind.endswith("call") else "P"
        _need(len(legs) == 2 and all(lg.cp == cp and lg.ratio == 1 for lg in legs), "malformed calendar or diagonal")
        buy = [lg for lg in legs if lg.side == "buy"]
        sell = [lg for lg in legs if lg.side == "sell"]
        _need(len(buy) == 1 and len(sell) == 1 and buy[0].expiry > sell[0].expiry, "the long leg must expire later than the short leg")
        if kind.startswith("calendar"):
            _need(buy[0].strike == sell[0].strike, "a calendar uses one strike")
        else:
            covers = buy[0].strike <= sell[0].strike if cp == "C" else buy[0].strike >= sell[0].strike
            _need(covers, "a diagonal's long leg must cover its short leg (otherwise the loss is not capped at the debit)")


def economics(kind: str, legs, qty: int = 1, how: str = "model") -> dict:
    """Per-structure dollars. max_loss includes entry and exit fees (conservative). Debit structures need a positive debit; credit structures a positive credit."""
    validate(kind, legs)
    n = net(legs, how)
    fees2 = 2 * fees_one_side(legs, qty)
    w = width_of(kind, legs)
    if kind in ("long-call", "long-put", "debit-vertical-call", "debit-vertical-put", "long-butterfly-call", "long-butterfly-put") or kind.startswith(("calendar", "diagonal")):
        _need(n > 0, "a debit structure must cost money to open")
        loss = n * 100 * qty + fees2
        profit = None if (kind in ("long-call", "long-put") or kind.startswith(("calendar", "diagonal"))) else (w - n) * 100 * qty - fees2
        if profit is not None:
            _need(profit > 0, "no profit potential after costs")
    else:
        credit = -n
        _need(credit > 0, "a credit structure must pay money to open")
        _need(credit < w, "the credit cannot be as large as the width")
        loss = (w - credit) * 100 * qty + fees2
        profit = credit * 100 * qty - fees2
        _need(profit > 0, "no profit potential after costs")
    return {"net": round(n, 4), "max_loss_usd": round(loss, 2), "max_profit_usd": None if profit is None else round(profit, 2), "width": w, "fees_round_trip_usd": round(fees2, 2),
            "friction_usd": friction(legs) * qty}


def close_net(legs, how: str = "model") -> float:
    return net(legs, how, closing=True)


def pnl_usd(entry_net: float, exit_net: float, qty: int, legs, fees_sides: int = 2) -> float:
    """Result in dollars: -(entry_net + exit_net) x 100 x qty minus fees on the sides done. entry_net: debit positive. exit_net: net to close, debit positive."""
    return round(-(entry_net + exit_net) * 100 * qty - fees_sides * fees_one_side(legs, qty), 2)
