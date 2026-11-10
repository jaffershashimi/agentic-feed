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
| `checkpoint` | Check the table's history against a saved row count and digest (no stored rows needed) | one small JSON, only if asked and only when clean |

Exit codes: 0 clean, 2 bad input, 3 problem found.

```
python3 -m consumer.records_store sync --table ledger.csv --store records/ledger
python3 -m consumer.records_store reconcile --table ledger.csv --store records/ledger
python3 -m consumer.records_store restore --store records/ledger --key event_id --out ledger_restored.csv
```

Default keys: `ledger.csv` event_id; `score_parts.csv` decision_id, trade_id, component; `variant_tags.csv` trade_id; `replay_sessions.csv` session_key. Any other table needs `--key`.

## Checkpoint: the lightweight guard the daily run uses

The segment store needs the stored files on disk, and a project run can only read project files as inline text. So the daily run uses `checkpoint`, which needs just two values copied from the last checkpoint: the row count and the digest.

```
python3 -m consumer.records_store checkpoint --table ledger.csv --prev-rows 1677 --prev-digest <64 hex> --out checkpoint-2026-11-11.json
```

The ledger is append-only, so its first N rows must be identical at every later run. `checkpoint` re-renders those N rows in a fixed format (so line endings and quoting cannot cause a false alarm), hashes them, and compares.

| Status | Meaning | Exit |
|---|---|---|
| `BASELINE` | no previous checkpoint given; records the starting point | 0 |
| `OK` | history unchanged; `grew_by` says how many rows were added | 0 |
| `LOST` | fewer rows than before (the Nov 3 case); `lost_rows` says how many | 3 |
| `CHANGED` | same or more rows but an old row or the header differs | 3 |

On `LOST` or `CHANGED` nothing is written and the run must stop before any ledger write and report. The saved JSON holds only the table name, row count, digest, raw sha256 and time. It never overwrites an existing file.

This detects a bad overwrite on the next run. It does not undo it: restoring needs a copy of the rows (the Drive daily exports are not byte-exact, so they are a last resort; the segment store above is the proper source once it is fed from an exact copy).

## What the report tells you

`live_rows`, `store_rows`, `unsynced_rows` (new, normal), `lost_rows` and `changed_rows` (both mean history was altered, stop and investigate), `store_problems` (damage, with a code), and `head`, the sha256 of the newest receipt.

## Limits, stated plainly

- Shadow mode: nothing reads from the segment store yet, and it is not being fed automatically (that needs an exact copy of the live ledger, which a project run cannot hand over as a file). The checkpoint is the part in daily use.
- A checkpoint proves history is unchanged since the last checkpoint. If a bad overwrite happens and nobody runs the check before the next legitimate append, the new baseline would absorb it; that is why the run checks before it writes and the handoff records the row count and digest each day.
- Deleting the newest segment and its receipt together is not visible from the chain alone. Record the first 12 characters of `head` in each daily handoff and compare it with the next run. A shorter chain with an older head is that deletion.
- A row that is wrong at the moment it is first synced is stored as wrong. The store proves records did not change afterward, not that they were right.
- It guards the tables it is pointed at. `decisions.jsonl` is not covered yet.

## Tested

37 tests in `tests/test_records_store.py`, including the Nov 3 case (a live file rolled back by 363 rows is detected by both the store and the checkpoint, and restored exactly from the store), tamper and gap detection, a damaged store blocking sync, line-ending changes not raising a false alarm, and byte-identical output for identical input. Run on Python 3.12, the version CI uses.
