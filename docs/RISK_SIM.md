# Rule-level risk simulator (P4-5)

`feed_pipeline/risksim.py` runs a seeded Monte Carlo of a rule set expressed in percentages:
- position size (fraction of equity per trade);
- stop and target (fractions of the position);
- trades per day and stop-after-first-loss;
- a drawdown brake;
- a gap model: a losing trade can gap through its stop and lose `gap_loss`;
- a round-trip cost.

It contains no account values or positions. Win rates are assumptions, not measured edge.

**Outputs:**
- the probability the brake trips on day one (from a fresh peak) and within the horizon;
- the probability of reaching a deeper ruin level;
- max-drawdown percentiles (capped by the brake, because trading stops) and terminal-equity percentiles;
- the expected return per trade and the breakeven win rate after costs and gaps;
- full Kelly, a half-Kelly cap, and whether the size exceeds that cap;
- the worst single-trade and single-day loss.

**Published file:** `feed/risk-sim.json` (schema `risk-sim/1`). The publish workflow rebuilds it with a fixed seed, so an unchanged rule set produces no new commit. It covers the documented desk rules (10% and 15% size, 15% stop, 30% target, 2 trades a day, stop after the first loss, 15% brake) under assumed win rates of 30%, 35% and 40%. It also assumes 5% of losses gap and lose the whole premium, and a 2% round-trip cost.

**What it shows today (model output):**
- Under those assumptions, the breakeven win rate is about 43%. At 30–40% the expected return per trade is negative and the brake eventually trips.
- At 10% size, no single day can trip the brake: the worst day loses 10.2%.
- At 15% size, one gapped loss (15.3%) can trip it on day one, about 4% of days at a 40% win rate.

**Tests** (in `tests/test_risksim.py`):
- input validation;
- the Kelly and breakeven closed forms;
- exact compounding when every trade wins;
- the mean terminal equity against the analytic value;
- the first-day brake probability against its closed form;
- seeded reproducibility;
- a public-safety check on the report.
