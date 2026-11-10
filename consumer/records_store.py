"""Append-only records store with a hash chain (shadow mode).

Why: on 2026-11-03 a chat overwrote the paper ledger from a stale copy and 363 rows
vanished. The ledger lives in a project file that anything can rewrite. This tool keeps a
second, tamper-evident copy made only of NEW files, so a rewrite of the live file is caught
the same day and the lost rows can be restored from the store.

How: each `sync` writes one immutable segment (only rows the store has not seen) and one
receipt. A receipt names its segment's sha256 and the sha256 of the previous receipt, so
an edited, deleted or reordered file breaks the chain. Nothing is ever edited in place.

Commands (all print a JSON report; none ever edits an existing file):
  sync       add rows the store does not have; refuse (exit 3) if the live table lost or
             changed rows the store already holds, or if the store itself is damaged
  reconcile  compare the live table with the store, write nothing
  verify     check the store's own integrity
  restore    write the store's union of rows to a NEW file (refuses to overwrite)

Exit codes: 0 clean, 2 usage or file error, 3 problem found (lost, changed, damaged).

Limit: deleting the newest segment and its receipt together cannot be seen from the chain
alone. Record the `head` value (first 12 characters) in the daily handoff and compare it
with the next run's report.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
GENESIS = "0" * 64
DEFAULT_KEYS = {
    "ledger.csv": ["event_id"],
    "score_parts.csv": ["decision_id", "trade_id", "component"],
    "variant_tags.csv": ["trade_id"],
    "replay_sessions.csv": ["session_key"],
}


class StoreError(Exception):
    """Bad input (exit 2)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    """Parse a CSV into (header, rows). Rejects empty files, ragged rows, blank headers."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except OSError as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc
    parsed = [r for r in csv.reader(io.StringIO(text, newline="")) if r]
    if not parsed:
        raise StoreError(f"{path} is empty")
    header, rows = parsed[0], parsed[1:]
    if any(not h.strip() for h in header):
        raise StoreError(f"{path} has a blank column name")
    for n, r in enumerate(rows, start=2):
        if len(r) != len(header):
            raise StoreError(f"{path} row {n} has {len(r)} cells, header has {len(header)}")
    return header, rows


def render_csv(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def keyed(header: list[str], rows: list[list[str]], key_cols: list[str], label: str) -> dict[tuple, list[str]]:
    try:
        idx = [header.index(c) for c in key_cols]
    except ValueError as exc:
        raise StoreError(f"{label}: key column missing from header ({exc})") from exc
    out: dict[tuple, list[str]] = {}
    for r in rows:
        k = tuple(r[i] for i in idx)
        if k in out:
            raise StoreError(f"{label}: duplicate key {'|'.join(k)}")
        out[k] = r
    return out


def key_columns(table: Path, override: str | None) -> list[str]:
    if override:
        return [c.strip() for c in override.split(",") if c.strip()]
    keys = DEFAULT_KEYS.get(Path(table).name)
    if not keys:
        raise StoreError(f"no default key for {Path(table).name}; pass --key col1,col2")
    return keys


def _seg_names(seq: int) -> tuple[str, str]:
    return f"seg-{seq:06d}.csv", f"seg-{seq:06d}.json"


def load_store(store: Path, key_cols: list[str]):
    """Return (header, rows_by_key, problems, head_sha, next_seq). Never raises on damage."""
    store = Path(store)
    problems: list[dict] = []
    receipts = sorted(store.glob("seg-*.json")) if store.is_dir() else []
    segments = sorted(store.glob("seg-*.csv")) if store.is_dir() else []
    seqs = []
    for p in receipts:
        try:
            seqs.append(int(p.stem.split("-")[1]))
        except (IndexError, ValueError):
            problems.append({"code": "BAD_NAME", "file": p.name})
    for p in segments:
        try:
            s = int(p.stem.split("-")[1])
        except (IndexError, ValueError):
            problems.append({"code": "BAD_NAME", "file": p.name})
            continue
        if s not in seqs:
            problems.append({"code": "SEGMENT_WITHOUT_RECEIPT", "file": p.name})
    seqs = sorted(seqs)
    header: list[str] | None = None
    rows_by_key: dict[tuple, list[str]] = {}
    prev = GENESIS
    expected = 1
    head = GENESIS
    for seq in seqs:
        if seq != expected:
            problems.append({"code": "GAP", "expected_seq": expected, "found_seq": seq})
            expected = seq
        seg_name, rec_name = _seg_names(seq)
        rec_path = store / rec_name
        rec_bytes = rec_path.read_bytes()
        try:
            rec = json.loads(rec_bytes)
        except ValueError:
            problems.append({"code": "BAD_RECEIPT", "file": rec_name})
            expected += 1
            continue
        if rec.get("prev_receipt_sha256") != prev:
            problems.append({"code": "CHAIN_BROKEN", "file": rec_name})
        seg_path = store / seg_name
        if not seg_path.exists():
            problems.append({"code": "SEGMENT_MISSING", "file": seg_name})
        else:
            seg_bytes = seg_path.read_bytes()
            if sha256_bytes(seg_bytes) != rec.get("segment_sha256"):
                problems.append({"code": "SEGMENT_HASH", "file": seg_name})
            else:
                try:
                    h, rows = read_table(seg_path)
                except StoreError:
                    problems.append({"code": "SEGMENT_UNREADABLE", "file": seg_name})
                    h, rows = None, []
                if h is not None:
                    if len(rows) != rec.get("rows"):
                        problems.append({"code": "ROWCOUNT", "file": seg_name})
                    if header is None:
                        header = h
                    elif h != header:
                        problems.append({"code": "HEADER_CHANGED", "file": seg_name})
                    try:
                        for k, r in keyed(h, rows, key_cols, seg_name).items():
                            if k in rows_by_key:
                                problems.append({"code": "DUP_KEY", "file": seg_name, "key": "|".join(k)})
                            else:
                                rows_by_key[k] = r
                    except StoreError as exc:
                        problems.append({"code": "KEY_ERROR", "file": seg_name, "detail": str(exc)})
        prev = sha256_bytes(rec_bytes)
        head = prev
        expected += 1
    return header, rows_by_key, problems, head, expected


def compare(src_header, src_rows, store_header, store_rows):
    """Compare live table rows (keyed) with the store. Returns (new, lost, changed)."""
    new = [k for k in src_rows if k not in store_rows]
    lost = [k for k in store_rows if k not in src_rows]
    changed = [k for k, r in store_rows.items() if k in src_rows and src_rows[k] != r]
    return new, lost, changed


def report_for(table: Path, store: Path, key_override: str | None):
    key_cols = key_columns(table, key_override)
    src_header, src_list = read_table(table)
    src_rows = keyed(src_header, src_list, key_cols, str(table))
    store_header, store_rows, problems, head, next_seq = load_store(store, key_cols)
    rep = {
        "table": Path(table).name,
        "key": key_cols,
        "live_rows": len(src_rows),
        "live_sha256": sha256_bytes(Path(table).read_bytes()),
        "store_rows": len(store_rows),
        "segments": next_seq - 1,
        "head": head,
        "store_problems": problems,
        "unsynced_rows": 0,
        "lost_rows": 0,
        "changed_rows": 0,
        "header_changed": False,
    }
    if store_header is not None and store_header != src_header:
        rep["header_changed"] = True
        return rep, src_header, src_rows, store_header, store_rows, [], [], [], next_seq, head
    new, lost, changed = compare(src_header, src_rows, store_header, store_rows)
    rep["unsynced_rows"] = len(new)
    rep["lost_rows"] = len(lost)
    rep["changed_rows"] = len(changed)
    if lost:
        rep["lost_sample"] = ["|".join(k) for k in lost[:5]]
    if changed:
        rep["changed_sample"] = ["|".join(k) for k in changed[:5]]
    return rep, src_header, src_rows, store_header, store_rows, new, lost, changed, next_seq, head


def bad(rep: dict) -> bool:
    return bool(rep["store_problems"] or rep["lost_rows"] or rep["changed_rows"] or rep["header_changed"])


def sync(table: Path, store: Path, key_override: str | None = None, created: str | None = None) -> tuple[dict, int]:
    rep, src_header, src_rows, _sh, _sr, new, _l, _c, next_seq, head = report_for(table, store, key_override)
    rep["action"] = "none"
    if bad(rep):
        rep["action"] = "refused"
        return rep, 3
    if not new:
        return rep, 0
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    seg_name, rec_name = _seg_names(next_seq)
    seg_path, rec_path = store / seg_name, store / rec_name
    if seg_path.exists() or rec_path.exists():
        raise StoreError(f"{seg_name} or {rec_name} already exists; refusing to overwrite")
    rows = [src_rows[k] for k in new]
    seg_bytes = render_csv(src_header, rows)
    receipt = {
        "version": VERSION,
        "seq": next_seq,
        "table": Path(table).name,
        "created": created or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": len(rows),
        "segment_file": seg_name,
        "segment_sha256": sha256_bytes(seg_bytes),
        "prev_receipt_sha256": head,
        "live_rows_at_sync": rep["live_rows"],
        "live_sha256_at_sync": rep["live_sha256"],
        "header": src_header,
    }
    rec_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    seg_path.write_bytes(seg_bytes)
    rec_path.write_bytes(rec_bytes)
    rep["action"] = "wrote"
    rep["wrote"] = {"segment": seg_name, "receipt": rec_name, "rows": len(rows)}
    rep["store_rows"] += len(rows)
    rep["segments"] = next_seq
    rep["head"] = sha256_bytes(rec_bytes)
    rep["unsynced_rows"] = 0
    return rep, 0


def reconcile(table: Path, store: Path, key_override: str | None = None) -> tuple[dict, int]:
    rep = report_for(table, store, key_override)[0]
    return rep, (3 if bad(rep) else 0)


def verify(store: Path, key: str) -> tuple[dict, int]:
    key_cols = [c.strip() for c in key.split(",") if c.strip()]
    header, rows, problems, head, next_seq = load_store(store, key_cols)
    rep = {"store_rows": len(rows), "segments": next_seq - 1, "head": head, "store_problems": problems}
    return rep, (3 if problems else 0)


def restore(store: Path, key: str, out: Path) -> tuple[dict, int]:
    out = Path(out)
    if out.exists():
        raise StoreError(f"{out} exists; restore never overwrites")
    key_cols = [c.strip() for c in key.split(",") if c.strip()]
    header, rows_by_key, problems, head, next_seq = load_store(store, key_cols)
    rep = {"store_rows": len(rows_by_key), "segments": next_seq - 1, "head": head, "store_problems": problems}
    if problems or header is None:
        rep["action"] = "refused"
        return rep, 3
    out.write_bytes(render_csv(header, list(rows_by_key.values())))
    rep["action"] = "wrote"
    rep["out"] = str(out)
    rep["out_sha256"] = sha256_bytes(out.read_bytes())
    return rep, 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sync", "reconcile"):
        p = sub.add_parser(name)
        p.add_argument("--table", required=True, type=Path)
        p.add_argument("--store", required=True, type=Path)
        p.add_argument("--key")
        if name == "sync":
            p.add_argument("--created", help="override the receipt timestamp (tests)")
    p = sub.add_parser("verify")
    p.add_argument("--store", required=True, type=Path)
    p.add_argument("--key", required=True)
    p = sub.add_parser("restore")
    p.add_argument("--store", required=True, type=Path)
    p.add_argument("--key", required=True)
    p.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "sync":
            rep, code = sync(args.table, args.store, args.key, args.created)
        elif args.cmd == "reconcile":
            rep, code = reconcile(args.table, args.store, args.key)
        elif args.cmd == "verify":
            rep, code = verify(args.store, args.key)
        else:
            rep, code = restore(args.store, args.key, args.out)
    except StoreError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(rep, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
