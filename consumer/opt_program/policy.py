"""Frozen program constants. Every number below is an owner decision of 2026-11-21 unless the comment says it is an engineering choice (flagged on the Board)."""
from __future__ import annotations

from datetime import date

PROGRAM_START = date(2026, 11, 23)
ACCOUNT_LABEL = "OPT-PAPER-1"          # results are labelled with this and never pooled with any other account

# Risk (owner)
MAX_LOSS_USD = 1000.0                   # max loss at entry per structure
MAX_LOSS_FALLBACK_USD = 1500.0          # only when the smallest available version cannot fit under MAX_LOSS_USD; never above, never raised for conviction
OPEN_RISK_CAP_USD = 10000.0
MAX_OPEN_PER_STRATEGY = 3
MAX_OPEN_PER_UNDERLYING_PER_STRATEGY = 1
STRATEGY_HALT_USD = -1500.0             # realized plus modelled open result per strategy
ACCOUNT_HALT_USD = -4000.0              # all strategies together: stop every new entry and wait for the owner

# Calendar (owner): Thanksgiving. Market closed 2026-11-26, early close 2026-11-27. No expirations on or inside those days.
BLOCKED_EXPIRIES = {date(2026, 11, 26), date(2026, 11, 27)}

# Costs (engineering choice, flagged): flat per contract, per leg, per side. Alpaca equity options have no commission; the regulatory pass-through is
# options regulatory fee 0.015 plus OCC clearing fee 0.025 per contract per side (sources also disagree on a FINRA TAF of 0 to 0.00329 on sells), so 0.05 is rounded up to cover them.
FEE_PER_CONTRACT_LEG_USD = 0.05
# Fills (engineering choice, flagged): every modelled fill sits this fraction of the way from the mid price to the natural price (buy at ask, sell at bid). 1.0 is also reported.
FILL_FRACTION_TOWARD_NATURAL = 0.75

# Liquidity filter (engineering choice, flagged), applied to every leg and to the structure
LEG_MIN_BID_SOLD = 0.05                 # a leg we sell must bid at least this much
LEG_WIDTH_FRACTION_OF_MID = 0.05        # bid-ask width must be at most 5 percent of the mid ...
LEG_WIDTH_FLOOR_USD = 0.05              # ... but a width up to this is always fine
LEG_WIDTH_CEILING_USD = 0.25            # ... and never wider than this
LEG_MIN_OPEN_INTEREST = 100             # applied when the contract list reports open interest
STRUCTURE_FRICTION_MAX_FRACTION = 0.15  # entry cost from the spread (natural vs mid, all legs) at most 15 percent of max loss

# Transfer to real size (owner)
SMALL_ACCOUNT_USD = 800
SMALL_RISK_RANGE_USD = (50.0, 80.0)

# Ranking labels (owner)
MIN_TRADES_FOR_RANKING = 30
UNRANKED_LABEL = "unranked, not evidence"

ENTRY_WINDOW_ET = ((10, 25), (11, 45))  # entries only between 10:25 and 11:45 ET (after the 10:20 ET engine check); early-close days end at 11:30
CONTROL_DAILY_PROB = 0.20               # shadow random-entry control: chance per eligible spec and underlying and day
