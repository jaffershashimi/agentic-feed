"""Strategy specs: one JSON file per spec. A spec is frozen in the ledger (with its sha256) before its first fill and cannot change afterwards; a change is a new spec id.
A spec trades only when approvals.json lists it with the same sha256 (owner approval is a one-word message; no approval, no trades)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import structures as S

REQUIRED = ("id", "name", "family", "structure", "underlying_type", "underlyings", "dte_min", "dte_max", "dte_target", "legs", "entry", "exit")
COND_KEYS = {"ret_1d_max", "ret_5d_max", "ret_5d_min", "rsi14_max", "rsi14_min", "above_sma50", "iv_rv_min", "iv_rv_max", "breakout_days_min"}
BREAKOUT_DAYS = (10, 20, 55)
# Fail closed: a spec may carry only fields the engine knows. A draft names the engine features it needs; any feature not listed here is refused.
TOP_KEYS = set(REQUIRED) | {"sources", "max_premium_usd", "directional_group", "batch", "twin_of", "requires_engine"}
EXIT_KEYS = {"profit_target_pct", "loss_stop_pct", "time_exit_dte", "max_hold_sessions", "managed"}
SUPPORTED_FEATURES = {"short_dte_expiries", "max_premium_usd", "directional_cap", "exit_before_expiry_min_1", "debit_spread_exit_min_1", "breakout_signal", "unmanaged_exit"}
LONG_PREMIUM_SPREADS = ("debit-vertical-call", "debit-vertical-put")
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
    unknown = set(spec) - TOP_KEYS
    if unknown:
        raise SpecError(f"{spec['id']}: unknown fields {sorted(unknown)} (refused, never partly used)")
    missing_features = set(spec.get("requires_engine") or []) - SUPPORTED_FEATURES
    if missing_features:
        raise SpecError(f"{spec['id']}: needs engine features not built: {sorted(missing_features)}")
    ex = spec["exit"]
    if set(ex) - EXIT_KEYS:
        raise SpecError(f"{spec['id']}: unknown exit fields {sorted(set(ex) - EXIT_KEYS)}")
    for k in ("profit_target_pct", "loss_stop_pct", "time_exit_dte", "max_hold_sessions"):
        if k not in ex:
            raise SpecError(f"{spec['id']}: exit needs {k}")
    if ex.get("managed", True) is False:
        if ex["profit_target_pct"] is not None or ex["loss_stop_pct"] is not None:
            raise SpecError(f"{spec['id']}: an unmanaged spec has no profit target and no loss stop")
    elif not (isinstance(ex["profit_target_pct"], (int, float)) and isinstance(ex["loss_stop_pct"], (int, float))
              and 0 < ex["profit_target_pct"] <= 1.5 and 0 < ex["loss_stop_pct"] <= 1.0):
        raise SpecError(f"{spec['id']}: profit target or loss stop out of range")
    if not (isinstance(ex["time_exit_dte"], int) and ex["time_exit_dte"] >= 1):
        raise SpecError(f"{spec['id']}: close at least 1 day before expiry")
    if has_short_legs(spec["structure"]) and spec["structure"] not in LONG_PREMIUM_SPREADS and ex["time_exit_dte"] < 8:
        raise SpecError(f"{spec['id']}: a structure with short legs must close at 8 or more days to expiry (no short legs into expiration week)")
    if "max_premium_usd" in spec and not (isinstance(spec["max_premium_usd"], (int, float)) and 0 < spec["max_premium_usd"] <= 1000):
        raise SpecError(f"{spec['id']}: max_premium_usd must be between 0 and the 1,000 max loss")
    if spec["dte_min"] <= ex["time_exit_dte"]:
        raise SpecError(f"{spec['id']}: dte_min must be above the time exit")
    ent = spec["entry"]
    if not (ent.get("always") is True or (isinstance(ent.get("conditions"), dict) and ent["conditions"] and set(ent["conditions"]) <= COND_KEYS)):
        raise SpecError(f"{spec['id']}: entry must be always or known conditions {sorted(COND_KEYS)}")
    if "breakout_days_min" in (ent.get("conditions") or {}) and ent["conditions"]["breakout_days_min"] not in BREAKOUT_DAYS:
        raise SpecError(f"{spec['id']}: breakout_days_min must be one of {BREAKOUT_DAYS}")
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
