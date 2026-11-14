# Alpaca paper order tool

`consumer/alpaca_paper.py` places orders on the Alpaca PAPER account only. Approved by the owner on Nov 13, 2026 as plan A: a plumbing test first, then mirroring R2 stock entries as a separate class.

## Why
The desk ledger records fills the desk assumed (buy at the ask at the quote time). A broker paper account gives simulated fills, so slippage, rejected orders and missed fills become measurable. Results are a separate class, BROKER_PAPER. They never count toward the go-live gates, never enter the paper ledger and never pool with PAPER or REPLAY.

## Guardrails (enforced in code, tested)
| Guardrail | Detail |
|---|---|
| Paper host only | Base URL must be exactly `https://paper-api.alpaca.markets`; `ALPACA_PAPER_TRADE=false` is refused. Lookalike hosts, http, ports and userinfo tricks are refused. |
| Dry run by default | `buy` and `close` send nothing unless `--submit` is passed. |
| Stocks only | Symbol must be a plain ticker and an active, tradable `us_equity` asset. Always buy to open; no shorting, no options. |
| Size | At most 1,000 USD per order, whole shares rounded down. |
| Limits | At most 3 open positions, counting unfilled entry orders. One position or pending entry per symbol. |
| Stop on every entry | OTO order: market buy for the day with a stop-loss child 20 percent below the reference price (the R2 stop). |
| Market hours | Refused while the market is closed, so nothing queues for the open by accident. |
| Kill switch | A `HALT` file in the state directory stops every order command. `halt` also cancels open orders; `halt --flatten` closes positions. `resume` clears it. |
| Log | Every change is appended to `orders.jsonl` in the state directory (no keys). |

## Commands
```
python3.12 -m consumer.alpaca_paper preflight
python3.12 -m consumer.alpaca_paper buy SPY --ref-price 739.15            # dry run
python3.12 -m consumer.alpaca_paper buy SPY --ref-price 739.15 --submit   # paper order
python3.12 -m consumer.alpaca_paper positions | orders | fills
python3.12 -m consumer.alpaca_paper close SPY --submit
python3.12 -m consumer.alpaca_paper halt [--flatten] [--reason TEXT]
python3.12 -m consumer.alpaca_paper resume
```
Credentials come from the environment (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER_BASE_URL`). Keys are never printed or logged. `status` and `preflight` print account state flags only, never balances.

## Plan
1. Monday Nov 16, after the open: one SPY plumbing test (buy with stop, check the fill against the archived SIP bar, close).
2. No new schedules (owner decision, Nov 13, 2026: token cost). The tool runs only when the owner asks for a trade in chat, during market hours. Any automation later would be a plain rule-following script on free CI minutes, not an AI schedule, and needs a written strategy and the owner's approval first.
3. For each trade the owner asks for, report fill price minus the reference price (slippage). Results stay a separate BROKER_PAPER class.

## Not covered
Real-money trading stays paused and is the owner's alone. This tool refuses any host other than the paper one.
