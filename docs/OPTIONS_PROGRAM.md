# Options paper program (second Alpaca paper account)

`consumer/opt_program/` trades defined-risk option structures on a SECOND Alpaca paper account. Owner decisions of 2026-11-21 are final. It touches nothing that exists: not the first paper account, S-042, S-048, S-052 or policy p3. Results are never pooled across accounts. Paper only.

## Commands (counts only on stdout)
```
python3 -m consumer.opt_program account-check            # YES or NO for multi-leg approval, and proof the account differs from the existing desk's
python3 -m consumer.opt_program run                      # dry run: plans, sends nothing, writes nothing
python3 -m consumer.opt_program run --submit             # live: needs approval, multi-leg level, a different account, and the workflow's OPTIONS_SUBMIT switch
python3 -m consumer.opt_program report | verify | checkpoint
```
Keys come from the owner's NEW secrets `OPT_LAB_ALPACA_KEY_ID` and `OPT_LAB_ALPACA_SECRET_KEY` and nothing else, sent only to the paper host (`consumer.alpaca_paper.make_client`). The existing account's keys (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`) are never a fallback and are read only through a GET-only client, to prove the two accounts differ. Nothing here can write or change a secret.

## Rules enforced in code (tested)
| Rule | Where |
|---|---|
| Defined risk only; every builder proves each short leg is covered; calendars and diagonals are refused until a paper test passes | `structures.py` |
| Max loss at entry 1,000 USD; 1,500 only when the smallest available version cannot fit under 1,000; never above | `select.pick` |
| Open risk cap 10,000; at most 3 open per strategy; 1 open per underlying per strategy; no same-day re-entry | `engine.py` |
| Strategy halt at -1,500 and account halt at -4,000 (realized plus modelled open result); a halt stays until a RESUME row | `engine.evaluate_halts`, `ledger.reduce` |
| No short legs into expiration week; no expiry on 2026-11-26 or 2026-11-27 | `select.choose_expiry`, `decide_exit` |
| Liquidity filter on every leg and on the structure | `select.liquidity_ok`, `policy.py` |
| Cost: 0.05 USD per contract per leg per side; fills 75 percent of the way from mid to natural (1.0 also reported) | `policy.py`, `structures.py` |
| A spec trades only when approved with the same sha256; it is frozen in the ledger before its first fill | `specs.py`, `engine.run` |
| Append-only hash-chained ledger; daily checkpoint (row count and digest) checked before every write | `ledger.py` |
| Catalyst tags at entry time from the repo calendar (FOMC, CPI, jobs, PCE, monthly expiration); stocks without an earnings source count as tagged | `catalysts.py` |
| Shadow random-entry control for every strategy: same structure and exits, no orders, outside every cap | `engine.py` |
| Report: expectancy per dollar at risk with a bootstrap interval, drawdown, worst trade, trade count; win rate shown never ranks; with and without catalyst tags; versus control; `unranked, not evidence` until 30 trades and a Stage 1 result; scaled to an 800 USD account at 50 to 80 USD risk | `report.py` |

## State
Specs, approvals and the Stage 1 file live in the private hub (`system/12-options-program/`). The ledger and checkpoints live on the hub's `options-ledger` branch. Open positions are recomputed from the ledger and reconciled with the broker each run; a mismatch (for example an account reset) stops new entries. Never reset the account without exporting a checkpoint first.

## Known unknowns (flagged, fail-safe)
- The sign convention of the net limit price for credit multi-leg orders on Alpaca paper is sent as negative for a credit; if the broker rejects it the order is simply not placed.
- Alpaca paper fills may be better than real fills; the ledger records the model price and the broker fill side by side and uses the worse of the two.
