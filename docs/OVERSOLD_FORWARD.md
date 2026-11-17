# S-042 forward observation (stress variant) on the Alpaca paper account

`consumer/oversold_forward.py` runs the frozen S-042 stress variant (V3) forward, once a day after the close, on the Alpaca PAPER account. It is TEST ONLY and a separate class (BROKER_PAPER): never pooled with PAPER, REPLAY or SYNTHETIC, never counted toward go-live. Protocol and kill rules: Agentic-Lab `system/06-research/specs/S-042-forward-protocol.md`. Backtest it comes from: `S-042-results-2026-11-15.md` (passed its pre-registered bar in 2022 to 2026, lost in 2017 to 2021, depends on panic rebounds).

## What it does
| Step | Rule |
|---|---|
| Data | Daily split and dividend adjusted SIP bars for a fixed universe (top 1,000 US stocks by prior-year dollar volume) and SPY. Bars after the last completed session are dropped. |
| Signal at the close of day t | Eligible (in the universe, 20-day average dollar volume at least 10 million, price at least 5 dollars), the 25-day deviation z-score at most -2, and SPY 20-day realized volatility above the 75th percentile of its previous 252 values. Same functions as the backtest (`consumer/oversold_panel.py`). |
| Entry | Market buy, day order, queued for the next open, whole shares up to 1,000 USD, one-triggers-other stop at 90% of the signal close. Ranking when more signals than slots: lowest RSI2, then most negative z, then symbol. At most 10 open positions of this strategy. |
| Exit | Decided at a close: close above SMA5, or the 5th session since entry. The stop is cancelled and a market sell is queued for the next open. |
| State | On the broker: entries carry `client_order_id` `s042v3-SYMBOL-YYMMDD`, exits `s042v3x-...`. Nothing is stored locally. Re-running the same day does nothing new. |

## Guardrails
Paper host only; HALT kill switch; dry run unless `--submit`; orders are sent only while the market is CLOSED, so they queue for the next open (as the executable-exit analysis assumed); stocks only, no shorting; skipped symbols are reported with a reason.

## Safety nets (added Nov 16 2026)
They only remove candidates or add information; the frozen signal is unchanged.
* **Data gate** (`consumer/data_gate.py`): a candidate whose bars look wrong (unadjusted split ratio, gaps, bad prints, zero volume) is quarantined and listed in the output.
* **Corporate actions**: a candidate with a split, merger or spin-off ex date in the next 7 days is skipped as `CORPORATE_ACTION_SOON`. If the endpoint errors the run continues and says so.
* **Cost model** (`consumer/cost_model.py`): each candidate carries `est_cost_bps` (half spread plus square-root impact). `report` adds `entry_gap_vs_signal_close_bps`, the fill against the signal close recovered from the stop leg.

## Commands
```
python3.12 -m consumer.oversold_forward signals --universe UNIVERSE.txt      # read only
python3.12 -m consumer.oversold_forward run --universe UNIVERSE.txt           # dry run
python3.12 -m consumer.oversold_forward run --universe UNIVERSE.txt --submit  # queue orders for the next open
python3.12 -m consumer.oversold_forward report                                # closed trades and totals from the broker's orders
```

## Verified on the paper account (Nov 16 2026)
One SPY share through the same order type: market buy filled 0.003% from the reference price with the stop resting; cancelling the stop and selling flattened it. Open orders list the stop as its own sell order, which is what the exit step cancels.

## First fills (2026-11-17)
The first 10 queued entries filled 2 to 3 minutes after the open (not at the opening print), with a mean gap of +44 bps against the signal close (range -120 to +224, n 10). All stop orders were live after the fills. Treat the backtest's open-fill assumption as optimistic until 20 or so fills say otherwise.

## Limits
No pre-open fill check: queued market orders fill at the open whatever it is, and a gap below the stop fills at the open. Paper fills ignore queue position and market impact. The universe is a fixed list for 2026 and must be rebuilt each January.
