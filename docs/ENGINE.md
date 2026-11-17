# Engine

The one program that backtests and runs strategies. Research and paper only; the engine cannot place an order (execution stays in `consumer/alpaca_paper.py` behind its guards). Audit that led to it: [AUDIT_2026-11-17.md](AUDIT_2026-11-17.md).

## Layers (package `engine/`)
| Layer | File | Job |
|---|---|---|
| Data types | `types.py` | `Intent` (what a strategy wants), `Position`, `Trade` |
| Strategy | `strategy.py`, `strategies/` | decide at a close from data known at that close; plugins implement `signals` and `exit_reason`. First plugin: `oversold_v3.py` (S-042 V3 exactly as the forward tool trades it) |
| Portfolio | `portfolio.py` | caps, ranking, sizing; deterministic |
| Execution model | `simbroker.py` | next-open fills, broker-side stops, separate entry and exit costs |
| Backtest | `backtest.py` | day-by-day loop identical to the live schedule: decide after the close, fill at the next open, stops live at the broker |
| Health | `health.py` | stop coverage, orphan orders, stale entries, news-source drift, data freshness |
| Runner | `runner.py` | `backtest` and `health` commands |

Principle: **the live decision code is the backtest decision code.** `tests/test_engine.py` proves parity: the engine's S-042 signals equal the forward tool's candidates, in the same order, on the same data.

## Commands
```
python3 -m engine.runner backtest PANEL_DIR OUT_DIR [--dump-trades]   # as-implemented backtest with four execution scenarios
python3 -m engine.runner health [--json]                               # read only; exit 1 on a FAIL
python3 -m consumer.system_preflight                                   # the wider dependency check
python3 -m unittest tests.test_engine tests.test_engine_health tests.test_stress_system
```

## First results: S-042 V3 as deployed (10 positions, 1,000 USD each, 2017 to Nov 2026, 1,822 trades)
| Execution scenario (per side) | Mean per trade | Total | Judge window | Final window |
|---|---|---|---|---|
| 5 bps (what the old backtests assumed) | +0.255% | +4,178 USD | +0.06% | +0.51% |
| **Market orders at the open, 32 bps** (measured half-spread at 09:30 to 09:35) | **-0.285%** | **-5,276 USD** | -0.48% | -0.03% |
| 10:00 ET execution, 9 bps, with measured price drift | +0.228% | +3,707 USD | +0.04% | +0.48% |
| Opening-auction orders, 5 bps (hypothesis, untested on the paper account) | +0.255% | +4,178 USD | +0.06% | +0.51% |

Evidence for the inputs: median quoted spread of the first 10 live names 64 bps at 09:30 to 09:35 ET and 19 bps at 11:00 ET; in 350 sampled past trades the price at 10:00 ET moved +0.5 bps against a buyer and +5.8 bps in favour of a seller relative to the 09:30 open; the first 10 paper fills averaged +40 bps against the open. Reading: the strategy's edge is about 25 bps a trade and the opening spread costs more than that, so **where and how orders execute decides whether S-042 works at all.**

## Plan
| Phase | What | Status | Needs |
|---|---|---|---|
| P0 | Engine core, as-implemented backtest, health, preflight, failure scenarios | done 2026-11-17 | nothing |
| P1 | Deterministic runtime outside the chat session: a GitHub Actions workflow in the private hub runs health, preflight, signals and (only when the owner switches it on) orders. Drops token cost and the dependence on one container | workflow written, dry-run only | owner adds two repository secrets and decides when to switch on orders |
| P2 | Execution layer: order-timing policies (opening-auction, 10:00 ET marketable limit), slippage measured from quote data, cost model fitted to it and used by the backtest | experiment queued: tiny opening-auction orders to see how the paper account fills them | owner approval before S-042 changes its order type |
| P3 | Strategy factory: more daily strategies with different drivers, each with a frozen as-implemented backtest, run as many small forward tests in parallel (evidence velocity) | next | none |
| P4 | Portfolio and risk across strategies: exposure, correlation, daily reconciliation against broker truth, alerts | after P3 | none |
| P5 | Data platform: durable store for the research panel, point-in-time universes, a news archive; a paid feed only if a measured need exists | after P3 | owner decision on spend |
| P6 | Real-time layer (always-on machine) | only if a validated strategy needs it | owner decision |

Rule carried forward: no strategy goes forward until its protocol, as written, has an as-implemented backtest in this engine.
