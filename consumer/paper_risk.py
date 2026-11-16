"""Tier-2 sizing for the NEW news strategies on the Alpaca PAPER account (BROKER_PAPER class). Paper only; real money is never touched.

Why a second tier: the plumbing tool (alpaca_paper.py) is capped at 1,000 USD per order so a plumbing bug cannot cost much, and the S-042 forward test keeps its
frozen 1,000 USD size so its protocol stays valid. The owner asked on 2026-11-16 for more paper money to learn markets faster, so the news strategies (S-043,
S-044 and later ones) size from the limits below. Changing these numbers is an owner decision, so they live in one place, with tests.

Limits (paper account equity is about 100,000 USD; numbers are fractions of it, not of any real account):
  max loss per trade       2,500 USD  (2.5 percent of equity), measured at the stop or at the defined-risk maximum
  max notional per stock   10,000 USD
  max total open risk      7,500 USD  (sum of per-trade max loss across open news trades)
  daily loss halt          3,000 USD  (realised plus open loss since the open: stop trading for the day and write HALT)
  max open news trades     6
Defined-risk options only (long options and vertical spreads); no naked short options, no undefined risk. Stocks may be shorted only through long puts or put spreads;
the tier never sells stock short.
"""
import math

MAX_LOSS_PER_TRADE = 2500.0
MAX_STOCK_NOTIONAL = 10000.0
MAX_TOTAL_OPEN_RISK = 7500.0
DAILY_LOSS_HALT = 3000.0
MAX_OPEN_TRADES = 6


class RiskError(Exception):
    """A tier-2 limit refused the trade. Nothing was sent."""


def stock_shares(price, stop_fraction):
    """Whole shares so that a stop at `stop_fraction` of price risks at most MAX_LOSS_PER_TRADE and notional stays within MAX_STOCK_NOTIONAL."""
    if price <= 0 or not 0 < stop_fraction < 1:
        raise RiskError("price must be positive and the stop fraction between 0 and 1")
    loss_per_share = price * (1.0 - stop_fraction)
    q = math.floor(min(MAX_LOSS_PER_TRADE / loss_per_share, MAX_STOCK_NOTIONAL / price))
    if q < 1:
        raise RiskError("one share exceeds the per-trade limits")
    return q


def option_contracts(max_loss_per_contract):
    """Contracts of a defined-risk structure (max loss per contract, in USD per 100-share contract) that fit the per-trade limit."""
    if max_loss_per_contract <= 0:
        raise RiskError("a defined-risk structure needs a positive max loss per contract")
    q = math.floor(MAX_LOSS_PER_TRADE / max_loss_per_contract)
    if q < 1:
        raise RiskError("one contract exceeds the per-trade limit")
    return q


def check_new_trade(trade_max_loss, open_risks, day_pnl):
    """Raise RiskError unless a new trade with `trade_max_loss` fits: per-trade, total open risk, open count and the daily loss halt."""
    if trade_max_loss > MAX_LOSS_PER_TRADE + 1e-9:
        raise RiskError(f"trade risk {trade_max_loss:.0f} exceeds {MAX_LOSS_PER_TRADE:.0f}")
    if len(open_risks) >= MAX_OPEN_TRADES:
        raise RiskError(f"{len(open_risks)} news trades are open (limit {MAX_OPEN_TRADES})")
    if sum(open_risks) + trade_max_loss > MAX_TOTAL_OPEN_RISK + 1e-9:
        raise RiskError(f"total open risk would be {sum(open_risks) + trade_max_loss:.0f} (limit {MAX_TOTAL_OPEN_RISK:.0f})")
    if day_pnl <= -DAILY_LOSS_HALT:
        raise RiskError(f"daily loss {day_pnl:.0f} reached the halt line {DAILY_LOSS_HALT:.0f}")
    return True
