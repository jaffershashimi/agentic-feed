"""Bounded offline research runner (framework handoff items D-H). Standard library only. Never places orders.

It replays the documented +30% / -15% long-option structure over real S&P 500 and VIX history, with option prices
modeled by Black-Scholes from VIX (they are NOT historical option quotes). It compares pre-registered fixed rules
with cash on chronological train / validation / holdout splits, purges outcomes that cross a split boundary, adds a
block-bootstrap of one million resampled outcomes for uncertainty, and reports a separately labeled tail-stress batch.

Every run writes an immutable manifest: dataset, code, policy, strategy and cost-model hashes, seed, data class,
availability time, split ranges, unique observations versus resample count, attempted-model count, metrics and
limitations. Unchanged inputs reuse the previous result (cache by run key) instead of manufacturing new evidence.

    python3 -m feed_pipeline.research --out feed/research.json [--csv-dir DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from . import optionmath
from .sources import CBOE_URL, fetch_text, parse_cboe
from .volcore import parse_spx

SCHEMA = "research-run/1"
HERE = Path(__file__).resolve().parent
YEAR = 252.0

# Pre-registered policy: the desk's documented exit structure, in percent of premium. Not tuned on any split.
POLICY = {"target": 0.30, "stop": 0.15, "hold_sessions": 10, "dte_sessions": 21, "moneyness": 1.0, "iv_source": "VIX close / 100"}
# Pre-registered cost model: half the quoted spread paid on entry and on exit, plus fees, as fractions of the debit.
COSTS = {"spread_pct_of_mid": 0.04, "fees_pct_of_debit": 0.01}
# Pre-registered candidate rules. Each is a fixed rule; none is fitted.
STRATEGIES = {
    "cash": {"kind": None, "filter": "never"},
    "call_every_session": {"kind": "call", "filter": "always"},
    "call_above_sma200": {"kind": "call", "filter": "spx_above_sma200"},
    "put_every_session": {"kind": "put", "filter": "always"},
    "put_vix_above_25": {"kind": "put", "filter": "vix_at_least_25"},
}
SPLITS = {"train": ("1990-01-01", "2014-12-31"), "validation": ("2015-01-01", "2020-12-31"), "holdout": ("2021-01-01", "2100-01-01")}
SEED = 20261024
RESAMPLES_TOTAL = 1_000_000
TAIL_STRESS = {"overnight_gap": -0.07, "iv_multiplier": 1.6, "label": "TAIL_STRESS_SYNTHETIC"}


def sha(data) -> str:
    if not isinstance(data, (bytes, bytearray)):
        data = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def code_hash() -> str:
    return sha(b"".join((HERE / name).read_bytes() for name in ("research.py", "optionmath.py", "volcore.py", "sources.py")))


def align(spx_bars: list, vix: list) -> list:
    """Rows (date, spx_close, vix_close) where both exist and are positive, oldest first, one row per date."""
    v = {d: x for d, x in vix if x and x > 0}
    rows, seen = [], set()
    for b in spx_bars:
        d, close = b[0], b[4]
        if d in v and close > 0 and d not in seen:
            rows.append((d, close, v[d]))
            seen.add(d)
    return rows


def dataset_hash(rows: list) -> str:
    return sha("\n".join(f"{d.isoformat()},{s!r},{v!r}" for d, s, v in rows).encode())


def _sma200(rows: list) -> list:
    out, total = [], 0.0
    for i, (_, s, _) in enumerate(rows):
        total += s
        if i >= 200:
            total -= rows[i - 200][1]
        out.append(total / 200 if i >= 199 else None)
    return out


def _signal(rule: str, i: int, rows: list, sma: list) -> bool:
    if rule == "always":
        return True
    if rule == "spx_above_sma200":
        return sma[i] is not None and rows[i][1] > sma[i]
    if rule == "vix_at_least_25":
        return rows[i][2] >= 25
    return False


def trade_outcome(rows: list, i: int, kind: str, gap: float = 0.0, iv_mult: float = 1.0) -> dict | None:
    """Enter at the close of row i, check the exits at each following close, exit at the bid. Returns the net return
    on the debit, the exit kind and the exit index; None if the history ends before the trade can resolve."""
    hold, dte = POLICY["hold_sessions"], POLICY["dte_sessions"]
    if i + hold >= len(rows):
        return None
    s0, v0 = rows[i][1], rows[i][2] / 100
    strike = s0 * POLICY["moneyness"]
    mid0 = optionmath.bsm(s0, strike, dte / YEAR, v0, kind)["price"]
    half = COSTS["spread_pct_of_mid"] / 2
    debit = mid0 * (1 + half)
    fees = COSTS["fees_pct_of_debit"]
    for k in range(1, hold + 1):
        s = rows[i + k][1] * (1 + gap if k == 1 else 1)
        v = rows[i + k][2] / 100 * (iv_mult if k == 1 else 1)
        mid = optionmath.bsm(s, strike, max(dte - k, 0.5) / YEAR, v, kind)["price"]
        ret = mid * (1 - half) / debit - 1
        if ret >= POLICY["target"]:
            return {"net": ret - fees, "exit": "target", "end": i + k}
        if ret <= -POLICY["stop"]:
            return {"net": ret - fees, "exit": "stop", "end": i + k}  # gaps fill at the observed close, never at the level
    return {"net": ret - fees, "exit": "time", "end": i + hold}


def _split_of(d: date) -> str | None:
    for name, (a, b) in SPLITS.items():
        if date.fromisoformat(a) <= d <= date.fromisoformat(b):
            return name
    return None


def _quantile(sorted_values: list, q: float):
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))]


def _max_drawdown(returns: list, allocation: float = 0.10) -> float:
    equity = peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1 + allocation * r
        peak = max(peak, equity)
        worst = max(worst, 1 - equity / peak)
    return worst


def block_bootstrap_ci(values: list, resamples: int, rng: random.Random, block: int = 20) -> tuple:
    """Moving-block bootstrap of the mean (dependent, overlapping outcomes). Returns (low, high, outcomes drawn)."""
    n = len(values)
    if n < 2 * block:
        return None, None, 0
    reps = max(1, resamples // n)
    means = []
    for _ in range(reps):
        total, count = 0.0, 0
        while count < n:
            start = rng.randrange(0, n - block + 1)
            for x in values[start:start + block]:
                total += x
                count += 1
                if count == n:
                    break
        means.append(total / n)
    means.sort()
    return _quantile(means, 0.025), _quantile(means, 0.975), reps * n


def metrics(outcomes: list, rng: random.Random, resamples: int) -> dict:
    nets = [o["net"] for o in outcomes]
    if not nets:
        return {"n": 0, "note": "no observations"}
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x <= 0]
    s = sorted(nets)
    # Non-overlapping sequence (each trade starts after the previous one exits) for a realistic drawdown path.
    seq, next_free = [], -1
    for o in outcomes:
        if o["start"] > next_free:
            seq.append(o["net"])
            next_free = o["end"]
    lo, hi, drawn = block_bootstrap_ci(nets, resamples, rng)
    return {
        "n": len(nets), "win_rate": round(len(wins) / len(nets), 4), "mean_net": round(sum(nets) / len(nets), 5),
        "payoff_ratio": round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 3) if wins and losses and sum(losses) else None,
        "p05": round(_quantile(s, 0.05), 4), "p01": round(_quantile(s, 0.01), 4), "worst": round(s[0], 4),
        "exits": {k: sum(1 for o in outcomes if o["exit"] == k) for k in ("target", "stop", "time")},
        "mean_ci95": [None if lo is None else round(lo, 5), None if hi is None else round(hi, 5)],
        "ci_excludes_zero": None if lo is None else (lo > 0 or hi < 0),
        "non_overlapping_trades": len(seq), "max_drawdown_at_10pct_allocation": round(_max_drawdown(seq), 4),
        "resampled_outcomes": drawn,
    }


def evaluate(rows: list, seed: int = SEED) -> dict:
    sma = _sma200(rows)
    rng = random.Random(seed)
    results, coverage = {}, {}
    per_split_counts = {}
    for name, spec in STRATEGIES.items():
        by_split = {k: [] for k in SPLITS}
        purged = unresolved = 0
        for i in range(len(rows)):
            if spec["kind"] is None or not _signal(spec["filter"], i, rows, sma):
                continue
            split = _split_of(rows[i][0])
            if split is None:
                continue
            o = trade_outcome(rows, i, spec["kind"])
            if o is None:
                unresolved += 1
                continue
            if _split_of(rows[o["end"]][0]) != split:
                purged += 1  # the outcome window crosses into the next split
                continue
            by_split[split].append(dict(o, start=i))
        coverage[name] = {"purged_cross_split": purged, "unresolved_end_of_history": unresolved}
        per_split_counts[name] = {k: len(v) for k, v in by_split.items()}
        per_cell = RESAMPLES_TOTAL // (sum(1 for x in STRATEGIES.values() if x["kind"]) * len(SPLITS))
        results[name] = {k: (metrics(v, rng, per_cell) if spec["kind"] else
                             {"n": 0, "mean_net": 0.0, "note": "cash: no position, return 0"}) for k, v in by_split.items()}
    # Tail stress on holdout entries of the unfiltered call and put rules: a -7% overnight gap with IV up 60% on day one.
    stress = {}
    for name in ("call_every_session", "put_every_session"):
        kind = STRATEGIES[name]["kind"]
        nets = [o["net"] for i in range(len(rows)) if _split_of(rows[i][0]) == "holdout"
                for o in [trade_outcome(rows, i, kind, TAIL_STRESS["overnight_gap"], TAIL_STRESS["iv_multiplier"])] if o]
        s = sorted(nets)
        stress[name] = {"n": len(nets), "mean_net": round(sum(nets) / len(nets), 4) if nets else None,
                        "p05": round(_quantile(s, 0.05), 4) if s else None, "worst": round(s[0], 4) if s else None}
    unique = sum(sum(c.values()) for c in per_split_counts.values())
    drawn = sum(m.get("resampled_outcomes", 0) for r in results.values() for m in r.values())
    return {"results": results, "coverage": coverage, "observation_counts": per_split_counts, "unique_observations": unique,
            "resampled_outcomes": drawn, "tail_stress": dict(TAIL_STRESS, results=stress)}


def run_key(ds_hash: str) -> str:
    return sha({"dataset": ds_hash, "code": code_hash(), "policy": sha(POLICY), "strategies": sha(STRATEGIES),
                "costs": sha(COSTS), "splits": sha(SPLITS), "seed": SEED, "resamples": RESAMPLES_TOTAL})


def build(rows: list, source_hashes: dict, previous: dict | None, now: datetime) -> tuple:
    """Returns (manifest, reused). Reuses the previous manifest unchanged when the run key matches."""
    if len(rows) < 300:
        raise ValueError(f"only {len(rows)} aligned rows; refusing to evaluate")
    ds = dataset_hash(rows)
    key = run_key(ds)
    if previous and previous.get("runKey") == key:
        return previous, True
    attempted = sorted(set((previous or {}).get("attemptedStrategyHashes", [])) | {sha({n: s}) for n, s in STRATEGIES.items()})
    body = evaluate(rows)
    manifest = {
        "schemaVersion": SCHEMA, "experimentId": "desk-structure-historical-v1", "runKey": key,
        "codeCommit": os.environ.get("GITHUB_SHA"), "codeHash": code_hash(), "datasetHash": ds, "sourceHashes": source_hashes,
        "datasetLicenseRef": "Cboe public index history CSVs (SPX, VIX); terms pending P0-3 verification (docs/DATA_SOURCES.md)",
        "dataClass": "HISTORICAL_UNDERLYING_MODELED_OPTIONS",
        "policyHash": sha(POLICY), "policy": POLICY, "strategyHash": sha(STRATEGIES), "strategies": STRATEGIES,
        "costModelHash": sha(COSTS), "costModel": COSTS, "seed": SEED, "createdAt": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "availableAt": rows[-1][0].isoformat(), "firstDate": rows[0][0].isoformat(), "rows": len(rows),
        "splits": SPLITS, "uniqueObservationCount": body["unique_observations"], "scenarioCount": body["resampled_outcomes"],
        "attemptedStrategyHashes": attempted, "attemptedModelCount": len(attempted),
        "metrics": body["results"], "coverage": body["coverage"], "observationCounts": body["observation_counts"],
        "tailStress": body["tail_stress"],
        "limitations": [
            "Option prices are Black-Scholes values from the VIX close, not historical option quotes: no real bid/ask, skew, fills or assignment.",
            "Exits are checked at daily closes only; an intraday touch of a level is not seen, and a gap fills at the next close.",
            "Outcomes start every session and overlap; the bootstrap uses moving blocks, and resampled outcomes are not new observations.",
            "Index options proxy the structure; single-stock options carry different volatility, skew, spreads and event risk.",
            "Rules are pre-registered and untuned; the holdout is reported once per code version and must not be used to tune.",
            "Tail stress is synthetic and labeled separately; it is not a probability estimate.",
        ],
        "liveExecution": False,
    }
    manifest["artifactHash"] = sha({k: v for k, v in manifest.items() if k != "artifactHash"})
    return manifest, False


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="feed/research.json")
    ap.add_argument("--csv-dir", help="read SPX_History.csv and VIX_History.csv from here instead of fetching")
    args = ap.parse_args(argv)
    out = Path(args.out)
    lock = out.with_suffix(".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # one run at a time
    except FileExistsError:
        print(json.dumps({"status": "SKIPPED", "reason": f"lock {lock} exists"}))
        return 0
    try:
        texts = {}
        for name in ("SPX", "VIX"):
            texts[name] = (Path(args.csv_dir) / f"{name}_History.csv").read_text() if args.csv_dir else fetch_text(CBOE_URL.format(name=name))
        rows = align(parse_spx(texts["SPX"]), parse_cboe(texts["VIX"], "VIX"))
        previous = json.loads(out.read_text()) if out.exists() else None
        manifest, reused = build(rows, {k: sha(v.encode()) for k, v in texts.items()}, previous, datetime.now(timezone.utc))
        if not reused:
            atomic_write(out, json.dumps(manifest, sort_keys=True, indent=1) + "\n")
        print(json.dumps({"status": "REUSED" if reused else "WRITTEN", "runKey": manifest["runKey"], "rows": manifest["rows"],
                          "uniqueObservations": manifest["uniqueObservationCount"], "scenarios": manifest["scenarioCount"]}))
        return 0
    except Exception as exc:  # fail-soft: the previous artifact stays in place
        print(json.dumps({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    finally:
        os.close(fd)
        os.unlink(lock)


if __name__ == "__main__":
    sys.exit(main())
