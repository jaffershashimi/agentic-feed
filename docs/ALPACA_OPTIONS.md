# Alpaca options (paper account, defined risk)

`consumer/alpaca_options.py` opens, manages and reports defined-risk option structures on the Alpaca PAPER account. Approved by the owner on Nov 14, 2026: defined risk only, at most 1,000 USD of premium per trade, and Claude runs it without asking per trade. It builds on `docs/ALPACA_PAPER.md` (paper host only, kill switch, log). Results are BROKER_PAPER, a separate class: never pooled with PAPER, REPLAY or SYNTHETIC, never counted toward go-live, never in the paper ledger.

## Structures and limits (enforced in code, tested)
| Structure | What it is | Maximum loss |
|---|---|---|
| long-call, long-put | Buy one option | Premium, at most 1,000 USD |
| straddle | Buy a call and a put, same strike and expiry | Premium, at most 1,000 USD |
| strangle | Buy a put below and a call above spot | Premium, at most 1,000 USD |
| debit-vertical | Buy one strike, sell another, same type and expiry | Net debit, at most 1,000 USD |
| covered-call | Sell a call against 100 shares held per contract | Not capped at 1,000: the shares can fall. Capital at most 25% of equity |
| cash-secured-put | Sell a put with strike x 100 set aside | Not capped at 1,000: stock can fall to zero below the strike. Capital at most 25% of equity |

Also enforced: expiry 7 to 60 days out (no 0DTE), a two-sided quote with spread at most 20% of the mid on every leg, limit orders only (day), at most 6 open structures, premium at risk at most 5% of equity in total, capital in use at most 50% of equity, no naked short options, no credit spreads, the HALT file stops everything, and a dry run unless `--submit`. Orders are only sent while the market is open, because after-hours option quotes are not real.

## Commands
```
python3.12 -m consumer.alpaca_options chain SPY --min-dte 14 --max-dte 45
python3.12 -m consumer.alpaca_options open straddle SPY --expiry 2026-12-18 --strikes 740            # dry run
python3.12 -m consumer.alpaca_options open straddle SPY --expiry 2026-12-18 --strikes 740 --submit
python3.12 -m consumer.alpaca_options manage [--submit]
python3.12 -m consumer.alpaca_options book
```
Each structure carries a tag in the order's `client_order_id`, so the broker itself remembers which legs belong together; no local state is needed between sessions.

## Management rules (fixed in advance, no discretion)
Long structures: close at a 50% loss of premium, at +100%, or 5 days before expiry. Short options: buy to close at 2.5 times the credit, at 20% of the credit left, or 3 days before expiry. Legs close as single limit orders near the mid. Long options cannot lose more than the premium, so nothing needs a wake-up to stay safe.

## Autonomy and cost
No schedule exists for this. Claude trades when a session is active during market hours (any message from the owner is enough) and decides without asking. Nothing runs between sessions. Every trade is journaled outside GitHub, in Drive, with its thesis, structure, maximum loss and exit plan, because broker orders and positions are account data.

## Evidence plan
A backtest on this data is not possible (see `system/06-research/option-backtest-2026-11-14.md` in the private hub): Alpaca's historical option bars are trade prints only, sparse at 5-minute resolution, with no historical quotes. The test is forward: a journal entry per trade, then a read after at least 30 trades per structure, reported with intervals and never pooled.
