"""Agentic Lab engine: one code path for strategies whether they are backtested or run on the paper account.

Layers (each file is one layer):
  types       plain data: Intent (what a strategy wants), Position, Trade
  strategy    Strategy base class: decide at the close, from data known at the close
  portfolio   Portfolio rules: caps, ranking, sizing; turns intents into orders
  simbroker   the execution model used in backtests (next-open fills, broker-side stops)
  backtest    day-by-day loop that runs Strategy + Portfolio + SimBroker exactly as the live tools run them
  health      checks for the failures that have actually happened (stale feeds, stops missing, orphan orders)
  runner      command line: backtest, parity, health, gate, pack, limits

Desk process layer (read only except where noted; see docs/ENGINE.md "Desk process layer"):
  limits      the limits register: every number that limits the desk, with its source and who set it
  gate        pre-trade gate: checks the day's dry-run order plan against the limits before anything is sent (PASS, BLOCK_ENTRIES, BLOCK_ALL)
  tca         post-trade cost analysis: measured execution cost against what the backtest assumed, split by execution mode
  recon       daily reconciliation: broker positions against our own tagged order records
  riskreport  exposure, concentration, day result and drawdown from the peak, against proposed report-only tiers
  monitor     model monitoring: forward results against the backtest benchmark, with pre-agreed triggers
  deskpack    one report that walks all nine stages of the daily process

Why it exists: on 2026-11-17 a strategy (S-044F) was put forward whose protocol, run as written, loses money in its own backtest, because the backtest and the live
tool were different programs. Here the live decision code IS the backtest decision code, so "as implemented" is the only kind of backtest there is.
Research and paper only. Nothing here can place an order; execution stays in consumer/alpaca_paper.py behind its guards.
"""
