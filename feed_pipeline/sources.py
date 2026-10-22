"""Free, keyless public data sources. Every fetch is fail-soft.

Each parser turns raw CSV text into a list of (date, value) observations,
oldest first, skipping missing values. Fetch failures are reported, never
replaced with guessed numbers.
"""
from __future__ import annotations

import csv
import io
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime

USER_AGENT = "agentic-feed/0.1 (public research feed; contact via GitHub repository issues)"

CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

CBOE_SERIES = ("VIX", "VIX9D", "VIX3M", "VVIX", "SKEW")
FRED_SERIES = ("SP500", "DGS2", "DGS10", "T10Y2Y", "BAMLH0A0HYM2", "NFCI", "DTWEXBGS")

Series = list[tuple[date, float]]


@dataclass
class FetchResult:
    provider: str
    name: str
    url: str
    series: Series = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.series)

    @property
    def as_of(self) -> date | None:
        return self.series[-1][0] if self.series else None


def fetch_text(url: str, attempts: int = 3, timeout: float = 20.0) -> str:
    """GET with exponential backoff (1s, 2s). Raises the last error."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last = error
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
    raise last if last else RuntimeError("fetch failed")


def _parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date {text!r}")


def _parse_number(text: str) -> float | None:
    text = text.strip()
    if text in ("", ".", "NaN", "nan", "N/A"):
        return None
    return float(text)


def parse_cboe(text: str, name: str) -> Series:
    """Cboe history CSV: DATE plus either OHLC columns (use CLOSE) or one column named after the index."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"{name}: empty CSV")
    columns = {c.strip().upper(): c for c in rows[0].keys() if c}
    value_col = columns.get("CLOSE") or columns.get(name.upper())
    date_col = columns.get("DATE")
    if not value_col or not date_col:
        raise ValueError(f"{name}: unexpected columns {list(columns)}")
    return _collect(rows, date_col, value_col)


def parse_fred(text: str, series: str) -> Series:
    """FRED fredgraph CSV: a date column (observation_date or DATE) and the series column; '.' means missing."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"{series}: empty CSV")
    columns = {c.strip(): c for c in rows[0].keys() if c}
    date_col = columns.get("observation_date") or columns.get("DATE")
    value_col = columns.get(series)
    if not date_col or not value_col:
        raise ValueError(f"{series}: unexpected columns {list(columns)}")
    return _collect(rows, date_col, value_col)


def _collect(rows: list[dict], date_col: str, value_col: str) -> Series:
    out: dict[date, float] = {}
    for row in rows:
        value = _parse_number(row.get(value_col) or "")
        if value is not None:
            out[_parse_date(row[date_col])] = value
    return sorted(out.items())


def fetch_cboe(name: str, fetch=fetch_text) -> FetchResult:
    url = CBOE_URL.format(name=name)
    try:
        return FetchResult("cboe", name, url, parse_cboe(fetch(url), name))
    except Exception as error:  # noqa: BLE001 - one provider must never break the feed
        return FetchResult("cboe", name, url, error=f"{type(error).__name__}: {error}"[:200])


def fetch_fred(series: str, fetch=fetch_text) -> FetchResult:
    url = FRED_URL.format(series=series)
    try:
        return FetchResult("fred", series, url, parse_fred(fetch(url), series))
    except Exception as error:  # noqa: BLE001
        return FetchResult("fred", series, url, error=f"{type(error).__name__}: {error}"[:200])
