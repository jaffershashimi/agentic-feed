# Research log (agentic-feed simulations and stress tests)

Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, and no result changes a trading rule by itself.

Records stored: 20; unique research runs: 10; paper-lab records: 19. Latest record session: 2026-11-05.

## Latest research run (holdout split)

Run key c2417f141054, code 4531914, data through 2026-11-05. 27020 unique observations, 990109 resampled scenarios.

| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |
|---|---|---|---|---|---|---|---|
| call_above_sma200 | 1166 | 28.8% | -6.1% | -8.9% to -3.9% | 1.58 | -34.6% | 66.6% |
| call_every_session | 1458 | 29.6% | -5.7% | -8.2% to -2.8% | 1.60 | -36.2% | 80.1% |
| cash | 0 | n/a | 0.0% | n/a | n/a | n/a | n/a |
| put_every_session | 1458 | 27.0% | -7.6% | -9.7% to -4.9% | 1.76 | -45.8% | 98.7% |
| put_vix_above_25 | 193 | 25.4% | -13.4% | -18.8% to -6.9% | 1.43 | -51.6% | 76.1% |

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

VIX 28.18, 20-day realized 20.22, VIX/RV20 1.39, VRP 7.96 vol points, VIX percentile (252d) 98.8, term 3m/1m 0.907, v0 regime stress (unvalidated).

## Paper lab ($10,000 paper account, cost scenarios; modeled options)

1000000 synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: 0 at 10% size, 0 at 15% size (premium about $2385.88).

| rule / scenario | holdout n | win rate | mean net | 95% interval |
|---|---|---|---|---|
| call_above_sma200/liquid | 1166 | 32.6% | -2.6% | -5.6% to 1.4% |
| call_above_sma200/stressed | 1166 | 20.6% | -10.9% | -13.8% to -8.5% |
| call_above_sma200/typical | 1166 | 31.8% | -3.5% | -7.0% to 0.2% |
| call_above_sma200/wide | 1166 | 27.9% | -6.4% | -9.3% to -3.3% |
| call_every_session/liquid | 1458 | 33.5% | -2.0% | -4.4% to 1.6% |
| call_every_session/stressed | 1458 | 21.6% | -10.8% | -13.5% to -8.7% |
| call_every_session/typical | 1458 | 32.2% | -3.0% | -5.9% to 0.1% |
| call_every_session/wide | 1458 | 28.1% | -6.0% | -8.6% to -3.4% |

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
| 2026-11-05 | -6.1% | 28.8% | -7.6% | 43.1% | 28.18 | 7.96 |
