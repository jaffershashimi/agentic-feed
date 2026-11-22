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
Specs, approvals and the Stage 1 file live in the private hub (`system/13-options-program/`). The ledger and checkpoints live on the hub's `options-ledger` branch. Open positions are recomputed from the ledger and reconciled with the broker each run; a mismatch (for example an account reset) stops new entries. Never reset the account without exporting a checkpoint first.

## Known unknowns (flagged, fail-safe)
- The sign convention of the net limit price for credit multi-leg orders on Alpaca paper is sent as negative for a credit; if the broker rejects it the order is simply not placed.
- Alpaca paper fills may be better than real fills; the ledger records the model price and the broker fill side by side and uses the worse of the two.

## Accounts and the 2026-11-22 additions

Two options paper accounts, never pooled. `OPT-PAPER-1` (the second Alpaca paper account, 25,000 USD) runs the ten approved starters and stays on its own pin exactly as built. `RESEARCH-LAB` (a third Alpaca paper account, 100,000 USD, secrets `OPT_LAB2_ALPACA_KEY_ID` and `OPT_LAB2_ALPACA_SECRET_KEY`) gets batch 2, the unmanaged twins and the tiered universe, with its own ledger, specs, approvals, controls and kill criteria. Select it with `--account RESEARCH-LAB`.

| Limit | OPT-PAPER-1 | RESEARCH-LAB (same percentage of the account) |
|---|---|---|
| Max loss per trade | 1,000 (1,500 fallback) | 1,000 (1,500 fallback) |
| Open risk cap | 10,000 (40%) | 40,000 (40%) |
| Strategy halt | -1,500 (6%) | -6,000 (6%) |
| Account halt | -4,000 (16%) | -16,000 (16%) |
| Correlated group cap (SPY, QQQ, IWM, XSP, SPX) | none (frozen as built) | 15,000 (15%) |
| Monthly halt / review line | none | -6,000 (6%) / -10,000 (10%, report only) |
| Directional sub-cap, premium per directional arm | none | 1,500, 300 |

Before any order on RESEARCH-LAB the account check must say: ACTIVE, multi-leg approval, about 100,000 in size and buying power, and a different account from both the stock desk and OPT-PAPER-1 (yes or no only, never a number).

Spec schema is strict: unknown fields, and drafts that name an engine feature not built, are refused rather than partly used. New: `max_premium_usd`, `directional_group`, `breakout_days_min` (10, 20 or 55), unmanaged exits (`managed: false`, no target and no stop), and long-premium debit spreads may close 1 day before expiry (credit structures still close at 8 or more days).

Ranking (owner rule 2026-11-22, final): paper checks implementation and costs only; ranking comes from real-quote history with Holm correction and deflated Sharpe (`stage1.json` needs `holm_pass` and `dsr_pass`); no trade-count rule; paper never changes a rank. The report shows `trades_needed_t2` per strategy.

Order ids are fixed per spec, underlying and day (`O-121-IWM-261124-r`, exits prefixed `x`). They are the broker's client order id, which the broker refuses to accept twice, so a retried or re-run workflow cannot double-submit.
