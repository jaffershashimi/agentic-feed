# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 56; unique research runs: 26; paper-lab records: 55. Latest record session: 2026-11-27.

## Latest research run (holdout split)

Run key cf74253ec42a, code 3a422f2, data through 2026-11-27. 27067 unique observations, 988839 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1181 | 28.4% | -6.4% | -9.0% to -3.9% | 1.57 | -34.8% | 69.0% |
| call_every_session | 1473 | 29.3% | -5.9% | -8.6% to -3.4% | 1.59 | -36.2% | 81.6% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1473 | 27.1% | -7.4% | -10.0% to -5.3% | 1.77 | -46.0% | 98.8% |
| put_vix_above_25 | 195 | 25.1% | -13.6% | -19.0% to -7.2% | 1.43 | -52.2% | 76.6% |

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

VIX 23.81, 20-day realized 32.47, VIX/RV20 0.73, VRP -8.66 vol points, VIX percentile (252d) 88.1, term 3m/1m 1.000, v0 regime neutral (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $2000.63).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1181 | 32.3% | -2.9% | -5.8% to 0.8% |
| call_above_sma200/stressed | 1181 | 20.3% | -11.1% | -13.5% to -8.1% |
| call_above_sma200/typical | 1181 | 31.4% | -3.9% | -6.9% to -0.5% |
| call_above_sma200/wide | 1181 | 27.6% | -6.6% | -9.7% to -3.0% |
| call_every_session/liquid | 1473 | 33.3% | -2.2% | -4.8% to 0.9% |
| call_every_session/stressed | 1473 | 21.4% | -11.0% | -13.2% to -8.9% |
| call_every_session/typical | 1473 | 31.9% | -3.3% | -5.9% to -0.1% |
| call_every_session/wide | 1473 | 27.8% | -6.3% | -8.8% to -3.5% |

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
| 2026-11-27 | -6.4% | 28.5% | -7.5% | 43.1% | 26.80 | -4.91 |
| 2026-11-27 | -6.4% | 28.4% | -7.4% | 43.1% | 23.81 | -8.66 |
