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


# ---------------------------------------------------------------------------------------------------------------------------
# Day-trade gate, adopted from the day-trader-skill pack's Risk Manager (MIT, third_party/day_trader_skill/trader-risk). It applies to intraday strategies such as S-046.
# A BLOCK is final for that order; an exit is never blocked by a day limit (getting out must always be possible).
from datetime import datetime, time as _time
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
DAY_MAX_LOSS_PER_TRADE = MAX_LOSS_PER_TRADE
DAY_MAX_DAILY_LOSS = DAILY_LOSS_HALT
DAY_MAX_TRADES = 3
DAY_MIN_REWARD_RISK = 1.0
DAY_NO_ENTRIES_BEFORE = _time(9, 45)
DAY_NO_ENTRIES_AFTER = _time(15, 30)
DAY_FLAT_BY = _time(15, 55)


def check_day_trade(entry, stop, target, qty, now, placed_today, side="buy", is_exit=False, fresh_price=None):
    """Return a list of BLOCK reasons (empty means PASS). entry, stop, target in dollars, qty whole shares, now a timezone-aware datetime,
    placed_today a list of max losses (USD) of entries already placed today. fresh_price (under 15 minutes old) raises the entry that risk is measured from, so a plan
    written at 10:15 cannot understate the risk at 10:55 after the price ran up. Exits (is_exit) are never blocked by the day limits."""
    if is_exit:
        return []
    reasons = []
    if side != "buy":
        reasons.append("short: the day-trade tier is long only")
    if qty < 1 or int(qty) != qty:
        reasons.append("size: whole shares only")
    base = max(entry, fresh_price) if fresh_price else entry
    if stop is None:
        reasons.append("stop: every day-trade entry needs a stop")
        return reasons
    if stop >= base:
        reasons.append("stop: a long's stop goes under the entry")
        return reasons
    risk = base - stop
    loss = qty * risk
    if loss > DAY_MAX_LOSS_PER_TRADE + 1e-9:
        reasons.append(f"loss: {qty} shares x {risk:.2f} risk a share = ${loss:.2f} at the stop, over ${DAY_MAX_LOSS_PER_TRADE:.0f} per trade")
    if target is not None:
        rr = (target - base) / risk
        if rr < DAY_MIN_REWARD_RISK - 1e-9:
            reasons.append(f"reward:risk: {rr:.2f} under the minimum {DAY_MIN_REWARD_RISK:.2f}")
    if len(placed_today) >= DAY_MAX_TRADES:
        reasons.append(f"trades: {len(placed_today)} entries already placed today (limit {DAY_MAX_TRADES})")
    if sum(placed_today) + loss > DAY_MAX_DAILY_LOSS + 1e-9:
        reasons.append(f"daily loss: today's max losses {sum(placed_today):.0f} plus this {loss:.0f} would pass ${DAY_MAX_DAILY_LOSS:.0f}")
    t = now.astimezone(_ET)
    if t.weekday() >= 5 or not (DAY_NO_ENTRIES_BEFORE <= t.time() <= DAY_NO_ENTRIES_AFTER):
        reasons.append("window: new entries only 09:45 to 15:30 New York time on a trading day")
    return reasons
