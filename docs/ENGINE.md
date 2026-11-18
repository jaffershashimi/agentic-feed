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

## Execution slot and live news (2026-11-17)

**Owner decision (Jaffer, 2026-11-17):** S-042 paper execution may use any session, including extended hours. Engineering
chooses the slot by evidence, and paper trading only. Live trading stays paused.

What the evidence supports today:

| Slot | Evidence | Status |
|---|---|---|
| Market order at the open | measured about 32 bps opening spread, engine result -0.29% per trade | worst option, current fallback |
| Marketable limit at about 10:00 ET | engine scenario +0.23% per trade, 350-trade timing study | implemented: `oversold_forward run --mode morning` |
| Opening-auction order | hypothesis (+0.26% if filled at the auction print) | paper experiment `scripts/opg_experiment.py` runs 2026-11-17 night |
| After hours / pre-market | no backtest, no spread data; limit orders only, stops do not trigger outside the regular session | not used until measured |

Morning mode places a marketable limit 30 bps through the IEX quote for both entries and exits (entries keep the
10 percent broker-side stop as an OTO leg), refuses a quote wider than 1 percent, only sends inside 09:50 to 11:30 ET,
and is idempotent: a second run the same day sees the pending and filled tagged orders and sends nothing.

`consumer/news_digest.py` prints live headlines (Yahoo Finance per symbol, MarketWatch/Dow Jones, Fed, Google News)
for the names we hold or might buy. It is information only: no news rule gates S-042 until one is pre-registered and tested.


## Desk process layer (2026-11-17)

Added after a review of how institutional desks run their day (pre-trade controls, post-trade cost analysis, daily reconciliation, limit tiers, model monitoring).
The nine stages and who owns each are in the hub's `docs/PROCESS.md`. Code here:

| Stage | Module | Command | What it does | Can it send an order? |
|---|---|---|---|---|
| Limits | `engine/limits.py` | `python3 -m engine.runner limits` | one register of every limit, read from the code that enforces it; owner-set vs proposed | no |
| 3 Pre-trade gate | `engine/gate.py` | `python3 -m engine.runner gate PLAN.json` | reviews the dry-run plan: kill switch, window, free slots, size cap, stop attached, tag, duplicates, corporate-action data, health. Fails closed | no |
| 4 Execution | `consumer/oversold_forward.py` | `run --mode morning --submit [--exits-only]` | unchanged rules; `--exits-only` lets a gate block stop new entries while exits still go out | yes (paper, existing guards) |
| 5 Post-trade cost | `engine/tca.py` | via `pack` | shortfall against the signal close and against arrival, fill rate, split by execution mode | no |
| 6 Reconciliation | `engine/recon.py` | via `pack` | broker positions against tagged orders: quantity breaks, unexplained, missing, overdue, cap breach | no |
| 7 Risk limits | `engine/riskreport.py` | via `pack` | day result, drawdown from the peak, gross and single-name exposure against proposed tiers (report only) | no |
| 8 Model monitoring | `engine/monitor.py` | via `pack` | cost drift, return drift, fill rate, plumbing, evidence progress | no |
| All | `engine/deskpack.py` | `python3 -m engine.runner pack` | one pack, counts and percentages only | no |

Design rules: the gate and the pack never change a plan or a strategy rule; a RED tier is a request for the owner's decision, not an action; logs hold counts, percentages
and check names, never symbols or dollar amounts; every stage reads the broker's own records, so nothing depends on a chat session being alive.

First live reading (paper account, read only, 2026-11-17): the 10 measured legacy entries (market orders at the open) cost a mean 36.4 bps against the first
one-minute bar at the open; the engine had modeled that mode at 32 bps. The 9 bps morning-limit model has no fills yet and is judged separately once the first
10 morning entries exist. Reconciliation found no breaks across the 9 open positions.

## Finding: entry stops expired at the close (2026-11-17), and the fix

The first after-close health run after the desk process went live reported no stop on any of the 9 open positions. Cause: the stop attached to each entry (an OTO leg) inherits the
entry's time in force, and entries were `day` orders, so every stop expired at 4 PM and nothing re-placed it. Positions were unprotected overnight; earlier "stops present" results
were true only because those checks ran before the close. The backtest assumed a stop that is always live, so any gap beyond 10 percent overnight was never capped in live trading.

Fix, same day: (1) morning entries are `gtc`, so the attached stop is `gtc` too (tested on the paper account: a GTC parent gives a GTC stop leg); (2) a stop guard
(`ensure_stops`, also `oversold_forward protect [--submit]`) re-places any missing stop for the full quantity as a GTC stop at the stop price the entry recorded, runs on every
submit and is not stopped by a gate block, because it only reduces risk; its orders carry the tag `s042v3s-` and a fill is recorded as a STOP exit; (3) the 9 open positions were
re-protected by hand with the same code; (4) `engine health --safe` withholds stock names so a failing check never writes holdings into a GitHub log.

## Owner decision: S-042 paper size and cap (2026-11-17)

Owner (Jaffer), paper account only: use much more of the roughly 100,000 USD paper balance so more trades close sooner. S-042 entries go from 1,000 to **3,000 USD** and the position cap from
10 to **20** (about 60,000 USD deployed at most; worst case if every 10 percent stop hit is about 6 percent of the account). Reason: on a stress day the strategy often has 20 or more signals and 1 free slot, so
the bottleneck for the 100-trade gate is the number of trades, not the dollars. Strategy rules, the stop (10 percent below the signal close, now always live), the 5-session time stop and the morning
limit execution are unchanged. Returns per trade stay comparable across the change; positions opened before it are 1,000 USD each. The engine's own backtest keeps 10 x 1,000 USD because it reports
per-trade statistics; a 20-slot backtest would select more trades but is not needed for the monitor, which compares per-trade percentages. The proposed report-only gross-exposure tiers moved
from 30 and 50 percent to 70 and 90 percent so the new normal does not read as an alarm. Nothing is enforced by those tiers.

## Opening-auction paper experiment (2026-11-18)

Question: can S-042 enter at the opening auction (about 32 bps cheaper than a market order at the open, per the as-implemented backtest)? One-share paper orders tagged `xlab-`, placed the evening before (`scripts/opg_experiment.py place`), read at 9:42 ET (`read`). Counts and prices only, no balances.

Result:
- **OPG (opening-auction) orders are accepted** by the paper API: market OPG and a marketable limit OPG were both accepted.
- **None filled.** All three expired unfilled at about 9:31 ET. The paper engine does not simulate the opening auction, so there is no fill price to compare with the official open. Paper cannot measure auction fill quality; only real money could, and nothing here changes that.
- **OPG plus an attached stop is rejected as one order.** An OTO order with time in force `opg` fails with HTTP 422: `oto orders support only "day" or "gtc" time_in_force`. A stop would have to be placed separately after the auction fill, which leaves a short unprotected window.
- A GTC OTO test on the same day was accepted and then canceled by us, so the GTC parent plus GTC stop pattern the engine already uses is unaffected.

Decision for S-042: **no change.** Opening-auction execution is not testable on paper and cannot carry the stop in one order, so the engine keeps its marketable limit orders at about 10:05 ET (the +0.23 percent per trade case). Real-fill quality versus the backtest is measured by the TCA stage on the forward trades. Revisit auction entry only after the real-money readiness review (Jan 11, 2027) and with the owner's approval.
