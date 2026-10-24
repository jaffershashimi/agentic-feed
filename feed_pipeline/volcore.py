"""Volatility core (issue P2-1), index level. Standard library only; fail-soft; point-in-time.

From Cboe daily OHLC and the VIX family it computes realized volatility four ways (close-to-close, Parkinson,
Garman-Klass, Yang-Zhang), a GARCH(1,1) forecast, VIX rank and percentile from VIX's own history, the variance risk
premium, term-structure slopes and SKEW, and writes feed/vol-core.json (schema vol-core/1). Every value is computed
from observations dated on or before the last completed session; anything without enough history is null.

Per-ticker outputs need daily prices (P1-2) and our own option-implied-vol history (P1-7); until those exist this
covers the S&P 500 only.

    python3 -m feed_pipeline.volcore --out feed/vol-core.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from datetime import date, datetime, timezone

from . import calendar
from .sources import CBOE_URL, _parse_date, _parse_number, fetch_text

SCHEMA = "vol-core/1"
YEAR = 252
Bar = tuple  # (date, open, high, low, close)


def parse_ohlc(text: str) -> list:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty CSV")
    cols = {c.strip().upper(): c for c in rows[0] if c}
    need = ("DATE", "OPEN", "HIGH", "LOW", "CLOSE")
    if any(k not in cols for k in need):
        raise ValueError(f"unexpected columns {list(cols)}")
    bars = []
    for row in rows:
        values = [_parse_number(row[cols[k]]) for k in need[1:]]
        if any(v is None or v <= 0 for v in values):
            continue  # missing or non-positive values are skipped, never filled
        o, h, l, c = values
        if not (l <= min(o, c) and max(o, c) <= h):
            continue  # an inconsistent bar is dropped rather than repaired
        bars.append((_parse_date(row[cols["DATE"]]), o, h, l, c))
    bars.sort(key=lambda b: b[0])
    return bars


def _ann(variance: float) -> float:
    return math.sqrt(max(variance, 0.0) * YEAR) * 100


def close_to_close(bars: list, n: int = 20) -> float | None:
    if len(bars) < n + 1:
        return None
    r = [math.log(b[4] / a[4]) for a, b in zip(bars[-n - 1:], bars[-n:])]
    m = sum(r) / n
    return _ann(sum((x - m) ** 2 for x in r) / (n - 1))


def parkinson(bars: list, n: int = 20) -> float | None:
    if len(bars) < n:
        return None
    return _ann(sum(math.log(b[2] / b[3]) ** 2 for b in bars[-n:]) / (4 * math.log(2) * n))


def garman_klass(bars: list, n: int = 20) -> float | None:
    if len(bars) < n:
        return None
    v = sum(0.5 * math.log(b[2] / b[3]) ** 2 - (2 * math.log(2) - 1) * math.log(b[4] / b[1]) ** 2 for b in bars[-n:]) / n
    return _ann(v)


def yang_zhang(bars: list, n: int = 20) -> float | None:
    if len(bars) < n + 1:
        return None
    window, prev = bars[-n:], bars[-n - 1:-1]
    o = [math.log(b[1] / p[4]) for b, p in zip(window, prev)]  # overnight
    c = [math.log(b[4] / b[1]) for b in window]  # open to close
    rs = [math.log(b[2] / b[4]) * math.log(b[2] / b[1]) + math.log(b[3] / b[4]) * math.log(b[3] / b[1]) for b in window]
    var = lambda xs: sum((x - sum(xs) / n) ** 2 for x in xs) / (n - 1)  # noqa: E731
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    return _ann(var(o) + k * var(c) + (1 - k) * sum(rs) / n)


def garch11(returns: list) -> dict | None:
    """Gaussian GARCH(1,1) by maximum likelihood with variance targeting (grid search). None with < 250 returns."""
    if len(returns) < 250:
        return None
    mean = sum(returns) / len(returns)
    eps = [r - mean for r in returns]
    var = sum(e * e for e in eps) / len(eps)
    best = None
    for a in [x / 100 for x in range(1, 31)]:
        for b in [x / 100 for x in range(50, 99)]:
            if a + b >= 0.995:
                continue
            omega, h, ll = var * (1 - a - b), var, 0.0
            for e in eps:
                ll -= 0.5 * (math.log(h) + e * e / h)
                h = omega + a * e * e + b * h
            if best is None or ll > best[0]:
                best = (ll, a, b, omega, h)
    ll, a, b, omega, h_next = best
    persistence = a + b

    def horizon(days: int) -> float:  # mean variance over the next `days` days
        return sum(var + persistence ** i * (h_next - var) for i in range(days)) / days

    return {"alpha": a, "beta": b, "persistence": round(persistence, 3), "long_run_vol": round(_ann(var), 2),
            "vol_1d": round(_ann(h_next), 2), "vol_5d": round(_ann(horizon(5)), 2), "vol_21d": round(_ann(horizon(21)), 2),
            "exp_move_1d_pct": round(math.sqrt(h_next) * 100, 3), "exp_move_5d_pct": round(math.sqrt(horizon(5) * 5) * 100, 3)}


def rank_and_percentile(values: list, n: int = 252) -> tuple:
    window = values[-n:]
    if len(window) < n:
        return None, None
    x, lo, hi = window[-1], min(window), max(window)
    rank = None if hi == lo else 100 * (x - lo) / (hi - lo)
    pctile = 100 * sum(1 for v in window if v <= x) / len(window)
    return rank, pctile


def _r(x, d=2):
    return None if x is None else round(x, d)


def compute(spx: list, vix: list, vix9d: list, vix3m: list, skew: list, session: date) -> dict:
    """All inputs are (date, value) or OHLC bars; only rows dated <= session are used."""
    cut = lambda rows: [r for r in rows if r[0] <= session]  # noqa: E731
    spx, vix, vix9d, vix3m, skew = cut(spx), cut(vix), cut(vix9d), cut(vix3m), cut(skew)
    last = lambda rows: rows[-1][-1] if rows else None  # noqa: E731
    v, v9, v3 = last(vix), last(vix9d), last(vix3m)
    rv = {"close_to_close": close_to_close(spx), "parkinson": parkinson(spx), "garman_klass": garman_klass(spx), "yang_zhang": yang_zhang(spx)}
    returns = [math.log(b[4] / a[4]) for a, b in zip(spx, spx[1:])][-1000:]
    rank, pctile = rank_and_percentile([x[1] for x in vix])
    cc = rv["close_to_close"]
    return {
        "schema": SCHEMA, "session": session.isoformat(), "underlying": "SPX", "implied": "VIX (30-day)",
        "as_of": {"spx": spx[-1][0].isoformat() if spx else None, "vix": vix[-1][0].isoformat() if vix else None},
        "rv20_pct": {k: _r(x) for k, x in rv.items()},
        "garch11": garch11(returns),
        "iv_rv_ratio": _r(v / cc, 3) if v and cc else None,
        "vrp_vol_pts": _r(v - cc) if v is not None and cc is not None else None,
        "vrp_variance": _r((v * v - cc * cc) / 100) if v is not None and cc is not None else None,
        "iv_rank_252": _r(rank, 1), "iv_pctile_252": _r(pctile, 1),
        "term_slope_3m_minus_1m": _r(v3 - v) if v3 is not None and v is not None else None,
        "term_slope_1m_minus_9d": _r(v - v9) if v is not None and v9 is not None else None,
        "skew_index": _r(last(skew)),
        "exp_move_1d_pct": _r(v / math.sqrt(YEAR), 3) if v is not None else None,
        "exp_move_5d_pct": _r(v * math.sqrt(5 / YEAR), 3) if v is not None else None,
        "note": "Context only, not a validated signal. Null means not enough history or data unavailable.",
    }


def _series(text: str, name: str) -> list:
    from .sources import parse_cboe
    return parse_cboe(text, name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    session = calendar.last_completed_session(datetime.now(timezone.utc))
    inputs, errors = {}, []
    for name in ("SPX", "VIX", "VIX9D", "VIX3M", "SKEW"):
        try:
            text = fetch_text(CBOE_URL.format(name=name))
            inputs[name] = parse_ohlc(text) if name == "SPX" else _series(text, name)
        except Exception as exc:  # fail-soft: report and continue with nulls
            inputs[name], errors = [], errors + [f"{name}: {type(exc).__name__}"]
    out = compute(inputs["SPX"], inputs["VIX"], inputs["VIX9D"], inputs["VIX3M"], inputs["SKEW"], session)
    out["errors"] = errors
    text = json.dumps(out, sort_keys=True, separators=(",", ":"))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
