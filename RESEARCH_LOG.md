# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 5; unique research runs: 2; paper-lab records: 4. Latest record session: 2026-10-27.

## Latest research run (holdout split)

Run key 04052abe1163, code 6843276, data through 2026-10-26. 26996 unique observations, 988629 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1158 | 29.0% | -6.0% | -10.3% to -3.8% | 1.58 | -34.6% | 65.2% |
| call_every_session | 1450 | 29.8% | -5.6% | -8.3% to -3.1% | 1.59 | -36.2% | 79.3% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1450 | 27.0% | -7.6% | -10.0% to -5.3% | 1.76 | -46.0% | 98.7% |
| put_vix_above_25 | 193 | 25.4% | -13.4% | -18.8% to -6.1% | 1.43 | -51.6% | 76.1% |

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

VIX 18.39, 20-day realized 9.92, VIX/RV20 1.85, VRP 8.47 vol points, VIX percentile (252d) 66.7, term 3m/1m 1.084, v0 regime neutral (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $1625.66).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1158 | 32.8% | -2.5% | -5.2% to 2.1% |
| call_above_sma200/stressed | 1158 | 20.7% | -10.8% | -13.5% to -8.4% |
| call_above_sma200/typical | 1158 | 32.0% | -3.4% | -6.5% to -0.3% |
| call_above_sma200/wide | 1158 | 28.1% | -6.3% | -9.0% to -3.4% |
| call_every_session/liquid | 1450 | 33.7% | -1.9% | -4.6% to 1.5% |
| call_every_session/stressed | 1450 | 21.7% | -10.8% | -13.3% to -8.8% |
| call_every_session/typical | 1450 | 32.3% | -2.9% | -5.8% to 0.6% |
| call_every_session/wide | 1450 | 28.3% | -5.9% | -8.6% to -3.5% |

## Trend (one row per unique research run, newest last)

| session | call above SMA200 mean | its win rate | put every session mean | breakeven (size 10%) | VIX | VRP |
|---|---|---|---|---|---|---|
| 2026-10-26 | -6.0% | 29.0% | -7.6% | 43.1% | 19.45 | 9.53 |
| 2026-10-27 | -6.0% | 29.0% | -7.6% | 43.1% | 18.39 | 8.47 |
