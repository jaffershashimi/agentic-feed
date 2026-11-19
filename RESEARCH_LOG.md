# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 41; unique research runs: 19; paper-lab records: 40. Latest record session: 2026-11-18.

## Latest research run (holdout split)

Run key 3976be4694e3, code 6d76641, data through 2026-11-18. 27047 unique observations, 987665 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1175 | 28.6% | -6.3% | -9.0% to -3.8% | 1.57 | -34.8% | 68.4% |
| call_every_session | 1467 | 29.4% | -5.8% | -8.2% to -3.4% | 1.59 | -36.3% | 81.2% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1467 | 27.1% | -7.5% | -9.8% to -5.0% | 1.77 | -46.0% | 98.7% |
| put_vix_above_25 | 193 | 25.4% | -13.4% | -18.3% to -6.9% | 1.43 | -51.6% | 76.1% |

Tail stress (TAIL_STRESS_SYNTHETIC: IV x1.6, overnight gap -7.0%): call_every_session mean -60.8%, p05 -81.4%; put_every_session mean 260.1%, p05 142.8%.

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

VIX 24.56, 20-day realized 22.93, VIX/RV20 1.07, VRP 1.63 vol points, VIX percentile (252d) 92.9, term 3m/1m 0.989, v0 regime stress (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $2071.02).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1175 | 32.3% | -2.8% | -5.8% to 0.6% |
| call_above_sma200/stressed | 1175 | 20.4% | -11.1% | -13.8% to -7.9% |
| call_above_sma200/typical | 1175 | 31.6% | -3.8% | -6.7% to -0.2% |
| call_above_sma200/wide | 1175 | 27.7% | -6.6% | -9.4% to -3.5% |
| call_every_session/liquid | 1467 | 33.3% | -2.2% | -4.8% to 1.1% |
| call_every_session/stressed | 1467 | 21.5% | -11.0% | -13.1% to -8.7% |
| call_every_session/typical | 1467 | 32.0% | -3.2% | -5.7% to 0.3% |
| call_every_session/wide | 1467 | 28.0% | -6.2% | -8.7% to -3.3% |

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
| 2026-11-18 | -6.3% | 28.6% | -7.5% | 43.1% | 24.56 | 1.63 |
