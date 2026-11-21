"""Append-only, hash-chained ledger with a daily checkpoint (same idea as the existing desk's records store: a saved row count and digest, checked before every write).

Rows are never edited or deleted. Each row names the hash of the one before it, so an edited, removed or reordered row breaks the chain. The checkpoint (row count and chain digest)
is exported daily; before any write the ledger must still contain the checkpointed history. A broker reset never touches the ledger; if the account is ever reset, export a checkpoint first.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


class LedgerError(Exception):
    """The ledger is damaged, lost history, or a write was refused. Nothing was written."""


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def row_hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + canon(body)).encode()).hexdigest()


class Ledger:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.path = self.dir / "ledger.jsonl"
        self.cp_dir = self.dir / "checkpoints"

    def rows(self) -> list:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]

    def verify(self) -> dict:
        prev, n = "0" * 64, 0
        for r in self.rows():
            body = {k: v for k, v in r.items() if k not in ("hash",)}
            if body.get("prev") != prev or r.get("hash") != row_hash(prev, {k: v for k, v in body.items() if k != "prev"}):
                raise LedgerError(f"chain broken at row {n + 1}")
            if body.get("seq") != n + 1:
                raise LedgerError(f"sequence broken at row {n + 1}")
            prev, n = r["hash"], n + 1
        return {"rows": n, "digest": prev}

    def latest_checkpoint(self):
        if not self.cp_dir.exists():
            return None
        files = sorted(self.cp_dir.glob("*.json"))
        return json.loads(files[-1].read_text()) if files else None

    def guard(self) -> dict:
        """Run before every write: chain intact and the checkpointed history still present."""
        v = self.verify()
        cp = self.latest_checkpoint()
        if cp:
            rows = self.rows()
            if cp["rows"] > v["rows"] or (cp["rows"] and rows[cp["rows"] - 1]["hash"] != cp["digest"]):
                raise LedgerError("history changed or lost since the last checkpoint: nothing written")
        return v

    def append(self, rtype: str, now: datetime | None = None, **fields) -> dict:
        v = self.guard()
        self.dir.mkdir(parents=True, exist_ok=True)
        ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"seq": v["rows"] + 1, "ts": ts, "type": rtype, **fields}
        h = row_hash(v["digest"], body)
        row = {**body, "prev": v["digest"], "hash": h}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(canon(row) + "\n")
        return row

    def write_checkpoint(self, day: str) -> dict:
        v = self.guard()
        self.cp_dir.mkdir(parents=True, exist_ok=True)
        cp = {"day": day, "rows": v["rows"], "digest": v["digest"]}
        p = self.cp_dir / f"{day}.json"
        if p.exists() and json.loads(p.read_text()) != cp:
            # same day, more rows: keep every checkpoint, never overwrite one
            p = self.cp_dir / f"{day}-{v['rows']:06d}.json"
        p.write_text(json.dumps(cp, indent=1) + "\n")
        return cp


def reduce(rows: list) -> dict:
    """Current state from the rows alone: frozen specs, open and closed structures (real and control), halts."""
    specs, open_r, open_c, closed_r, closed_c = {}, {}, {}, [], []
    halts, no_fill = {}, 0
    for r in rows:
        t = r["type"]
        if t == "SPEC_FROZEN":
            specs[r["spec_id"]] = r
        elif t == "ENTRY":
            open_r[r["id"]] = r
        elif t == "CONTROL_ENTRY":
            open_c[r["id"]] = r
        elif t == "EXIT" and r["id"] in open_r:
            closed_r.append({"entry": open_r.pop(r["id"]), "exit": r})
        elif t == "CONTROL_EXIT" and r["id"] in open_c:
            closed_c.append({"entry": open_c.pop(r["id"]), "exit": r})
        elif t == "NO_FILL":
            no_fill += 1
        elif t == "HALT":
            halts[(r["scope"], r.get("spec_id"))] = True
        elif t == "RESUME":
            halts[(r["scope"], r.get("spec_id"))] = False
    return {"specs": specs, "open": open_r, "open_control": open_c, "closed": closed_r, "closed_control": closed_c, "halts": halts, "no_fill": no_fill}
