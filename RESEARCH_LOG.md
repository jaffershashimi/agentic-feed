# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 53; unique research runs: 25; paper-lab records: 52. Latest record session: 2026-11-25.

## Latest research run (holdout split)

Run key 1d8dbbd9094b, code 186e16b, data through 2026-11-25. 27064 unique observations, 988657 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1180 | 28.5% | -6.4% | -9.1% to -3.7% | 1.58 | -34.8% | 69.0% |
| call_every_session | 1472 | 29.3% | -5.9% | -8.1% to -3.3% | 1.59 | -36.2% | 81.6% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1472 | 27.0% | -7.5% | -9.8% to -5.0% | 1.77 | -46.0% | 98.8% |
| put_vix_above_25 | 195 | 25.1% | -13.6% | -18.9% to -6.9% | 1.43 | -52.2% | 76.6% |

Tail stress (TAIL_STRESS_SYNTHETIC: IV x1.6, overnight gap -7.0%): call_every_session mean -60.8%, p05 -81.4%; put_every_session mean 259.9%, p05 142.8%.

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

VIX 28.29, 20-day realized 31.71, VIX/RV20 0.89, VRP -3.42 vol points, VIX percentile (252d) 98.0, term 3m/1m 0.929, v0 regime stress (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $2339.51).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1180 | 32.3% | -2.9% | -5.9% to 1.2% |
| call_above_sma200/stressed | 1180 | 20.3% | -11.1% | -13.6% to -8.1% |
| call_above_sma200/typical | 1180 | 31.4% | -3.9% | -6.9% to -0.7% |
| call_above_sma200/wide | 1180 | 27.6% | -6.6% | -9.8% to -3.0% |
| call_every_session/liquid | 1472 | 33.3% | -2.2% | -4.8% to 0.8% |
| call_every_session/stressed | 1472 | 21.4% | -11.0% | -13.2% to -8.5% |
| call_every_session/typical | 1472 | 31.9% | -3.2% | -5.6% to -0.0% |
| call_every_session/wide | 1472 | 27.9% | -6.3% | -8.6% to -3.2% |

## Trend (one row per unique research run, newest last)

| session | call above SMA200 mean | its win rate | put every session mean | breakeven (size 10%) | VIX | VRP |
|---|---|---|---|---|---|---|
| 2026-10-26 | -6.0% | 29.0% | -7.6% | 43.1% | 19.45 | 9.53 |
| 2026-10-27 | -6.0% | 29.0% | -7.6% | 43.1% | 18.39 | 8.47 |
| 2026-10-28 | -6.0% | 29.0% | -7.6% | 43.1% | 17.52 | 7.40 |
| 2026-10-29 | -6.0% | 29.0% | -7.6% | 43.1% | 16.91 | 6.81 |
| 2026-10-30 | -6.1% | 28.9% | -7.5% | 43.1% | 17.20 | 7.04 |
| 2026-11-02 | -6.1% | 28.9% | -7.6% | 43.1% | 19.42 | 7.64 |
| 2026-11-03 | -6.1% | 28.9% | -7.6% | 43.1% | 21.15 | 9.63 |
| 2026-11-04 | -6.1% | 28.9% | -7.6% | 43.1% | 20.83 | 9.46 |
| 2026-11-05 | -6.1% | 28.8% | -7.5% | 43.1% | 16.61 | 3.69 |
| 2026-11-06 | -6.1% | 28.8% | -7.6% | 43.1% | 28.18 | 7.96 |
| 2026-11-09 | -6.1% | 28.8% | -7.6% | 43.1% | 25.64 | 5.56 |
| 2026-11-10 | -6.2% | 28.8% | -7.6% | 43.1% | 19.92 | -2.19 |
| 2026-11-11 | -6.2% | 28.7% | -7.6% | 43.1% | 19.46 | -2.66 |
| 2026-11-12 | -6.2% | 28.7% | -7.5% | 43.1% | 17.49 | -4.94 |
| 2026-11-13 | -6.2% | 28.7% | -7.5% | 43.1% | 18.70 | -3.70 |
| 2026-11-16 | -6.3% | 28.7% | -7.5% | 43.1% | 19.30 | -2.83 |
| 2026-11-17 | -6.3% | 28.6% | -7.6% | 43.1% | 22.91 | -0.10 |
| 2026-11-18 | -6.3% | 28.6% | -7.6% | 43.1% | 25.46 | 2.33 |
| 2026-11-19 | -6.3% | 28.6% | -7.5% | 43.1% | 24.56 | 1.63 |
| 2026-11-20 | -6.3% | 28.6% | -7.5% | 43.1% | 28.44 | 4.89 |
| 2026-11-20 | -6.3% | 28.5% | -7.5% | 43.1% | 35.89 | 9.36 |
| 2026-11-23 | -6.3% | 28.5% | -7.5% | 43.1% | 35.89 | 9.36 |
| 2026-11-24 | -6.4% | 28.5% | -7.5% | 43.1% | 26.62 | -5.34 |
| 2026-11-25 | -6.4% | 28.5% | -7.5% | 43.1% | 26.36 | -5.43 |
| 2026-11-25 | -6.4% | 28.5% | -7.5% | 43.1% | 28.29 | -3.42 |
