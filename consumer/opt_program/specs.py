"""Strategy specs: one JSON file per spec. A spec is frozen in the ledger (with its sha256) before its first fill and cannot change afterwards; a change is a new spec id.
A spec trades only when approvals.json lists it with the same sha256 (owner approval is a one-word message; no approval, no trades)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import structures as S

REQUIRED = ("id", "name", "family", "structure", "underlying_type", "underlyings", "dte_min", "dte_max", "dte_target", "legs", "entry", "exit")
COND_KEYS = {"ret_1d_max", "ret_5d_max", "ret_5d_min", "rsi14_max", "rsi14_min", "above_sma50", "iv_rv_min", "iv_rv_max"}
FAMILIES = ("credit-spread", "iron-condor", "debit-spread", "butterfly", "long-option", "calendar", "diagonal")


class SpecError(Exception):
    pass


def sha(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def has_short_legs(kind: str) -> bool:
    return kind not in ("long-call", "long-put")  # every other structure sells at least one option (a debit vertical sells the far strike)


def validate(spec: dict) -> dict:
    for k in REQUIRED:
        if k not in spec:
            raise SpecError(f"{spec.get('id', '?')}: missing {k}")
    if spec["structure"] not in S.KINDS:
        raise SpecError(f"{spec['id']}: unknown structure")
    if spec["family"] not in FAMILIES:
        raise SpecError(f"{spec['id']}: unknown family")
    if spec["underlying_type"] not in ("etf", "stock"):
        raise SpecError(f"{spec['id']}: underlying_type must be etf or stock")
    ex = spec["exit"]
    for k in ("profit_target_pct", "loss_stop_pct", "time_exit_dte", "max_hold_sessions"):
        if k not in ex:
            raise SpecError(f"{spec['id']}: exit needs {k}")
    if not (0 < ex["profit_target_pct"] <= 1.5 and 0 < ex["loss_stop_pct"] <= 1.0):
        raise SpecError(f"{spec['id']}: profit target or loss stop out of range")
    if has_short_legs(spec["structure"]) and ex["time_exit_dte"] < 8:
        raise SpecError(f"{spec['id']}: a structure with short legs must close at 8 or more days to expiry (no short legs into expiration week)")
    if spec["dte_min"] <= ex["time_exit_dte"]:
        raise SpecError(f"{spec['id']}: dte_min must be above the time exit")
    ent = spec["entry"]
    if not (ent.get("always") is True or (isinstance(ent.get("conditions"), dict) and ent["conditions"] and set(ent["conditions"]) <= COND_KEYS)):
        raise SpecError(f"{spec['id']}: entry must be always or known conditions {sorted(COND_KEYS)}")
    return spec


def load_dir(path) -> dict:
    out = {}
    for p in sorted(Path(path).glob("O-*.json")):
        spec = validate(json.loads(p.read_text()))
        if spec["id"] in out:
            raise SpecError(f"duplicate id {spec['id']}")
        out[spec["id"]] = spec
    return out


def approved(specs: dict, approvals: dict) -> dict:
    """Only specs whose current sha256 is the one the owner approved."""
    ok = {}
    for sid, spec in specs.items():
        a = (approvals or {}).get(sid) or {}
        if a.get("approved") is True and a.get("spec_sha256") == sha(spec) and spec["structure"] in S.ENGINE_SENDS:
            ok[sid] = spec
    return ok
