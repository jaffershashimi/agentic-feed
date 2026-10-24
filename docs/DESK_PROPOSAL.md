# Proposal: how the desk consumes the feed (P4-3)

This is a proposal for the owner, who edits the trading rules. Nothing here changes a rule by itself. The wording below is generic, so the private rules file can adopt it as written or change it. The evidence is the published market and model files only: no account data.

## 1. Freshness gate (adopt as written)

**Before:** "Use the feed if it is less than 24 hours old."

**After:** "Use the feed only if `schema` is `agentic-feed/1`, `data_health.core_ok` is true, the file is at most 6,000 bytes, and `session` equals the last completed NYSE session. Otherwise ignore it and work exactly as before."

**Evidence:** `docs/DESK_CONSUMER.md`. A flat 24-hour rule rejects every Monday-morning and post-holiday read.

## 2. Calendar blackouts (adopt as written)

**Before:** the blackout dates are typed by hand.

**After:** "Add every `events` entry (fomc_statement, early_close, market_closed, monthly_opex) with its PT time to the day's blackouts if missing. A hand-typed date and a feed date that disagree are both kept, and the run notes the conflict."

**Evidence:** the NYSE 2025–2027 table in `feed_pipeline/calendar.py`, which has tests.

## 3. Index option richness (proposal: a skip condition only)

**Before:** no rule on how much the option premium costs relative to realized movement.

**After:** "When `vol.vix_over_rv20` is above 1.5, do not buy an index (SPY/QQQ) option outright. Use the stock or ETF, a defined-risk debit spread, or wait. This only adds a reason to skip; it never raises size."

**Evidence:** `feed/vol-core.json` on Oct 23, 2026 showed VIX/RV20 of 1.96 and a VRP of 9.5 vol points (VIX percentile 78). The research runner prices options from the VIX, so a rich VIX makes the modeled premium expensive against what the index then moves. This is a cost argument, not a validated signal; the scorecard lists it as untested.

## 4. Win probability used by the entry math (proposal: tightening only)

**Before:** "EV% uses the calibration priors for the conviction score."

**After:** "Until the calibration file holds at least 20 measured trades, the win probability used for an index long option is the lower of the calibration prior and the research runner's holdout win rate for the closest pre-registered rule (`feed/research.json`, `metrics.<rule>.holdout.win_rate`). When the research file is missing, use the prior as before."

**Evidence:** `feed/research.json` (run 3340c71e8597, data through Oct 23, 2026, holdout 2021 onward). These rules buy SPX options with the +30% target and −15% stop:

| rule | n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200 | 1157 | 29.0% | −6.0% | −10.0% to −3.5% |
| call_every_session | 1449 | 29.8% | −5.6% | −8.2% to −2.8% |
| put_every_session | 1449 | 26.9% | −7.6% | −9.8% to −4.9% |
| put_vix_above_25 | 193 | 25.4% | −13.4% | −18.8% to −6.9% |

`feed/risk-sim.json` gives a breakeven win rate of 43.1% for this structure with a 5% gap-through-stop and 2% round-trip cost.

**Limits:**
- Option prices are Black-Scholes values from the VIX, with exits checked at daily closes.
- Index options are only a proxy for single names.
- The desk's own selection may differ.

The rule would still let a strong single-name catalyst through when its own calibrated prior is lower. Its effect is to stop an untested prior from overstating index win rates.

## 5. Later (not proposed yet)

Replace the win-probability priors with event-study evidence only after P3 promotes a signal on `docs/SIGNAL_SCORECARD.md`.
