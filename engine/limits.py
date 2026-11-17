"""The limits register: every number that limits what the paper desk may do, in one place, each with its source and who set it.

An institution keeps its risk limits in one machine-readable register that the pre-trade controls read, so a limit cannot differ between the document, the code
and the check. This register reads the live constants from the modules that enforce them (it holds no second copy), so it cannot drift from them.

owner_set True  : the owner or the owner's written decision set it. Only the owner changes it.
owner_set False : PROPOSED by engineering, REPORT ONLY. It raises WARN or FAIL in the desk pack and blocks nothing until the owner approves it in writing.
Paper account only. No real-money limit lives here.
"""
from consumer import alpaca_paper as ap
from consumer import oversold_forward as of
from consumer import paper_risk as pr

REGISTER = [
    # id, value, unit, owner_set, where it is enforced
    ("S042_POSITION_CAP", of.POSITION_CAP, "open positions", True, "consumer/oversold_forward.py"),
    ("S042_NOTIONAL_USD", of.NOTIONAL_USD, "USD per entry", True, "consumer/oversold_forward.py"),
    ("S042_STOP_FRACTION", of.STOP_FRACTION, "of the signal close", True, "consumer/oversold_forward.py (broker-side OTO stop on every entry)"),
    ("S042_HOLD_SESSIONS", of.HOLD_DAYS, "sessions (time stop)", True, "consumer/oversold_forward.py"),
    ("S042_LIMIT_SLIP", of.LIMIT_SLIP, "through the quote", True, "consumer/oversold_forward.py (morning mode)"),
    ("S042_MAX_QUOTE_SPREAD", of.MAX_QUOTE_SPREAD, "of the mid", True, "consumer/oversold_forward.py (morning mode)"),
    ("PLUMBING_MAX_OPEN_POSITIONS", ap.MAX_OPEN_POSITIONS, "positions", True, "consumer/alpaca_paper.py"),
    ("PLUMBING_MAX_ORDER_USD", ap.MAX_ORDER_USD, "USD per order", True, "consumer/alpaca_paper.py"),
    ("NEWS_MAX_LOSS_PER_TRADE", pr.MAX_LOSS_PER_TRADE, "USD", True, "consumer/paper_risk.py"),
    ("NEWS_MAX_STOCK_NOTIONAL", pr.MAX_STOCK_NOTIONAL, "USD", True, "consumer/paper_risk.py"),
    ("NEWS_MAX_TOTAL_OPEN_RISK", pr.MAX_TOTAL_OPEN_RISK, "USD", True, "consumer/paper_risk.py"),
    ("NEWS_DAILY_LOSS_HALT", pr.DAILY_LOSS_HALT, "USD", True, "consumer/paper_risk.py"),
    ("NEWS_MAX_OPEN_TRADES", pr.MAX_OPEN_TRADES, "trades", True, "consumer/paper_risk.py"),
    # Proposed, report only (owner decides whether any of these is ever enforced). Percent of paper account equity.
    ("PROPOSED_DAILY_LOSS_AMBER_PCT", -1.5, "percent of equity", False, "engine/riskreport.py"),
    ("PROPOSED_DAILY_LOSS_RED_PCT", -3.0, "percent of equity (same line as NEWS_DAILY_LOSS_HALT on a 100,000 USD account)", False, "engine/riskreport.py"),
    ("PROPOSED_DRAWDOWN_AMBER_PCT", -5.0, "percent below the equity peak", False, "engine/riskreport.py"),
    ("PROPOSED_DRAWDOWN_RED_PCT", -10.0, "percent below the equity peak", False, "engine/riskreport.py"),
    ("PROPOSED_GROSS_AMBER_PCT", 70.0, "percent of equity in positions", False, "engine/riskreport.py"),
    ("PROPOSED_GROSS_RED_PCT", 90.0, "percent of equity in positions", False, "engine/riskreport.py"),
    ("PROPOSED_LARGEST_AMBER_PCT", 5.0, "percent of equity in one name", False, "engine/riskreport.py"),
    ("PROPOSED_LARGEST_RED_PCT", 10.0, "percent of equity in one name", False, "engine/riskreport.py"),
]
_BY_ID = {r[0]: r for r in REGISTER}


def get(limit_id):
    return _BY_ID[limit_id][1]


def as_rows():
    return [{"id": i, "value": v, "unit": u, "owner_set": o, "enforced_in": w} for i, v, u, o, w in REGISTER]
