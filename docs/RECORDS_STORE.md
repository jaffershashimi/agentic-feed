# Records store (shadow mode)

`consumer/records_store.py` keeps a second, tamper-evident copy of the paper records so a bad overwrite of the live file is caught the same day and can be undone.

**Why it exists.** On 2026-11-03 a chat rewrote the paper ledger from a stale copy and 363 rows vanished. They were restored byte for byte from another copy, but only because one existed. The live files sit in a project that anything can rewrite, so the store never edits anything. It only adds new files.

## How it works

Each `sync` looks at the live table and writes only the rows the store has not seen:

- `seg-000001.csv`: the new rows (header plus rows).
- `seg-000001.json`: a receipt with the segment's sha256, the row count, and the sha256 of the previous receipt.

Because every receipt names the one before it, an edited, deleted or reordered file breaks the chain. The union of all segments is the full table.

| Command | What it does | Writes |
|---|---|---|
| `sync` | Add new rows. Refuses (exit 3) if the live table lost or changed rows the store holds, if the header changed, or if the store is damaged | new files only |
| `reconcile` | Compare live table and store, report lost, changed and unsynced rows | nothing |
| `verify` | Check the store's own chain and hashes | nothing |
| `restore` | Write the store's union of rows to a NEW file (never overwrites) | one new file |

Exit codes: 0 clean, 2 bad input, 3 problem found.

```
python3 -m consumer.records_store sync --table ledger.csv --store records/ledger
python3 -m consumer.records_store reconcile --table ledger.csv --store records/ledger
python3 -m consumer.records_store restore --store records/ledger --key event_id --out ledger_restored.csv
```

Default keys: `ledger.csv` event_id; `score_parts.csv` decision_id, trade_id, component; `variant_tags.csv` trade_id; `replay_sessions.csv` session_key. Any other table needs `--key`.

## What the report tells you

`live_rows`, `store_rows`, `unsynced_rows` (new, normal), `lost_rows` and `changed_rows` (both mean history was altered, stop and investigate), `store_problems` (damage, with a code), and `head`, the sha256 of the newest receipt.

## Limits, stated plainly

- Shadow mode: nothing reads from the store yet. It watches and can restore; it does not replace the live files.
- Deleting the newest segment and its receipt together is not visible from the chain alone. Record the first 12 characters of `head` in each daily handoff and compare it with the next run. A shorter chain with an older head is that deletion.
- A row that is wrong at the moment it is first synced is stored as wrong. The store proves records did not change afterward, not that they were right.
- It guards the tables it is pointed at. `decisions.jsonl` is not covered yet.

## Tested

25 tests in `tests/test_records_store.py`, including the Nov 3 case (a live file rolled back by 363 rows is detected, refused, and restored exactly), tamper and gap detection, a damaged store blocking sync, and byte-identical output for identical input.
