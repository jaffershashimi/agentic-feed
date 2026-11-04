# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 17; unique research runs: 8; paper-lab records: 16. Latest record session: 2026-11-04.

## Latest research run (holdout split)

Run key 89a5655834f3, code 53b600a, data through 2026-11-03. 27014 unique observations, 989739 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1164 | 28.9% | -6.1% | -9.4% to -3.6% | 1.58 | -34.6% | 65.8% |
| call_every_session | 1456 | 29.7% | -5.6% | -8.4% to -3.0% | 1.60 | -36.2% | 79.7% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1456 | 27.0% | -7.6% | -10.0% to -5.1% | 1.76 | -45.8% | 98.7% |
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

VIX 20.83, 20-day realized 11.37, VIX/RV20 1.83, VRP 9.46 vol points, VIX percentile (252d) 84.9, term 3m/1m 1.021, v0 regime neutral (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $1815.42).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1164 | 32.6% | -2.6% | -5.5% to 0.8% |
| call_above_sma200/stressed | 1164 | 20.6% | -10.9% | -14.0% to -8.1% |
| call_above_sma200/typical | 1164 | 31.9% | -3.5% | -6.8% to -0.5% |
| call_above_sma200/wide | 1164 | 27.9% | -6.3% | -9.3% to -3.5% |
| call_every_session/liquid | 1456 | 33.6% | -2.0% | -4.5% to 1.3% |
| call_every_session/stressed | 1456 | 21.6% | -10.8% | -13.3% to -8.6% |
| call_every_session/typical | 1456 | 32.2% | -3.0% | -5.6% to 0.3% |
| call_every_session/wide | 1456 | 28.2% | -6.0% | -8.8% to -3.1% |

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
