"""Catalyst tags, written at entry time before any result is known. Dated events come from the repo's calendar (FOMC, BLS and BEA releases, monthly expiration, early closes).
Earnings: ETFs have none; a single stock with no verified earnings source is tagged earnings_unknown and counts as catalyst-tagged (the conservative side)."""
from __future__ import annotations

from datetime import date, timedelta

from feed_pipeline import calendar as cal


def events_between(start: date, end: date) -> list:
    out = []
    d = start
    while d <= end:
        if d.year in cal.COVERED_YEARS:
            if d in cal.FOMC_DECISIONS:
                out.append({"date": d.isoformat(), "type": "fomc_statement"})
            if d + timedelta(days=1) in cal.FOMC_DECISIONS:
                out.append({"date": d.isoformat(), "type": "fomc_meeting_day1"})
            for kind in cal.MACRO_RELEASES.get(d, ()):
                out.append({"date": d.isoformat(), "type": kind})
            if d == cal.monthly_opex(d.year, d.month):
                out.append({"date": d.isoformat(), "type": "monthly_opex"})
            if d in cal.EARLY_CLOSES:
                out.append({"date": d.isoformat(), "type": "early_close"})
        d += timedelta(days=1)
    return out


def tag(underlying_type: str, entry_day: date, last_possible_exit: date, earnings_dates=None) -> dict:
    """underlying_type: 'etf' or 'stock'. earnings_dates: dates from a verified source, or None when there is none."""
    ev = events_between(entry_day, last_possible_exit)
    if underlying_type == "stock":
        if earnings_dates is None:
            ev.append({"date": None, "type": "earnings_unknown"})
        else:
            ev += [{"date": d.isoformat(), "type": "earnings"} for d in earnings_dates if entry_day <= d <= last_possible_exit]
    return {"events": ev, "catalyst_tagged": bool(ev), "window": [entry_day.isoformat(), last_possible_exit.isoformat()]}
