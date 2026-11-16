"""Transaction-cost model for a small paper order, so the backtest's flat 5 bps can be checked against something that scales with size and liquidity.

Model: expected cost per side (basis points of notional) = half the quoted spread + market impact.
  * half spread: a floor of `SPREAD_FLOOR_BPS` for liquid stocks; wider (by price tier) for cheaper names.
  * impact: the square-root law, impact = Y * daily_vol * sqrt(notional / ADV_dollars), with Y of about 1 (Almgren et al. 2005, Toth et al. 2011;
    "Square-root impact law" is documented across equity metaorder datasets). Tiny orders in liquid names land near zero.
Realised slippage compares a fill to a reference price. Used by the S-042 forward report; it never changes an order.
"""
import math

SPREAD_FLOOR_BPS = 1.0
IMPACT_Y = 1.0


def half_spread_bps(price):
    """Coarse tier by price (cheaper stocks have wider relative spreads in cents terms). A floor, not a quote."""
    if price >= 50:
        return SPREAD_FLOOR_BPS
    if price >= 20:
        return 2.0
    if price >= 10:
        return 3.5
    return 6.0


def sqrt_impact_bps(notional, adv_dollars, daily_vol, y=IMPACT_Y):
    if notional <= 0 or adv_dollars <= 0 or daily_vol < 0:
        raise ValueError("notional and ADV must be positive, volatility non-negative")
    return y * daily_vol * math.sqrt(notional / adv_dollars) * 1e4


def expected_cost_bps(price, notional, adv_dollars, daily_vol):
    """One side. A round trip costs about twice this."""
    return half_spread_bps(price) + sqrt_impact_bps(notional, adv_dollars, daily_vol)


def daily_vol(closes, n=20):
    xs = closes[-(n + 1):]
    if len(xs) < n + 1:
        raise ValueError("not enough closes")
    r = [xs[i] / xs[i - 1] - 1.0 for i in range(1, len(xs))]
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))


def realized_slippage_bps(side, reference, fill):
    """Positive means worse than the reference (paid more on a buy, received less on a sell)."""
    if reference <= 0 or fill <= 0:
        raise ValueError("prices must be positive")
    raw = (fill / reference - 1.0) * 1e4
    return raw if side == "buy" else -raw


def summarize(values):
    vals = sorted(values)
    if not vals:
        return {"n": 0}
    n = len(vals)
    return {"n": n, "mean_bps": round(sum(vals) / n, 2), "median_bps": round(vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2, 2), "worst_bps": round(vals[-1], 2)}
