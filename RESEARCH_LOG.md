# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Runs recorded: 1. Latest session: 2026-10-23.

## Latest research run (holdout split)

Run key 3340c71e8597, code f0c14ef, data through 2026-10-23. 26993 unique observations, 989601 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1157 | 29.0% | -6.0% | -10.0% to -3.5% | 1.58 | -34.6% | 65.2% |
| call_every_session | 1449 | 29.8% | -5.6% | -8.2% to -2.8% | 1.59 | -36.2% | 79.3% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1449 | 26.9% | -7.6% | -9.8% to -4.9% | 1.76 | -46.0% | 98.7% |
| put_vix_above_25 | 193 | 25.4% | -13.4% | -18.8% to -6.9% | 1.43 | -51.6% | 76.1% |

Tail stress (TAIL_STRESS_SYNTHETIC: IV x1.6, overnight gap -7.0%): call_every_session mean -60.8%, p05 -81.4%; put_every_session mean 259.9%, p05 142.5%.

## Rule-level risk simulation

| rule set | assumed win rate | breakeven | expected per trade | P(brake) |
|---|---|---|---|---|
| size_score_8 | 30% | 43.1% | -0.65% | 100% |
| size_score_8 | 35% | 43.1% | -0.40% | 100% |
| size_score_8 | 40% | 43.1% | -0.15% | 100% |
| size_score_9_10 | 30% | 43.1% | -0.97% | 100% |
| size_score_9_10 | 35% | 43.1% | -0.60% | 100% |
| size_score_9_10 | 40% | 43.1% | -0.23% | 100% |

## Volatility context

VIX 19.45, 20-day realized 9.92, VIX/RV20 1.96, VRP 9.53 vol points, VIX percentile (252d) 78.2, term 3m/1m 1.053, v0 regime neutral (unvalidated).

## Trend (one row per recorded run, newest last)

| session | call above SMA200 mean | its win rate | put every session mean | breakeven (size 10%) | VIX | VRP |
|---|---|---|---|---|---|---|
| 2026-10-23 | -6.0% | 29.0% | -7.6% | 43.1% | 19.45 | 9.53 |
