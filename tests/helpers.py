"""Synthetic fixtures. Values are invented; they are not market data."""
from __future__ import annotations

import math
from datetime import date, timedelta

from feed_pipeline import calendar
from feed_pipeline.sources import FetchResult


def sessions(end: date, count: int) -> list[date]:
    out, day = [], end
    while len(out) < count:
        if calendar.is_session(day):
            out.append(day)
        day -= timedelta(days=1)
    return list(reversed(out))


def wave(days: list[date], base: float, amplitude: float, period: int = 17) -> list[tuple[date, float]]:
    return [(d, round(base + amplitude * math.sin(i / period * 2 * math.pi), 4)) for i, d in enumerate(days)]


def trend(days: list[date], start: float, daily: float) -> list[tuple[date, float]]:
    return [(d, round(start * (1 + daily) ** i * (1 + 0.004 * math.sin(i)), 4)) for i, d in enumerate(days)]


def synthetic_results(end: date) -> dict[str, FetchResult]:
    days = sessions(end, 300)
    weekly = [d for d in days if d.weekday() == 4]
    data = {
        "VIX": ("cboe", wave(days, 17.0, 3.0)), "VIX9D": ("cboe", wave(days, 16.0, 3.5)),
        "VIX3M": ("cboe", wave(days, 19.0, 2.0)), "VVIX": ("cboe", wave(days, 90.0, 8.0)),
        "SKEW": ("cboe", wave(days, 140.0, 6.0)),
        "SP500": ("fred", trend(days, 5000.0, 0.0006)), "DGS2": ("fred", wave(days, 3.9, 0.2)),
        "DGS10": ("fred", wave(days, 4.2, 0.2)), "T10Y2Y": ("fred", wave(days, 0.3, 0.1)),
        "BAMLH0A0HYM2": ("fred", wave(days, 3.1, 0.3)), "NFCI": ("fred", wave(weekly, -0.5, 0.05, 7)),
        "DTWEXBGS": ("fred", wave(days, 120.0, 2.0)),
    }
    return {name: FetchResult(provider, name, f"synthetic://{name}", series) for name, (provider, series) in data.items()}
