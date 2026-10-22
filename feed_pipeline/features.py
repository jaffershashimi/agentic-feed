"""Deterministic features computed from point-in-time series.

Every function takes only observations up to the decision date, so callers
must slice series with `as_of_slice` first. Heuristic scores are labeled v0
and are NOT validated signals; see docs/SIGNAL_SCORECARD.md.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import fmean, pstdev

from .sources import Series

TRADING_DAYS = 252


def as_of_slice(series: Series, cutoff: date) -> Series:
    """Observations dated on or before `cutoff` (no lookahead)."""
    return [(d, v) for d, v in series if d <= cutoff]


def latest(series: Series) -> float | None:
    return series[-1][1] if series else None


def realized_vol(series: Series, window: int = 20) -> float | None:
    """Annualized close-to-close volatility of log returns, in percent."""
    closes = [v for _, v in series[-(window + 1):]]
    if len(closes) < window + 1 or any(c <= 0 for c in closes):
        return None
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    mean = fmean(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance * TRADING_DAYS) * 100


def sma(series: Series, window: int) -> float | None:
    values = [v for _, v in series[-window:]]
    return fmean(values) if len(values) == window else None


def zscore(series: Series, window: int = 252) -> float | None:
    """Latest value versus the trailing window (including it); None without enough history or variation."""
    values = [v for _, v in series[-window:]]
    if len(values) < max(13, window // 4):
        return None
    spread = pstdev(values)
    return None if spread == 0 else (values[-1] - fmean(values)) / spread


def expected_move_pct(implied_vol_pct: float | None, days: int) -> float | None:
    """One-sigma move over `days` trading days implied by an annualized vol, in percent."""
    if implied_vol_pct is None:
        return None
    return implied_vol_pct * math.sqrt(days / TRADING_DAYS)


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def macro_score(hy_oas: Series, nfci: Series, usd: Series) -> tuple[float | None, str, list[str]]:
    """v0 heuristic: positive = easier conditions. Average of negated z-scores; missing inputs are listed."""
    parts, missing = [], []
    # One year of history each: 252 daily observations, or 52 for the weekly NFCI.
    for name, series, window in (("hy_oas", hy_oas, 252), ("nfci", nfci, 52), ("usd", usd, 252)):
        z = zscore(series, window)
        (parts.append(-z) if z is not None else missing.append(name))
    if len(parts) < 2:
        return None, "unavailable", missing
    score = fmean(parts)
    tilt = "risk_on" if score > 0.5 else "risk_off" if score < -0.5 else "neutral"
    return score, tilt, missing


def regime_v0(vix: float | None, vix3m: float | None, index_level: float | None, index_sma200: float | None) -> tuple[str, list[str]]:
    """v0 rule-based label from volatility level, term structure and trend. Not a validated model."""
    if vix is None or vix3m is None:
        return "unavailable", ["vix or vix3m missing"]
    reasons = []
    backwardation = vix3m < vix
    if vix >= 25 or backwardation:
        reasons.append("vix>=25" if vix >= 25 else "term structure inverted")
        return "stress", reasons
    above_trend = None if index_level is None or index_sma200 is None else index_level > index_sma200
    if vix < 18 and above_trend:
        return "calm_uptrend", ["vix<18", "contango", "index above 200d average"]
    if above_trend is None:
        reasons.append("trend unavailable")
    return "neutral", reasons or ["mixed inputs"]
