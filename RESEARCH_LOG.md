# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 37; unique research runs: 17; paper-lab records: 36. Latest record session: 2026-11-17.

## Latest research run (holdout split)

Run key 51701d2874c8, code 2f31fb9, data through 2026-11-16. 27041 unique observations, 988474 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1173 | 28.6% | -6.3% | -9.6% to -3.7% | 1.58 | -34.8% | 68.4% |
| call_every_session | 1465 | 29.5% | -5.8% | -8.7% to -2.5% | 1.59 | -36.3% | 81.2% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1465 | 27.0% | -7.6% | -9.6% to -5.4% | 1.76 | -46.0% | 98.7% |
| put_vix_above_25 | 193 | 25.4% | -13.4% | -19.0% to -6.7% | 1.43 | -51.6% | 76.1% |

Tail stress (TAIL_STRESS_SYNTHETIC: IV x1.6, overnight gap -7.0%): call_every_session mean -60.8%, p05 -81.4%; put_every_session mean 260.0%, p05 142.8%.

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

VIX 22.91, 20-day realized 23.01, VIX/RV20 1.00, VRP -0.10 vol points, VIX percentile (252d) 90.1, term 3m/1m 1.005, v0 regime neutral (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $1956.45).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1173 | 32.4% | -2.8% | -5.6% to 1.0% |
| call_above_sma200/stressed | 1173 | 20.5% | -11.0% | -13.6% to -8.4% |
| call_above_sma200/typical | 1173 | 31.6% | -3.7% | -6.7% to 0.1% |
| call_above_sma200/wide | 1173 | 27.7% | -6.5% | -9.8% to -3.4% |
| call_every_session/liquid | 1465 | 33.4% | -2.2% | -4.3% to 1.3% |
| call_every_session/stressed | 1465 | 21.5% | -10.9% | -13.0% to -8.4% |
| call_every_session/typical | 1465 | 32.0% | -3.2% | -5.7% to -0.0% |
| call_every_session/wide | 1465 | 28.0% | -6.2% | -8.4% to -3.7% |

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
