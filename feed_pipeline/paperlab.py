"""Paper research lab: a bounded scenario experiment for a $10,000 paper account. Standard library only; never orders.

Three data classes stay separate and labeled:
- HISTORICAL observations: one outcome per entry date, replaying the research runner's +30% / -15% structure on real
  S&P 500 and VIX closes with Black-Scholes option prices from VIX (modeled prices, not option quotes), re-priced
  under each execution-cost scenario.
- SYNTHETIC scenarios: block-bootstrap equity paths drawn from those historical outcomes, capped at
  MAX_SCENARIOS (one million) trade draws in total. Resampled draws are not new observations.
- PAPER fills: never in this repository. The private desk scores its own ledger with consumer/paper_ledger.py.

Each cost scenario sets the quoted spread, extra slippage, per-contract fees, the price uncertainty of a 15-minute
delayed option quote and the chance a scheduled check finds no usable quote (the exit check is skipped that day).
Results are reported per chronological split (train, validation, holdout); nothing is fitted to any split.

    python3 -m feed_pipeline.paperlab --out feed/paper-lab.json [--csv-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import optionmath, research
from .sources import CBOE_URL, fetch_text, parse_cboe
from .volcore import parse_spx

SCHEMA = "paper-lab/1"
MAX_SCENARIOS = 1_000_000
SEED = 20261025
ACCOUNT = 10_000.0
SIZES = {"score_8": 0.10, "score_9_10": 0.15}  # fractions of paper equity per trade, as proposed; not an approved policy
SPY_PER_SPX = 0.1  # SPY trades near one tenth of the S&P 500 index; used only to size contracts and fees in dollars
FEE_PER_CONTRACT = 0.68  # USD per contract per side: a typical $0.65 commission plus regulatory fees
DELAY_MINUTES = 15  # the TradingView connector serves OPRA option quotes delayed 15 minutes (update_mode delayed_streaming_900)
PATHS_TRADES = 100  # trades per synthetic equity path
BOOTSTRAP_REPS = 200  # moving-block bootstrap replicates for each mean's interval (uncertainty, not scenarios)
# Execution scenarios. "liquid" matches the observed SPY 27-day ATM call quote (bid 14.54 / ask 14.64, about 0.7% of mid).
SCENARIOS = {
    "liquid": {"spread_pct": 0.007, "slippage_pct": 0.0025, "missing_check_prob": 0.02},
    "typical": {"spread_pct": 0.02, "slippage_pct": 0.005, "missing_check_prob": 0.05},
    "wide": {"spread_pct": 0.04, "slippage_pct": 0.01, "missing_check_prob": 0.05},
    "stressed": {"spread_pct": 0.08, "slippage_pct": 0.02, "missing_check_prob": 0.10},
}
RULES = ("call_every_session", "call_above_sma200")


def delay_sigma(vix: float) -> float:
    """Standard deviation of the S&P 500's relative move over the quote delay, from the VIX (annualized, percent)."""
    return vix / 100 * math.sqrt(DELAY_MINUTES / (6.5 * 60 * research.YEAR))


def outcome(rows: list, i: int, kind: str, sc: dict, rng: random.Random) -> dict | None:
    """One historical entry re-priced under scenario sc. Entry pays half the spread, slippage, the delayed-quote error
    (the option is bought at a price the market has since moved away from, adverse half the time) and fees; exits
    receive the bid minus slippage and fees. A missed check skips that day's exit test."""
    hold, dte = research.POLICY["hold_sessions"], research.POLICY["dte_sessions"]
    if i + hold >= len(rows):
        return None
    s0, v0 = rows[i][1], rows[i][2] / 100
    strike = s0 * research.POLICY["moneyness"]
    q0 = optionmath.bsm(s0, strike, dte / research.YEAR, v0, kind)
    mid0 = q0["price"]
    if mid0 <= 0:
        return None
    half, slip = sc["spread_pct"] / 2, sc["slippage_pct"]
    delay_error = abs(q0["delta"]) * s0 * delay_sigma(rows[i][2]) * abs(rng.gauss(0, 1)) * (1 if rng.random() < 0.5 else -1)
    entry = max(mid0 * (1 + half + slip) + delay_error, mid0 * 0.5)
    fee_frac = FEE_PER_CONTRACT / (entry * SPY_PER_SPX * 100)
    for k in range(1, hold + 1):
        if k < hold and rng.random() < sc["missing_check_prob"]:
            continue  # no usable quote at this check: the exit is not tested today
        mid = optionmath.bsm(rows[i + k][1], strike, max(dte - k, 0.5) / research.YEAR, rows[i + k][2] / 100, kind)["price"]
        ret = mid * (1 - half - slip) / entry - 1 - 2 * fee_frac
        if ret >= research.POLICY["target"]:
            return {"net": ret, "exit": "target", "start": i, "end": i + k}
        if ret <= -research.POLICY["stop"]:
            return {"net": ret, "exit": "stop", "start": i, "end": i + k}
    return {"net": ret, "exit": "time", "start": i, "end": i + hold}


def historical(rows: list, rule: str, sc: dict, seed: int) -> dict:
    """Outcomes per split for one rule and scenario, purging any outcome that crosses into the next split."""
    sma, rng = research._sma200(rows), random.Random(seed)
    spec = research.STRATEGIES[rule]
    out = {k: [] for k in research.SPLITS}
    for i in range(len(rows)):
        if not research._signal(spec["filter"], i, rows, sma):
            continue
        split = research._split_of(rows[i][0])
        o = outcome(rows, i, spec["kind"], sc, rng) if split else None
        if o and research._split_of(rows[o["end"]][0]) == split:
            out[split].append(o)
    return out


def non_overlapping(outcomes: list) -> list:
    seq, free = [], -1
    for o in outcomes:
        if o["start"] > free:
            seq.append(o["net"])
            free = o["end"]
    return seq


def synthetic_paths(nets: list, size: float, paths: int, rng: random.Random, block: int = 5) -> dict:
    """Block-bootstrap equity paths of PATHS_TRADES trades from a non-overlapping historical sequence."""
    n = len(nets)
    if n < 2 * block or paths < 1:
        return {"paths": 0, "note": "too few historical trades"}
    terminal, drawdowns, draws = [], [], 0
    for _ in range(paths):
        equity = peak = ACCOUNT
        worst, taken = 0.0, 0
        while taken < PATHS_TRADES:
            start = rng.randrange(0, n - block + 1)
            for r in nets[start:start + block]:
                equity *= 1 + size * r
                peak = max(peak, equity)
                worst = max(worst, 1 - equity / peak)
                taken += 1
                if taken == PATHS_TRADES:
                    break
        draws += taken
        terminal.append(equity)
        drawdowns.append(worst)
    terminal.sort()
    q = lambda xs, p: round(research._quantile(xs, p), 2)  # noqa: E731
    return {"paths": paths, "trade_draws": draws, "terminal_equity": {p: q(terminal, f) for p, f in (("p05", 0.05), ("p50", 0.5), ("p95", 0.95))},
            "p_loss": round(sum(1 for x in terminal if x < ACCOUNT) / paths, 4),
            "p_drawdown_over_15pct": round(sum(1 for d in drawdowns if d > 0.15) / paths, 4),
            "max_drawdown_p50": round(research._quantile(sorted(drawdowns), 0.5), 4)}


def summarize(outcomes: list, rng: random.Random) -> dict:
    nets = [o["net"] for o in outcomes]
    if not nets:
        return {"n": 0}
    lo, hi, _ = research.block_bootstrap_ci(nets, BOOTSTRAP_REPS * len(nets), rng)  # uncertainty only; reported separately
    wins = [x for x in nets if x > 0]
    return {"n": len(nets), "win_rate": round(len(wins) / len(nets), 4), "mean_net": round(sum(nets) / len(nets), 5),
            "mean_ci95": [None if lo is None else round(lo, 5), None if hi is None else round(hi, 5)],
            "exits": {k: sum(1 for o in outcomes if o["exit"] == k) for k in ("target", "stop", "time")}}


def contracts_affordable(rows: list) -> dict:
    """Whole SPY-equivalent ATM contracts each proposed size buys on $10,000 at the latest close (21 sessions to expiry)."""
    s, v = rows[-1][1], rows[-1][2] / 100
    premium = optionmath.bsm(s * SPY_PER_SPX, s * SPY_PER_SPX, research.POLICY["dte_sessions"] / research.YEAR, v, "call")["price"] * 100
    return {"as_of": rows[-1][0].isoformat(), "atm_call_premium_usd": round(premium, 2),
            **{k: math.floor(ACCOUNT * f / premium) if premium > 0 else None for k, f in SIZES.items()}}


def missing_data(rows: list) -> dict:
    """Calendar gaps in the aligned history: sessions where SPX or VIX was missing are absent from the rows."""
    gaps = [(a[0], b[0]) for a, b in zip(rows, rows[1:]) if (b[0] - a[0]).days > 4]
    return {"aligned_rows": len(rows), "gaps_over_4_days": len(gaps), "longest_gap_days": max(((b - a).days for a, b in gaps), default=0)}


def run(rows: list, now: datetime) -> dict:
    if len(rows) < 300:
        raise ValueError(f"only {len(rows)} aligned rows; refusing to evaluate")
    configs = [(rule, name, size_name) for rule in RULES for name in SCENARIOS for size_name in SIZES]
    paths_each = MAX_SCENARIOS // (len(configs) * PATHS_TRADES)
    rng = random.Random(SEED)
    hist, synth, total = {}, {}, 0
    for r_i, rule in enumerate(RULES):
        for s_i, (name, sc) in enumerate(SCENARIOS.items()):
            by_split = historical(rows, rule, sc, SEED + 100 * r_i + s_i)
            hist[f"{rule}/{name}"] = {split: summarize(v, rng) for split, v in by_split.items()}
            seq = non_overlapping(by_split["holdout"])
            for size_name, size in SIZES.items():
                res = synthetic_paths(seq, size, paths_each, rng)
                total += res.get("trade_draws", 0)
                synth[f"{rule}/{name}/{size_name}"] = res
    if total > MAX_SCENARIOS:
        raise RuntimeError(f"scenario bound exceeded: {total} > {MAX_SCENARIOS}")
    return {
        "schemaVersion": SCHEMA, "createdAt": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "availableAt": rows[-1][0].isoformat(), "seed": SEED, "account_usd": ACCOUNT, "sizes_proposed": SIZES,
        "policy": research.POLICY, "policyHash": research.sha(research.POLICY), "scenarios": SCENARIOS,
        "costNotes": {"fee_per_contract_usd": FEE_PER_CONTRACT, "option_quote_delay_minutes": DELAY_MINUTES, "spy_per_spx": SPY_PER_SPX},
        "historical": {"dataClass": "HISTORICAL_UNDERLYING_MODELED_OPTIONS", "splits": research.SPLITS, "results": hist,
                       "missingData": missing_data(rows)},
        "synthetic": {"dataClass": "SYNTHETIC_BLOCK_BOOTSTRAP_OF_HOLDOUT", "maxScenarios": MAX_SCENARIOS, "tradeDraws": total,
                      "tradesPerPath": PATHS_TRADES, "pathsPerConfig": paths_each, "results": synth},
        "paperFills": {"dataClass": "PAPER", "status": "private ledger only; scored by consumer/paper_ledger.py, never published here"},
        "contractsAffordable": contracts_affordable(rows),
        "limitations": [
            "Historical outcomes use modeled option prices from the VIX close, not option quotes; exits are checked at daily closes.",
            "Synthetic paths resample historical holdout outcomes; they are not new observations or a forecast.",
            "Sizes are proposals awaiting the owner's paper-policy decision; fractional contracts are allowed in the paths, "
            "but contractsAffordable shows how many whole contracts each size buys.",
            "The delayed-quote error is symmetric noise around the true price; real delayed fills may be worse in fast markets.",
        ],
        "liveExecution": False,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="feed/paper-lab.json")
    ap.add_argument("--csv-dir", help="read SPX_History.csv and VIX_History.csv from here instead of fetching")
    args = ap.parse_args(argv)
    try:
        texts = {n: (Path(args.csv_dir) / f"{n}_History.csv").read_text() if args.csv_dir else fetch_text(CBOE_URL.format(name=n))
                 for n in ("SPX", "VIX")}
        out = run(research.align(parse_spx(texts["SPX"]), parse_cboe(texts["VIX"], "VIX")), datetime.now(timezone.utc))
        research.atomic_write(Path(args.out), json.dumps(out, sort_keys=True, indent=1) + "\n")
        print(json.dumps({"status": "WRITTEN", "tradeDraws": out["synthetic"]["tradeDraws"]}))
        return 0
    except Exception as exc:  # fail-soft: the previous artifact stays in place
        print(json.dumps({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
