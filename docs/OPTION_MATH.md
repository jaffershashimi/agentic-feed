# Option math and stop/target EV (P2-2)

`feed_pipeline/optionmath.py` is standard-library Python. It has no data dependency and makes no network calls.

- **`bsm()`:** Black-Scholes-Merton price, delta, gamma, vega (per 1.00 of volatility) and theta (per year), with a continuous dividend yield.
- **`implied_vol()`:** Newton's method with a bisection fallback. It returns `None` when no volatility in [0.0001, 5] fits the price (for example, a price below intrinsic value). It also returns `None` when moving volatility 10% either way changes the price by less than half a quote tick. In that case many volatilities fit the same quoted price, so any single answer would be invented.
- **`touch_probability()`:** the reflection-principle probability that GBM touches a level before time *t*, with drift.
- **`premium_martingale()`:** treats the premium itself as a driftless GBM. The target-first probability is then stop / (target + stop): about one-third for +30% / −15%. This is the closed-form check.
- **`option_structure()`:** simulates the underlying (GBM, or Merton jump-diffusion), reprices the option with BSM as time decays (so theta is included), pays the ask on entry and exits at the bid. It returns `p_target_first`, `p_stop_first`, `p_time_exit`, `ev_pct`, the standard error, whether a stated EV threshold passes, and the list of assumptions. A level crossed between grid checks fills at the next check's price, never at the level.
- **Command line:** `python3 -m feed_pipeline.optionmath ev --spot 100 --strike 102 --days 21 --iv 0.35 --target 0.30 --stop 0.15 --hold-days 10 --spread-pct 0.04 --threshold-pct 3`

**Example (model output, not a forecast):** at-the-money-ish 21-day calls, IV 35%, a 4% spread, +30%/−15% exits and a 10-day hold. The target is hit first in about 29% of paths, the EV is about −3.3% and the threshold fails. Spread and theta make a driftless structure lose money.

A model probability is not a market probability. Constant volatility, lognormal paths and European repricing are simplifications. This module is a calculator, not a validated signal; it is listed as "untested" on the signal scorecard.
