"""Paper-desk cash-flow, fee, break-even and outcome math (paper-exp-004). Standard library only, no network, no broker.

Every function works on observed executable quotes: buys at the ask, sells at the bid. The quoted spread is therefore
already inside the cash flows and is never subtracted a second time. Slippage is a separate, optional haircut.
Probabilities passed in are assumed, uncalibrated hypotheses unless a caller says otherwise; nothing here measures edge.
"""
import math
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

# Robinhood US stock/ETF option and equity fees, as published and checked on 2026-10-24
# (https://robinhood.com/us/en/support/articles/trading-fees-on-robinhood/). Each entry is valid for an inclusive date
# range. Outside every range the schedule is unknown: fee functions return None and the trade is not measured.
FEE_SCHEDULES = [
    {"start": date(2026, 10, 1), "end": date(2026, 12, 31),
     "option_per_contract": Decimal("0.04"),      # options regulatory/exchange fee per contract traded, each side
     "sec_per_million": Decimal("20.60"),         # SEC fee on sell principal, rounded up to the cent
     "taf_option_per_contract": Decimal("0"),     # FINRA TAF paused Oct 1 - Dec 31, 2026
     "taf_equity_per_share": Decimal("0"),
     "source": "robinhood trading-fees page, checked 2026-10-24"},
]
TICK = Decimal("0.01")


def schedule_for(day):
    for s in FEE_SCHEDULES:
        if s["start"] <= day <= s["end"]:
            return s
    return None


def _cents_up(x):
    return (Decimal(x) / TICK).to_integral_value(rounding=ROUND_CEILING) * TICK


def sec_fee(sell_principal, day):
    s = schedule_for(day)
    if s is None:
        return None
    if sell_principal <= 0:
        return Decimal("0")
    return _cents_up(Decimal(str(sell_principal)) * s["sec_per_million"] / Decimal(1_000_000))


def option_fees(contracts, side, price, day, multiplier=100):
    """Fees for one option fill. side is buy or sell. Returns a dict of components, or None if the schedule is unknown."""
    s = schedule_for(day)
    if s is None:
        return None
    reg = s["option_per_contract"] * contracts
    taf = s["taf_option_per_contract"] * contracts if side == "sell" else Decimal("0")
    sec = sec_fee(Decimal(str(price)) * multiplier * contracts, day) if side == "sell" else Decimal("0")
    return {"regulatory": reg, "taf": taf, "sec": sec, "total": reg + taf + sec, "source": s["source"]}


def equity_fees(shares, side, price, day):
    s = schedule_for(day)
    if s is None:
        return None
    taf = s["taf_equity_per_share"] * Decimal(str(shares)) if side == "sell" else Decimal("0")
    sec = sec_fee(Decimal(str(price)) * Decimal(str(shares)), day) if side == "sell" else Decimal("0")
    return {"regulatory": Decimal("0"), "taf": taf, "sec": sec, "total": taf + sec, "source": s["source"]}


def round_tick_up(x):
    return float(_cents_up(Decimal(str(x))))


def long_option(contracts, entry_ask, exit_bid, day_in, day_out, multiplier=100, slippage=0.0):
    """Cash flows of a long call or put: buy at ask, sell at bid, spread counted once."""
    fi, fo = option_fees(contracts, "buy", entry_ask, day_in, multiplier), option_fees(contracts, "sell", exit_bid, day_out, multiplier)
    if fi is None or fo is None:
        return None
    debit = Decimal(str(entry_ask)) * multiplier * contracts + fi["total"]
    proceeds = Decimal(str(exit_bid)) * multiplier * contracts - fo["total"]
    pnl = proceeds - debit - Decimal(str(slippage))
    return {"debit": float(debit), "proceeds": float(proceeds), "pnl": float(pnl), "return": float(pnl / debit),
            "fees": float(fi["total"] + fo["total"])}


def max_loss_long_option(contracts, entry_ask, day_in, multiplier=100):
    """Maximum economic loss of a long option: the full debit plus entry fees (expires worthless)."""
    fi = option_fees(contracts, "buy", entry_ask, day_in, multiplier)
    return None if fi is None else float(Decimal(str(entry_ask)) * multiplier * contracts + fi["total"])


def stop_risk(contracts, entry_ask, stop_price, day_in, multiplier=100, slippage=0.0):
    """Planned loss if the stop executes exactly at the stop price, with fees and the separate slippage reserve."""
    r = long_option(contracts, entry_ask, stop_price, day_in, day_in, multiplier, slippage)
    return None if r is None else -r["pnl"]


def stressed_loss(contracts, entry_ask, stop_price, gap_fraction, day_in, multiplier=100, slippage=0.0):
    """Loss if the exit fills gap_fraction below the stop (a gap through the stop), floored at zero premium."""
    exit_px = max(0.0, stop_price * (1 - gap_fraction))
    r = long_option(contracts, entry_ask, exit_px, day_in, day_in, multiplier, slippage)
    return None if r is None else -r["pnl"]


def debit_spread(contracts, long_ask, short_bid, long_exit_bid, short_exit_ask, day_in, day_out, multiplier=100,
                 quote_times=None, max_skew_seconds=5):
    """A debit vertical valued as a package. quote_times = (long_entry, short_entry, long_exit, short_exit) datetimes;
    entry and exit leg quotes must each be within max_skew_seconds of each other, otherwise the package is not fillable."""
    if quote_times is not None:
        le, se, lx, sx = quote_times
        if any(t is None for t in quote_times):
            return {"status": "UNFILLABLE", "reason": "missing leg quote time"}
        if abs((le - se).total_seconds()) > max_skew_seconds or abs((lx - sx).total_seconds()) > max_skew_seconds:
            return {"status": "UNFILLABLE", "reason": "leg quotes are not synchronized"}
    if None in (long_ask, short_bid, long_exit_bid, short_exit_ask):
        return {"status": "UNFILLABLE", "reason": "missing leg quote"}
    fees = [option_fees(contracts, "buy", long_ask, day_in, multiplier), option_fees(contracts, "sell", short_bid, day_in, multiplier),
            option_fees(contracts, "sell", long_exit_bid, day_out, multiplier), option_fees(contracts, "buy", short_exit_ask, day_out, multiplier)]
    if any(f is None for f in fees):
        return {"status": "UNMEASURED", "reason": "fee schedule unknown for the date"}
    m = Decimal(multiplier) * contracts
    debit = (Decimal(str(long_ask)) - Decimal(str(short_bid))) * m + fees[0]["total"] + fees[1]["total"]
    proceeds = (Decimal(str(long_exit_bid)) - Decimal(str(short_exit_ask))) * m - fees[2]["total"] - fees[3]["total"]
    if debit <= 0:
        return {"status": "UNFILLABLE", "reason": "not a debit"}
    pnl = proceeds - debit
    return {"status": "MEASURED", "debit": float(debit), "proceeds": float(proceeds), "pnl": float(pnl),
            "max_loss": float(debit), "return": float(pnl / debit)}


def expected_value(p, net_win, net_loss):
    """EV = p*G - (1-p)*L with G, L > 0 in dollars. p is an assumed, uncalibrated hypothesis."""
    if not (0 <= p <= 1) or net_win <= 0 or net_loss <= 0:
        raise ValueError("need 0 <= p <= 1 and positive win and loss")
    return p * net_win - (1 - p) * net_loss


def break_even_p(net_win, net_loss, cost=0.0):
    """Break-even win probability. With gross outcomes and a separate round-trip cost C: (L + C) / (G + L)."""
    if net_win <= 0 or net_loss <= 0 or cost < 0:
        raise ValueError("need positive win and loss and non-negative cost")
    return (net_loss + cost) / (net_win + net_loss)


def fill_allowed(quote_time, fill_time, bid, ask, delay, market_open, max_age=60):
    """A quote supports a paper fill only with a validated source time, at most max_age seconds old, no look-ahead,
    realtime delay, bid and ask present and not crossed, and the market open. Returns (allowed, reason)."""
    if not market_open:
        return False, "MARKET_CLOSED"
    if quote_time is None:
        return False, "missing source quote time"
    if delay != "realtime":
        return False, f"quote delay {delay}"
    if quote_time > fill_time:
        return False, "quote time after fill (look-ahead)"
    if (fill_time - quote_time) > timedelta(seconds=max_age):
        return False, "stale quote"
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return False, "missing bid or ask"
    if bid > ask:
        return False, "crossed quote"
    return True, "ok"


def classify_interval(prev_bid, next_bid, stop, target, low=None, high=None):
    """Outcome between two option observations. Without an observed low and high of the option bid in the interval,
    a pass through both levels, or any level crossed between sparse points, cannot be ordered: UNRESOLVED.
    Returns STOP, TARGET, OPEN or UNRESOLVED."""
    if prev_bid is None or next_bid is None:
        return "UNRESOLVED"
    lo = low if low is not None else min(prev_bid, next_bid)
    hi = high if high is not None else max(prev_bid, next_bid)
    hit_stop, hit_target = lo <= stop, hi >= target
    if hit_stop and hit_target:
        return "UNRESOLVED"
    if low is None or high is None:
        # Only the endpoints were seen. A level that the endpoints straddle may have been touched, but the path is unseen.
        if hit_stop or hit_target:
            return "UNRESOLVED"
        return "OPEN"
    return "STOP" if hit_stop else "TARGET" if hit_target else "OPEN"
