"""Risk limits report: exposure, concentration, the day's result and drawdown from the equity peak, against tiered limits.

Institutions run a limit hierarchy with early-warning tiers before a hard stop, and measure drawdown from the high-water mark, not from the starting capital.
The tiers here are PROPOSED and REPORT ONLY (see engine/limits.py): they raise WARN (amber) or FAIL (red) in the desk pack and block nothing. The owner decides
whether any tier is ever enforced; a red tier is a call for the owner's decision, never an automatic action on the strategy's frozen rules.

Output is percentages only. Balances and position sizes in dollars stay out of the log.
"""
from engine import limits


def _tier(value, amber, red, worse_is_lower):
    if worse_is_lower:
        return "FAIL" if value <= red else ("WARN" if value <= amber else "OK")
    return "FAIL" if value >= red else ("WARN" if value >= amber else "OK")


def measure(account, positions, history_equity):
    """account: /v2/account dict. positions: /v2/positions list. history_equity: daily equity series (may hold None)."""
    equity = float(account["equity"])
    last = float(account.get("last_equity") or 0)
    mv = [abs(float(p.get("market_value") or 0)) for p in positions]
    series = [float(x) for x in (history_equity or []) if x is not None] + [equity]
    peak = max(series)
    rows = {
        "day_result_pct": round(100 * (equity - last) / last, 2) if last > 0 else None,
        "drawdown_from_peak_pct": round(100 * (equity / peak - 1), 2) if peak > 0 else None,
        "gross_exposure_pct": round(100 * sum(mv) / equity, 2) if equity > 0 else None,
        "largest_position_pct": round(100 * max(mv) / equity, 2) if mv and equity > 0 else 0.0,
        "open_positions": len(positions),
    }
    g = limits.get
    tiers = {
        "day_result_pct": _tier(rows["day_result_pct"], g("PROPOSED_DAILY_LOSS_AMBER_PCT"), g("PROPOSED_DAILY_LOSS_RED_PCT"), True) if rows["day_result_pct"] is not None else "OK",
        "drawdown_from_peak_pct": _tier(rows["drawdown_from_peak_pct"], g("PROPOSED_DRAWDOWN_AMBER_PCT"), g("PROPOSED_DRAWDOWN_RED_PCT"), True) if rows["drawdown_from_peak_pct"] is not None else "OK",
        "gross_exposure_pct": _tier(rows["gross_exposure_pct"], g("PROPOSED_GROSS_AMBER_PCT"), g("PROPOSED_GROSS_RED_PCT"), False) if rows["gross_exposure_pct"] is not None else "OK",
        "largest_position_pct": _tier(rows["largest_position_pct"], g("PROPOSED_LARGEST_AMBER_PCT"), g("PROPOSED_LARGEST_RED_PCT"), False),
    }
    order = {"OK": 0, "WARN": 1, "FAIL": 2}
    status = max(tiers.values(), key=lambda t: order[t])
    return {"status": status, "measures": rows, "tiers": tiers, "enforced": False}


def analyze(call):
    account = call("GET", "/v2/account")
    positions = call("GET", "/v2/positions")
    try:
        history = (call("GET", "/v2/account/portfolio/history?period=3M&timeframe=1D") or {}).get("equity") or []
    except Exception:
        history = []   # no history: drawdown is measured from today's equity only (reported as 0), and the other measures still work
    return measure(account, positions, history)
