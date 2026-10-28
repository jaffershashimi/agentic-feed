# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 6; unique research runs: 3; paper-lab records: 5. Latest record session: 2026-10-27.

## Latest research run (holdout split)

Run key d3a75c8e90c6, code 9aa6c18, data through 2026-10-27. 26999 unique observations, 988814 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1159 | 29.0% | -6.0% | -10.1% to -3.8% | 1.58 | -34.6% | 65.2% |
| call_every_session | 1451 | 29.8% | -5.6% | -8.3% to -3.3% | 1.59 | -36.2% | 79.3% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1451 | 27.0% | -7.6% | -9.5% to -5.6% | 1.76 | -46.0% | 98.7% |
| put_vix_above_25 | 193 | 25.4% | -13.4% | -19.0% to -7.0% | 1.43 | -51.6% | 76.1% |

Tail stress (TAIL_STRESS_SYNTHETIC: IV x1.6, overnight gap -7.0%): call_every_session mean -60.8%, p05 -81.4%; put_every_session mean 260.0%, p05 142.5%.

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

VIX 17.52, 20-day realized 10.12, VIX/RV20 1.73, VRP 7.40 vol points, VIX percentile (252d) 57.1, term 3m/1m 1.117, v0 regime calm_uptrend (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $1558.01).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1159 | 32.8% | -2.5% | -5.8% to 1.2% |
| call_above_sma200/stressed | 1159 | 20.7% | -10.8% | -13.5% to -8.0% |
| call_above_sma200/typical | 1159 | 32.0% | -3.4% | -6.4% to 0.3% |
| call_above_sma200/wide | 1159 | 28.0% | -6.3% | -9.1% to -3.1% |
| call_every_session/liquid | 1451 | 33.7% | -1.9% | -4.7% to 0.9% |
| call_every_session/stressed | 1451 | 21.7% | -10.8% | -13.2% to -8.7% |
| call_every_session/typical | 1451 | 32.3% | -2.9% | -5.7% to 0.2% |
| call_every_session/wide | 1451 | 28.3% | -6.0% | -8.3% to -3.4% |

## Trend (one row per unique research run, newest last)

| session | call above SMA200 mean | its win rate | put every session mean | breakeven (size 10%) | VIX | VRP |
|---|---|---|---|---|---|---|
| 2026-10-26 | -6.0% | 29.0% | -7.6% | 43.1% | 19.45 | 9.53 |
| 2026-10-27 | -6.0% | 29.0% | -7.6% | 43.1% | 18.39 | 8.47 |
| 2026-10-27 | -6.0% | 29.0% | -7.6% | 43.1% | 17.52 | 7.40 |
