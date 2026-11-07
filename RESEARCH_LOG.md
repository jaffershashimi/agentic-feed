# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 22; unique research runs: 11; paper-lab records: 21. Latest record session: 2026-11-06.

## Latest research run (holdout split)

Run key f2a527b69f24, code ed3bff5, data through 2026-11-06. 27023 unique observations, 990294 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1167 | 28.8% | -6.1% | -9.2% to -3.9% | 1.58 | -34.6% | 67.6% |
| call_every_session | 1459 | 29.6% | -5.7% | -8.1% to -3.1% | 1.60 | -36.2% | 80.7% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1459 | 27.0% | -7.6% | -10.0% to -5.6% | 1.76 | -45.8% | 98.7% |
| put_vix_above_25 | 193 | 25.4% | -13.4% | -19.0% to -7.0% | 1.43 | -51.6% | 76.1% |

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

VIX 25.64, 20-day realized 20.08, VIX/RV20 1.28, VRP 5.56 vol points, VIX percentile (252d) 95.2, term 3m/1m 0.959, v0 regime stress (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $2177.76).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1167 | 32.6% | -2.6% | -5.4% to 1.3% |
| call_above_sma200/stressed | 1167 | 20.6% | -10.9% | -13.7% to -8.0% |
| call_above_sma200/typical | 1167 | 31.8% | -3.6% | -6.5% to -0.1% |
| call_above_sma200/wide | 1167 | 27.9% | -6.4% | -9.4% to -3.7% |
| call_every_session/liquid | 1459 | 33.5% | -2.0% | -4.5% to 1.7% |
| call_every_session/stressed | 1459 | 21.6% | -10.9% | -12.7% to -8.3% |
| call_every_session/typical | 1459 | 32.1% | -3.1% | -5.6% to 0.1% |
| call_every_session/wide | 1459 | 28.1% | -6.0% | -8.6% to -3.0% |

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
| 2026-11-06 | -6.1% | 28.8% | -7.6% | 43.1% | 25.64 | 5.56 |
