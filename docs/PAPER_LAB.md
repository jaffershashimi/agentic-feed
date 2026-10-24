# Paper lab

`feed_pipeline/paperlab.py` writes `feed/paper-lab.json` (schema `paper-lab/1`) after every publish run. It is a bounded scenario experiment for a $10,000 paper account. It never places orders.

## Data classes (kept separate)

| Section | dataClass | What it is |
|---|---|---|
| `historical` | `HISTORICAL_UNDERLYING_MODELED_OPTIONS` | One outcome per entry date on real S&P 500 and VIX closes, using the research runner's +30% / −15% structure, re-priced under each cost scenario. Option prices are Black-Scholes values from the VIX, not quotes. |
| `synthetic` | `SYNTHETIC_BLOCK_BOOTSTRAP_OF_HOLDOUT` | Equity paths of 100 trades resampled in blocks from the holdout outcomes. The total number of trade draws is capped at one million (`MAX_SCENARIOS`). These draws are not new observations. |
| `paperFills` | `PAPER` | Never in this repository. The private desk scores its own fills with `consumer/paper_ledger.py`. |

## Cost scenarios

| Scenario | Spread (% of mid) | Extra slippage | Missed checks |
|---|---|---|---|
| liquid | 0.7% (the observed SPY 27-day ATM call quote) | 0.25% | 2% |
| typical | 2% | 0.5% | 5% |
| wide | 4% | 1% | 5% |
| stressed | 8% | 2% | 10% |

Every scenario also carries:
- **Fees:** $0.68 per contract per side.
- **Quote delay:** the TradingView connector serves OPRA option quotes 15 minutes delayed (`update_mode` `delayed_streaming_900`). The entry price therefore carries an error of delta × the index's expected 15-minute move, drawn at random.
- **Missed checks:** a check that finds no usable quote skips that day's exit test. The final day is always tested.

## What it reports

- **Per split** (train, validation, holdout): n, win rate, mean net with a moving-block 95% interval, and exits by type. Nothing is fitted to any split.
- **Per rule, scenario and proposed size** (10% or 15% of equity): terminal-equity percentiles, the probability of a loss, the probability of a drawdown over 15%, and the median maximum drawdown.
- **`contractsAffordable`:** how many whole ATM SPY contracts each proposed size buys at $10,000. At about $1,250 a contract, a 10% size buys none.
- **`missingData`:** gaps in the aligned index history.

The sizes are proposals awaiting the owner's paper-policy decision. The history collection on the `data` branch records a summary of each run.
