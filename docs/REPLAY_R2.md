# Replay engine (rules R2)

`consumer/replay_r2.py` replays frozen replay rules R2 (R2_HASH c879c06e24a5, on paper policy p2) over saved bars. It is standard-library Python, offline and deterministic: the same bars and settings always give the same rows. It never talks to a broker and places no orders.

```
python3 -I -m consumer.replay_r2 --bars bars5m.csv --daily daily.csv --days 2026-10-26 --out out/
    [--option-bars options5m.csv] [--chain chain.csv] [--events events.json] [--done replay_sessions.csv]
```

## Inputs

- `bars5m.csv` and `daily.csv`: `symbol, begins_at, open, high, low, close, volume[, interpolated]`, the same fields as market-data 5-minute and daily bars. Interpolated bars and bars outside 6:30 to 13:00 PT are dropped. Load at least 15 sessions of 5m bars and 221 daily closes before the first replay day.
- `events.json` (optional): `{"earnings": [{"symbol": "X", "date": "YYYY-MM-DD", "timing": "am" | "pm"}], "macro": ["YYYY-MM-DD"]}`. Without it the R2 macro list (Oct 28 and 29, 2026) is used and no earnings are known.
- `options5m.csv` (optional, turns on the option arms): `underlying, expiration, strike, begins_at, open, high, low, close, volume[, interpolated]`, calls only, from Robinhood 5-minute option bars. Interpolated bars are dropped, as R2 section 1 says.
- `chain.csv` (optional): `underlying, expiration, strike` for every listed call. Without it, the strikes and expirations that have bars count as listed, which can change which 10 strikes are candidates.
- `--done`: a `replay_sessions.csv` with a `key` column. A key `R2|YYYY-MM-DD|SYM` is never replayed twice.

## Outputs

- `replay_fills.csv`: ledger rows in the `paper_ledger.py` format plus `score` and `source`. Check it with `paper_ledger.py --max-open 3 --max-entries 5 --no-first-loss-stop` and `score_check.py`.
- `score_parts.csv`: one row per criterion per entered trade (context, trigger, liquidity, room, risk), with the raw values.
- `decisions.json`: every decision point that had a trigger, with the failed filters, the score, the action, each arm's outcome (`arm_a`: OPTION, NO_FIT, MISSING_DATA or CAP_D4; `arm_b`: OPTION, STOCK or CAP_D4), the option pick with every candidate strike (IV, delta, cost, eligible), and the REALISTIC entry, exit and net for option trades (review only, never in the ledger).
- `replay_sessions_new.csv`: the keys replayed in this run, to append to `replay_sessions.csv`.

## What is built

- Decision points, trigger events, filters F1 to F7, the D3 score, sizing, stock fills and every R2 exit for the stock.
- Options (R2 sections 6 to 8), with `--option-bars`:
  - Arm A picks the call: first listed expiry on or after entry + 21 days; the 10 listed strikes whose rough delta (IV = 1.3 x max(RV20, 15%)) is 0.22 to 0.68, nearest 0.45; IV solved from the close of the option bar ending at the decision (Black-Scholes, r 4%, T to 16:00 ET on expiry); eligible at delta 0.30 to 0.60 and cost (entry bar high x 100 + $0.68) within the size; nearest 0.45 wins; quantity = floor(size / cost). No real bars at both the prior and the entry bar for any candidate: MISSING_DATA. Bars but none eligible: NO_FIT.
  - Arm B takes the same call when its IV / RV20 is 1.5 or lower, otherwise the stock.
  - HARSH ledger fills: buy the entry bar high, sell the exit bar low, $0.68 per contract per side; stop = fill x 0.85 rounded up. On real option bars after entry: the underlying rule exit moves the option to the first option bar at or after the stock exit bar (`EXIT_MIRROR_<reason>`), then the 15% stop at that bar's low, then a close at +30% exits at the next bar's low.
  - Each arm has its own p2 caps (3 open, 5 entries a session, $3,750 open size).
- Without `--option-bars`, each entry records arm A as `MISSING_DATA` and arm B takes the stock, as R2 section 6 prescribes when no option data exists.

## R3 (score test, replay only)

`--rules R3 --r2-hash <R3_HASH>` replays R3: identical to R2 except the D3 score, which becomes context, room and risk (as R2), `trend_1h_4h` (2 if the last completed 1h close is above the 1h EMA21 and the last completed 4h close is above the 4h EMA20, 1 if one of them) and `rel_volume` (2 at 1.5 or more, 1 at 1.0 or more). The trigger stays required and ADV20 stays filter F1. Arms `REPLAY-A-R3-<SYM>`, trade ids `R3A-`/`R3B-`, decision ids `R3-...`, session keys `R3|YYYY-MM-DD|SYM`, source `REPLAY_R3;R3_<hash>`. Running R2 and R3 through the same engine keeps the paired comparison (`compare_arms.py`) about the score alone.

`--done` accepts a file with a `key` or `session_key` column, so the project's `replay_sessions.csv` can be passed as is.

## Validation against the real R2 ledger (2026-10-27)

Run on Robinhood bars for two symbols the 1:23 run replayed by hand:

| Trade | Engine vs ledger |
|---|---|
| XLF Oct 20 07:00, 07:45, 10:45 (Nov 13 $56.5, $56.5, $56 calls) | Same contract, quantity, fill, delta, IV, stop, exit time, exit price and reason for every row. IV/RV 1.20 vs 1.21 on one trade (rounding of RV20). The 10:45 exit is on Oct 21, outside the loaded bars, so the engine leaves it UNRESOLVED |
| NVDA Oct 23 07:00, 07:45 (Nov 13 $220 call) | Same on every row |
| NVDA Oct 23 06:45 | The ledger has an entry; the engine has no trigger. The 06:45 close (212.575) is below the high of the previous four 15m bars (212.72), and a VWAP reclaim needs a same-session previous bar, so R2's text gives no trigger. The hand replay was wrong here |
| NVDA Oct 23 09:00 | The ledger skipped it for the caps (three open after the extra 06:45 entry); the engine, with two open, enters |

The engine matches the hand replay wherever the hand replay followed R2, and differs where it did not.

## Readings of the R2 text

R2 is frozen; where its words leave room, the engine reads them as follows. A different reading needs Jaffer's approval and a new rules version.

- "The 4 previous completed 15m bars" may reach into the prior session (the EMAs run across sessions too). A VWAP reclaim needs the previous 15m bar in the same session, because VWAP restarts each session.
- Fading momentum counts 15m bars that start at or after the entry, and "green" means a close above the entry bar open (the underlying price at entry).
- Positions opened by an earlier run are not known to the D4 caps of a later run. Replay consecutive days in one run.
- Option candidates are checked in order of rough-delta closeness; equal deltas break by the lower strike.
- A position still open at the last loaded bar is UNRESOLVED: no exit row.

All tests use invented symbols and prices.
