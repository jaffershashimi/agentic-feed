"""Data-quality gate for daily bars and a corporate-action look-ahead. Research and paper only.

Why: a pullback rule that buys the most oversold names is exactly what a bad bar looks like. An unadjusted split, a one-day print error, a gap
in the series or a name that is about to reverse split would all show up as a perfect "oversold" signal. This gate quarantines such symbols
before the signal sees them and says why, so the daily report can list them. It never repairs data: a bad symbol is skipped, not patched.

Checks per symbol (codes):
  NONPOSITIVE      a price at or below zero
  OHLC_INCONSISTENT high below low, or close outside the high/low range (1 percent tolerance for adjusted data)
  UNSORTED         dates not strictly increasing (duplicates included)
  GAP              more than `max_gap_days` calendar days between two consecutive bars inside the window
  SPLIT_SUSPECT    one-day move of at least 35 percent whose price ratio sits within 1.5 percent of a classic split ratio
  ZERO_VOLUME      three or more zero-volume days in the last 20 bars
  FLAT_TAPE        20 identical closes in a row
  STALE            last bar is not the as-of session (reported separately from quality)

Corporate actions: `upcoming_actions` asks the Alpaca corporate-actions data endpoint for splits, mergers and spin-offs whose ex date falls in
the next `days` days. Fail-soft: any error returns an empty result with a reason, because this is an extra safety net, not a dependency.
"""
import urllib.parse
from datetime import date, timedelta

SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0)
ACTION_TYPES = "forward_split,reverse_split,cash_merger,stock_merger,spin_off"


def _near_split(ratio):
    for k in SPLIT_RATIOS:
        for r in (k, 1.0 / k):
            if abs(ratio / r - 1.0) <= 0.015:
                return True
    return False


def check_series(d, asof=None, window=300, max_gap_days=6):
    """Return a sorted list of issue codes for one symbol's bars (dict of parallel lists). Only the last `window` bars are inspected."""
    n = len(d["c"])
    if n == 0:
        return ["EMPTY"]
    lo = max(0, n - window)
    issues = set()
    dates = d["date"][lo:]
    for i in range(len(dates)):
        o, h, l, c = d["o"][lo + i], d["h"][lo + i], d["l"][lo + i], d["c"][lo + i]
        if min(o, h, l, c) <= 0:
            issues.add("NONPOSITIVE")
            continue
        if h < l * 0.999 or c > h * 1.01 or c < l * 0.99:
            issues.add("OHLC_INCONSISTENT")
    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            issues.add("UNSORTED")
        else:
            gap = (date.fromisoformat(dates[i]) - date.fromisoformat(dates[i - 1])).days
            if gap > max_gap_days:
                issues.add("GAP")
    cs = d["c"][lo:]
    for i in range(1, len(cs)):
        if cs[i - 1] > 0 and cs[i] > 0:
            ratio = cs[i] / cs[i - 1]
            if abs(ratio - 1.0) >= 0.35 and _near_split(ratio):
                issues.add("SPLIT_SUSPECT")
    if sum(1 for v in d["v"][-20:] if v <= 0) >= 3:
        issues.add("ZERO_VOLUME")
    if len(cs) >= 20 and len(set(cs[-20:])) == 1:
        issues.add("FLAT_TAPE")
    return sorted(issues)


def gate(data, symbols, asof=None, **kw):
    """Split `symbols` into (clean, quarantine). `quarantine` maps symbol to issue codes. Symbols with no data are ignored here (reported as stale/short upstream)."""
    clean, bad = [], {}
    for s in symbols:
        d = data.get(s)
        if not d:
            continue
        iss = check_series(d, asof, **kw)
        (bad.__setitem__(s, iss) if iss else clean.append(s))
    return clean, bad


def upcoming_actions(call, symbols, asof, days=7):
    """{'actions': {sym: [{'type', 'ex_date'}]}, 'error': None|str}. Never raises."""
    found, err = {}, None
    syms = sorted(set(symbols))
    start, end = asof, asof + timedelta(days=days)
    try:
        for i in range(0, len(syms), 50):
            tok = None
            while True:
                q = {"symbols": ",".join(syms[i:i + 50]), "types": ACTION_TYPES, "start": start.isoformat(), "end": end.isoformat(), "limit": "1000"}
                if tok:
                    q["page_token"] = tok
                j = call("GET", "/v1/corporate-actions?" + urllib.parse.urlencode(q), None, "data")
                for kind, rows in (j.get("corporate_actions") or {}).items():
                    for r in rows or []:
                        sym, ex = r.get("symbol") or r.get("initial_symbol") or r.get("target_symbol"), r.get("ex_date") or r.get("process_date")
                        if sym and ex and start.isoformat() <= ex <= end.isoformat():
                            found.setdefault(sym, []).append({"type": kind, "ex_date": ex})
                tok = j.get("next_page_token")
                if not tok:
                    break
    except Exception as exc:  # fail-soft by design
        err = str(exc)[:160]
    return {"actions": found, "error": err}
