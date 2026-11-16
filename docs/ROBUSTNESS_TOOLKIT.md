# Robustness toolkit

Small, dependency-free tools whose only job is to make evidence harder to fool. All research or paper only; none places an order or changes a rule.
Standard library only, so CI needs nothing installed. Open-source background and the decisions on other tools: `docs/OPEN_SOURCE_LANDSCAPE.md`.

| Tool | Question it answers | Command or entry point |
|---|---|---|
| `consumer/robust_stats.py` | Is this Sharpe real once luck, many trials and short history are counted? PSR, Deflated Sharpe (DSR), minimum track record, Probability of Backtest Overfitting (CSCV), stationary bootstrap interval | imported by the tools below |
| `consumer/oversold_robust.py` | Does the S-042 edge survive a 36-config parameter sweep, and what is its DSR and PBO? | `python3 -m consumer.oversold_robust PANEL_DIR OUT_DIR` |
| `consumer/regime_hmm.py` | Calm or stressed? Causal two-state Gaussian HMM, filtered probabilities only, expanding-window refit | `rolling_stress_probability(returns)` |
| `consumer/tearsheet.py` | One-page SVG: headline numbers, equity vs benchmark with test windows, drawdown, calendar-year bars | `render(title, subtitle, dates, returns, benchmark, windows, notes)` |
| `consumer/data_gate.py` | Is a bar series trustworthy, and is a split or merger coming? Quarantines bad series, never repairs them | used by `oversold_forward` |
| `consumer/cost_model.py` | What should a small order cost (half spread plus square-root impact), and what did the fill cost? | used by `oversold_forward` |
| `consumer/overnight_monitor.py` | How wide are overnight spreads and how often is there no quote? (Dec 6 evening session prep) | `python3 -m consumer.overnight_monitor poll ...` then `summary ...` |
| `tests/test_indicator_crosscheck.py` | Do our RSI and moving averages equal the standard formulas? Skipped where pandas is absent | `python3 -m unittest tests.test_indicator_crosscheck` |

## How to read DSR and PBO
* **PSR** is the chance the true Sharpe is above a benchmark (zero here), given the track-record length and the skew and kurtosis of returns.
* **DSR** is PSR against the Sharpe you would expect from the best of N tries on pure noise. N is the number of variants or ideas tried. Above 0.95 is the usual bar; near 0.5 or below means luck is a live explanation.
* **PBO** is the share of train/test splits where the in-sample winner ranks below the median out of sample. A single value is noisy: pure noise reads anywhere from about 0.1 to 0.8 and averages 0.5. Use it as a warning light, not a verdict.
* None of this fixes a look-ahead bug or a data error. It only prices the cost of searching.

## First results (S-042 V3, 2017 to Nov 2026, 5 bps costs)
Sharpe 0.59, PSR above zero 0.98, DSR 0.81 at 4 variants and 0.36 at 42 registered ideas. Judge plus Final windows only: Sharpe 1.04, DSR 0.54 at 42. All 36 parameter configs are positive in the Judge and Final windows and all 36 are negative in the Development window (the March 2020 crash). PBO is 0.37 across the 4 declared variants and 0.29 across the 36-config grid. Full-period V3 trails SPY (Sharpe 0.59 against 0.87) with a 55% drawdown.
Reading: the effect is not a single lucky parameter, but it is regime dependent and it does not clear the multiple-testing bar. S-042 stays a paper observation.

The causal HMM, trained walk-forward on SPY, flags stress on 40% of scored days against 25% for the realised-vol flag, and it was already on in early February 2020 before the Feb 19 peak. It does not give a clean early warning of the 2020 crash, so it stays a diagnostic.
