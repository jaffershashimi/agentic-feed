"""Rule-level risk simulator (issue P4-5). Percentages and assumed win rates only; no account values or positions.

Seeded Monte Carlo of a rule set: position size (fraction of equity per trade), stop and target (fractions of the
position), trades per day, stop-after-first-loss, a drawdown brake (stop trading once equity falls this far below its
peak) and a gap model (a stop that gaps: the position loses `gap_loss` instead of `stop`). Equity starts at 1.0.

Outputs: probability the brake trips today (from a fresh peak) and over the horizon, probability of a deeper "ruin"
level, max-drawdown and terminal-equity percentiles, the full-Kelly fraction and a fractional-Kelly cap, and the
worst-case single-trade loss through a gapped stop. Win rates are assumptions, never measured edge.

    python3 -m feed_pipeline.risksim --out feed/risk-sim.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, dataclass

SCHEMA = "risk-sim/1"


@dataclass(frozen=True)
class Rules:
    size: float  # fraction of equity committed per trade (premium for options)
    stop: float  # loss as a fraction of the position when the stop fills normally
    target: float  # gain as a fraction of the position at the target
    trades_per_day: int
    stop_after_first_loss: bool
    brake: float | None  # stop trading when equity < peak * (1 - brake); None disables it
    gap_prob: float = 0.0  # probability a losing trade gaps through the stop
    gap_loss: float = 1.0  # loss fraction of the position on a gap (1.0 = the whole option premium)
    cost: float = 0.0  # round-trip cost as a fraction of the position, paid on every trade

    def check(self):
        for name in ("size", "stop", "target", "gap_prob", "gap_loss", "cost"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ValueError(f"{name} must be a finite number of at least 0")
        if not 0 < self.size <= 1 or not 0 < self.stop <= 1 or not 0 < self.gap_loss <= 1 or self.gap_prob > 1:
            raise ValueError("size, stop and gap_loss must be in (0, 1]; gap_prob in [0, 1]")
        if not isinstance(self.trades_per_day, int) or self.trades_per_day < 1:
            raise ValueError("trades_per_day must be a positive integer")
        if self.brake is not None and not (isinstance(self.brake, (int, float)) and 0 < self.brake < 1):
            raise ValueError("brake must be in (0, 1) or None")


def kelly_fraction(win_prob: float, target: float, stop: float, cost: float = 0.0) -> float:
    """Full-Kelly fraction of equity for a bet winning `target - cost` or losing `stop + cost` per unit staked."""
    b, a = target - cost, stop + cost
    if b <= 0:
        return 0.0
    return max(0.0, win_prob / a - (1 - win_prob) / b)


def breakeven_win_prob(rules: Rules) -> float:
    """Win probability at which the expected return per trade is zero, after costs and gaps."""
    win = rules.target - rules.cost
    avg_loss = (1 - rules.gap_prob) * (rules.stop + rules.cost) + rules.gap_prob * (rules.gap_loss + rules.cost)
    return avg_loss / (win + avg_loss) if win > 0 else 1.0


def _percentile(sorted_values, q):
    if not sorted_values:
        return None
    i = (len(sorted_values) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (i - lo)


def simulate(rules: Rules, win_prob: float, days: int = 252, paths: int = 4000, seed: int = 0, ruin_level: float = 0.5,
             kelly_multiple: float = 0.5) -> dict:
    rules.check()
    if not 0 <= win_prob <= 1 or days < 1 or paths < 2 or not 0 < ruin_level < 1:
        raise ValueError("need 0 <= win_prob <= 1, days >= 1, paths >= 2 and 0 < ruin_level < 1")
    rng = random.Random(seed)
    win = rules.size * (rules.target - rules.cost)
    loss = rules.size * (rules.stop + rules.cost)
    gap = rules.size * (rules.gap_loss + rules.cost)
    brake_day1 = brake_any = ruined = 0
    max_dds, terminals, trades_taken = [], [], 0
    for _ in range(paths):
        equity = peak = 1.0
        max_dd, braked, hit_ruin = 0.0, False, False
        for day in range(days):
            if braked:
                break
            for _ in range(rules.trades_per_day):
                trades_taken += 1
                if rng.random() < win_prob:
                    equity *= 1 + win
                    peak = max(peak, equity)
                    continue
                equity *= 1 - (gap if rng.random() < rules.gap_prob else loss)
                max_dd = max(max_dd, 1 - equity / peak)
                if equity <= ruin_level:
                    hit_ruin = True
                if rules.brake is not None and equity < peak * (1 - rules.brake):
                    braked = True
                    if day == 0:
                        brake_day1 += 1
                    break
                if rules.stop_after_first_loss:
                    break
        brake_any += braked
        ruined += hit_ruin
        max_dds.append(max_dd)
        terminals.append(equity)
    max_dds.sort()
    terminals.sort()
    full = kelly_fraction(win_prob, rules.target, rules.stop, rules.cost)
    expected_trade = win_prob * win - (1 - win_prob) * ((1 - rules.gap_prob) * loss + rules.gap_prob * gap)
    return {
        "win_prob_assumed": win_prob, "days": days, "paths": paths, "seed": seed,
        "p_brake_first_day": brake_day1 / paths, "p_brake_within_horizon": brake_any / paths,
        "ruin_level": ruin_level, "p_ruin_within_horizon": ruined / paths,
        "max_drawdown_pct": {q: round(100 * _percentile(max_dds, p), 2) for q, p in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))},
        "terminal_equity": {q: round(_percentile(terminals, p), 4) for q, p in (("p05", 0.05), ("p50", 0.5), ("p95", 0.95))},
        "expected_return_per_trade_pct": round(100 * expected_trade, 4),
        "breakeven_win_prob": round(breakeven_win_prob(rules), 4),
        "kelly_full": round(full, 4), "kelly_cap": round(kelly_multiple * full, 4), "kelly_multiple": kelly_multiple,
        "size_exceeds_kelly_cap": rules.size > kelly_multiple * full,
        "worst_single_trade_loss_pct": round(100 * gap if rules.gap_prob > 0 else 100 * loss, 2),
        "worst_day_loss_pct": round(100 * (1 - (1 - max(gap if rules.gap_prob > 0 else loss, loss)) ** (1 if rules.stop_after_first_loss else rules.trades_per_day)), 2),
    }


# The documented desk rules as percentages (public: no account values). Win rates are an assumption grid, not edge.
DOCUMENTED = {
    "size_score_8": Rules(size=0.10, stop=0.15, target=0.30, trades_per_day=2, stop_after_first_loss=True, brake=0.15, gap_prob=0.05, gap_loss=1.0, cost=0.02),
    "size_score_9_10": Rules(size=0.15, stop=0.15, target=0.30, trades_per_day=2, stop_after_first_loss=True, brake=0.15, gap_prob=0.05, gap_loss=1.0, cost=0.02),
}
WIN_PROBS = (0.30, 0.35, 0.40)


def report(paths: int = 3000, days: int = 252, seed: int = 20261024) -> dict:
    out = {"schema": SCHEMA, "note": "Percent rules and assumed win rates only. Model output, not a forecast or a measured edge.",
           "assumptions": {"gap_prob": "5% of losing trades gap through the stop", "gap_loss": "a gap loses the whole premium",
                           "cost": "2% of the position round trip", "brake": "trading stops for the horizon once equity is 15% below its peak",
                           "independence": "trades are independent with a fixed win probability"},
           "rule_sets": {}}
    for name, rules in DOCUMENTED.items():
        out["rule_sets"][name] = {"rules": asdict(rules), "results": [simulate(rules, p, days, paths, seed) for p in WIN_PROBS]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out")
    ap.add_argument("--paths", type=int, default=3000)
    args = ap.parse_args(argv)
    text = json.dumps(report(args.paths), sort_keys=True, separators=(",", ":"))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
