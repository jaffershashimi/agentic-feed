"""US equity session calendar used for freshness and event windows.

Holiday and early-close tables are maintained by hand from the NYSE published
schedule and must be re-checked each year (see docs/DATA_SOURCES.md).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
CALENDAR_VERSION = "nyse-2025-2027-v1"

HOLIDAYS = {
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}
# Early closes end the regular session at 13:00 ET.
EARLY_CLOSES = {date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24), date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)}
# FOMC statement days (second day of each meeting); statement at 14:00 ET.
FOMC_DECISIONS = {
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
}
# Scheduled 08:30 ET (05:30 PT) macro releases from the official BLS and BEA schedules, checked 2026-10-24. Dates not yet
# published by the agencies are absent, never guessed; extend this table when the agencies publish them.
MACRO_RELEASES = {
    date(2026, 10, 29): ("pce",),  # BEA Personal Income and Outlays, September 2026
    date(2026, 11, 6): ("nfp",),  # BLS Employment Situation, October 2026
    date(2026, 11, 10): ("cpi",),  # BLS CPI, October 2026
    date(2026, 11, 25): ("pce",),  # BEA, October 2026
    date(2026, 12, 4): ("nfp",),  # BLS, November 2026
    date(2026, 12, 10): ("cpi",),  # BLS, November 2026
    date(2026, 12, 23): ("pce",),  # BEA, November 2026
    date(2027, 1, 8): ("nfp",),  # BLS, December 2026
}
MACRO_RELEASES_CHECKED = date(2026, 10, 24)
COVERED_YEARS = {2025, 2026, 2027}


def is_session(day: date) -> bool:
    if day.year not in COVERED_YEARS:
        raise ValueError(f"Calendar {CALENDAR_VERSION} does not cover {day.year}")
    return day.weekday() < 5 and day not in HOLIDAYS


def session_close(day: date) -> datetime:
    return datetime.combine(day, time(13 if day in EARLY_CLOSES else 16), ET)


def previous_session(day: date) -> date:
    day -= timedelta(days=1)
    while not is_session(day):
        day -= timedelta(days=1)
    return day


def last_completed_session(now: datetime) -> date:
    """The most recent session whose regular close is at or before `now`."""
    local = now.astimezone(ET)
    today = local.date()
    if is_session(today) and local >= session_close(today):
        return today
    return previous_session(today)


def monthly_opex(year: int, month: int) -> date:
    """Third Friday of the month, moved to Thursday if that Friday is a holiday."""
    first = date(year, month, 1)
    friday = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    return friday - timedelta(days=1) if friday in HOLIDAYS else friday


def upcoming_events(start: date, days: int = 10) -> list[dict]:
    """Calendar events from `start` (inclusive) through `days` calendar days."""
    events = []
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        if day.year not in COVERED_YEARS:
            break
        if day in HOLIDAYS:
            events.append({"date": day.isoformat(), "time_pt": None, "type": "market_closed"})
        if day in EARLY_CLOSES:
            events.append({"date": day.isoformat(), "time_pt": "10:00", "type": "early_close"})
        if day in FOMC_DECISIONS:
            events.append({"date": day.isoformat(), "time_pt": "11:00", "type": "fomc_statement"})
        for kind in MACRO_RELEASES.get(day, ()):
            events.append({"date": day.isoformat(), "time_pt": "05:30", "type": kind})
        if day == monthly_opex(day.year, day.month):
            events.append({"date": day.isoformat(), "time_pt": None, "type": "monthly_opex"})
    return events
