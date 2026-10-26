# Replay engine (rules R2)

`consumer/replay_r2.py` replays frozen replay rules R2 (R2_HASH c879c06e24a5, on paper policy p2) over saved bars. It is standard-library Python, offline and deterministic: the same bars and settings always give the same rows. It never talks to a broker and places no orders.

```
python3 -I -m consumer.replay_r2 --bars bars5m.csv --daily daily.csv --days 2026-10-26 --out out/
    [--events events.json] [--done replay_sessions.csv]
```

## Inputs

- `bars5m.csv` and `daily.csv`: `symbol, begins_at, open, high, low, close, volume[, interpolated]`, the same fields as market-data 5-minute and daily bars. Interpolated bars and bars outside 6:30 to 13:00 PT are dropped. Load at least 15 sessions of 5m bars and 221 daily closes before the first replay day.
- `events.json` (optional): `{"earnings": [{"symbol": "X", "date": "YYYY-MM-DD", "timing": "am" | "pm"}], "macro": ["YYYY-MM-DD"]}`. Without it the R2 macro list (Oct 28 and 29, 2026) is used and no earnings are known.
- `--done`: a `replay_sessions.csv` with a `key` column. A key `R2|YYYY-MM-DD|SYM` is never replayed twice.

## Outputs

- `replay_fills.csv`: ledger rows in the `paper_ledger.py` format plus `score` and `source`. Check it with `paper_ledger.py --max-open 3 --max-entries 5 --no-first-loss-stop` and `score_check.py`.
- `score_parts.csv`: one row per criterion per entered trade (context, trigger, liquidity, room, risk), with the raw values.
- `decisions.json`: every decision point that had a trigger, with the failed filters, the score and the action.
- `replay_sessions_new.csv`: the keys replayed in this run, to append to `replay_sessions.csv`.

## What is built

- Decision points, trigger events, filters F1 to F7, the D3 score, sizing, stock fills and every R2 exit for the stock (arm B).
- Arm A (options) needs option bars and is not built yet. Each entry records arm A as `MISSING_DATA` and arm B takes the stock, as R2 section 6 prescribes when no option data exists.

## Readings of the R2 text

R2 is frozen; where its words leave room, the engine reads them as follows. A different reading needs Jaffer's approval and a new rules version.

- "The 4 previous completed 15m bars" may reach into the prior session (the EMAs run across sessions too). A VWAP reclaim needs the previous 15m bar in the same session, because VWAP restarts each session.
- Fading momentum counts 15m bars that start at or after the entry, and "green" means a close above the entry bar open (the underlying price at entry).
- Positions opened by an earlier run are not known to the D4 caps of a later run. Replay consecutive days in one run.
- A position still open at the last loaded bar is UNRESOLVED: no exit row.

All tests use invented symbols and prices.
