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


# ---------------------------------------------------------------- accounts (owner decisions 2026-11-22)
# Each paper account has its own limits, ledger, specs and controls; results are never pooled. OPT-PAPER-1 keeps every number above exactly as built (frozen).
# RESEARCH-LAB (100,000 USD paper) uses the same PERCENTAGES of the account as OPT-PAPER-1 (25,000 USD): open risk 40%, strategy halt 6%, account halt 16%;
# plus the correlated-group cap (15%) and monthly halt and review lines (6% and 10%) the owner approved with batch 2. Per-trade max loss stays 1,000 (owner);
# directional arms risk at most 300 of premium each, inside a 1,500 directional sub-cap (owner). Raising any percentage needs the owner first.
from dataclasses import dataclass, field  # noqa: E402

CORRELATED_GROUP = ("SPY", "QQQ", "IWM", "XSP", "SPX")   # broad US equity: counted as one group where a group cap applies


@dataclass(frozen=True)
class Account:
    label: str
    key_name: str
    secret_name: str
    open_risk_cap_usd: float
    strategy_halt_usd: float
    account_halt_usd: float
    start: date
    group_cap_usd: float | None = None
    group_members: tuple = field(default_factory=tuple)
    monthly_halt_usd: float | None = None      # negative: month-to-date result at or below this stops new entries until the next month
    monthly_review_usd: float | None = None    # negative: reported for review, stops nothing
    directional_cap_usd: float | None = None
    expected_equity_usd: float | None = None   # the account check confirms the account is about this size (within 10 percent)


ACCOUNTS = {
    "OPT-PAPER-1": Account(label=ACCOUNT_LABEL, key_name="OPT_LAB_ALPACA_KEY_ID", secret_name="OPT_LAB_ALPACA_SECRET_KEY",
                           open_risk_cap_usd=OPEN_RISK_CAP_USD, strategy_halt_usd=STRATEGY_HALT_USD, account_halt_usd=ACCOUNT_HALT_USD, start=PROGRAM_START),
    "RESEARCH-LAB": Account(label="RESEARCH-LAB", key_name="OPT_LAB2_ALPACA_KEY_ID", secret_name="OPT_LAB2_ALPACA_SECRET_KEY",
                            open_risk_cap_usd=40000.0, strategy_halt_usd=-6000.0, account_halt_usd=-16000.0, start=date(2026, 11, 24),
                            group_cap_usd=15000.0, group_members=CORRELATED_GROUP, monthly_halt_usd=-6000.0, monthly_review_usd=-10000.0,
                            directional_cap_usd=1500.0, expected_equity_usd=100000.0),
}
