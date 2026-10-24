"""Option math and Monte Carlo EV of a fixed stop/target premium structure (issue P2-2). Standard library only.

Black-Scholes-Merton prices and greeks (continuous dividend yield), an implied-vol solver (Newton with bisection
fallback), the reflection-principle probability of touching a level, and a seeded Monte Carlo of a premium
structure ("sell at +target% or stop at -stop%, else exit at the time limit") under GBM or Merton jump-diffusion.

Model assumptions are explicit in every result: lognormal underlying, constant volatility and rates, European
pricing used for repricing, exits at the bid (mid minus half the quoted spread), levels checked on the simulation
grid only (a level crossed between steps fills at the next step's price, never at the level). A model probability is
not a market probability and nothing here is a trading signal; see docs/SIGNAL_SCORECARD.md.

    python3 -m feed_pipeline.optionmath ev --spot 100 --strike 102 --days 21 --iv 0.35 --kind call \
        --target 0.30 --stop 0.15 --hold-days 10 --spread-pct 0.04 --threshold-pct 3
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, dataclass

YEAR_DAYS = 252.0


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _check(spot: float, strike: float, t: float, sigma: float) -> None:
    for name, value in (("spot", spot), ("strike", strike), ("t", t), ("sigma", sigma)):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite number above 0")


def bsm(spot: float, strike: float, t: float, sigma: float, kind: str = "call", r: float = 0.0, q: float = 0.0) -> dict:
    """Price and greeks. theta is per calendar year (divide by 365 for per day); vega per 1.00 of volatility."""
    _check(spot, strike, t, sigma)
    if kind not in ("call", "put"):
        raise ValueError("kind must be call or put")
    sq = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / sq
    d2 = d1 - sq
    dq, dr = math.exp(-q * t), math.exp(-r * t)
    if kind == "call":
        price = spot * dq * norm_cdf(d1) - strike * dr * norm_cdf(d2)
        delta = dq * norm_cdf(d1)
        theta = -spot * dq * norm_pdf(d1) * sigma / (2 * math.sqrt(t)) - r * strike * dr * norm_cdf(d2) + q * spot * dq * norm_cdf(d1)
    else:
        price = strike * dr * norm_cdf(-d2) - spot * dq * norm_cdf(-d1)
        delta = -dq * norm_cdf(-d1)
        theta = -spot * dq * norm_pdf(d1) * sigma / (2 * math.sqrt(t)) + r * strike * dr * norm_cdf(-d2) - q * spot * dq * norm_cdf(-d1)
    gamma = dq * norm_pdf(d1) / (spot * sq)
    vega = spot * dq * norm_pdf(d1) * math.sqrt(t)
    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def implied_vol(price: float, spot: float, strike: float, t: float, kind: str = "call", r: float = 0.0, q: float = 0.0,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-8, tick: float = 0.01) -> float | None:
    """Volatility that reproduces `price`, or None when no volatility in [lo, hi] can (e.g. below intrinsic value) or
    when the price barely depends on volatility: if moving volatility 10% either way changes the price by less than half
    a quote tick (e.g. deep in the money at low vol), many volatilities fit the same quoted price and any single answer
    would be invented."""
    _check(spot, strike, t, 1.0)
    if not math.isfinite(price) or price <= 0:
        return None
    f_lo, f_hi = bsm(spot, strike, t, lo, kind, r, q)["price"] - price, bsm(spot, strike, t, hi, kind, r, q)["price"] - price
    if f_lo > 0 or f_hi < 0:
        return None
    def identifiable(s: float) -> bool:
        band = bsm(spot, strike, t, s * 1.1, kind, r, q)["price"] - bsm(spot, strike, t, s * 0.9, kind, r, q)["price"]
        return band >= tick / 2

    sigma = 0.3
    for _ in range(50):  # Newton, falling back to bisection whenever a step leaves the bracket or vega vanishes
        g = bsm(spot, strike, t, sigma, kind, r, q)
        diff = g["price"] - price
        if abs(diff) < tol:
            return sigma if identifiable(sigma) else None
        if diff > 0:
            hi = sigma
        else:
            lo = sigma
        step = sigma - diff / g["vega"] if g["vega"] > 1e-12 else None
        sigma = step if step is not None and lo < step < hi else 0.5 * (lo + hi)
    return sigma if hi - lo < 1e-6 and identifiable(sigma) else None


def touch_probability(spot: float, level: float, t: float, sigma: float, mu: float = 0.0) -> float:
    """P(the underlying touches `level` before t) under GBM with drift mu, by the reflection principle."""
    _check(spot, level, t, sigma)
    if level == spot:
        return 1.0
    nu = mu - 0.5 * sigma * sigma
    b = math.log(level / spot)
    sq = sigma * math.sqrt(t)
    if b > 0:
        return norm_cdf((-b + nu * t) / sq) + math.exp(2 * nu * b / (sigma * sigma)) * norm_cdf((-b - nu * t) / sq)
    return norm_cdf((b - nu * t) / sq) + math.exp(2 * nu * b / (sigma * sigma)) * norm_cdf((b + nu * t) / sq)


@dataclass(frozen=True)
class StructureResult:
    paths: int
    p_target_first: float
    p_stop_first: float
    p_time_exit: float
    ev_pct: float
    ev_threshold_pct: float | None
    passes_threshold: bool | None
    standard_error_pct: float
    model: str
    assumptions: list


def _outcome(premiums, entry: float, target: float, stop: float, spread_pct: float):
    """First exit on the grid. Returns (kind, net return on the entry price after crossing half the spread)."""
    for p in premiums:
        bid = p * (1 - spread_pct / 2)
        if bid >= entry * (1 + target):
            return "target", bid / entry - 1
        if bid <= entry * (1 - stop):
            return "stop", bid / entry - 1
    return "time", premiums[-1] * (1 - spread_pct / 2) / entry - 1


def _summarize(kinds, returns, model, assumptions, threshold):
    n = len(returns)
    mean = sum(returns) / n
    var = sum((x - mean) ** 2 for x in returns) / (n - 1) if n > 1 else 0.0
    ev = 100 * mean
    return StructureResult(
        paths=n, p_target_first=kinds.count("target") / n, p_stop_first=kinds.count("stop") / n,
        p_time_exit=kinds.count("time") / n, ev_pct=ev, ev_threshold_pct=threshold,
        passes_threshold=None if threshold is None else ev >= threshold, standard_error_pct=100 * math.sqrt(var / n),
        model=model, assumptions=assumptions)


def premium_martingale(target: float, stop: float, premium_vol: float, steps: int = 400, paths: int = 20000,
                       seed: int = 0, drift: float = 0.0, threshold_pct: float | None = None) -> StructureResult:
    """The premium itself as a GBM (drift 0 = martingale). For a martingale the target-first probability is
    stop / (target + stop) in the continuous limit: one third for +30% / -15%."""
    rng = random.Random(seed)
    dt = 1.0 / steps
    kinds, rets = [], []
    for _ in range(paths):
        p, path = 1.0, []
        for _ in range(steps):
            p *= math.exp((drift - 0.5 * premium_vol ** 2) * dt + premium_vol * math.sqrt(dt) * rng.gauss(0, 1))
            path.append(p)
        k, r = _outcome(path, 1.0, target, stop, 0.0)
        kinds.append(k)
        rets.append(r)
    return _summarize(kinds, rets, "premium GBM", [f"premium drift {drift}", f"premium vol {premium_vol}", f"{steps} grid steps", "no spread"], threshold_pct)


def option_structure(spot: float, strike: float, days: float, iv: float, kind: str = "call", target: float = 0.30,
                     stop: float = 0.15, hold_days: float | None = None, spread_pct: float = 0.0, r: float = 0.0,
                     q: float = 0.0, mu: float = 0.0, steps_per_day: int = 13, paths: int = 5000, seed: int = 0,
                     jump_intensity: float = 0.0, jump_mean: float = 0.0, jump_vol: float = 0.0,
                     threshold_pct: float | None = None) -> StructureResult:
    """Simulate the underlying (GBM, plus Merton jumps if jump_intensity > 0, per year), reprice the option with BSM at
    the same implied vol as time decays (so theta is included), and apply the stop/target to the bid premium.
    Entry pays the ask (mid plus half the spread)."""
    _check(spot, strike, days, iv)
    hold = days if hold_days is None else min(hold_days, days)
    if not 0 < hold <= days or not 0 <= spread_pct < 1 or target <= 0 or not 0 < stop < 1:
        raise ValueError("need 0 < hold_days <= days, 0 <= spread_pct < 1, target > 0 and 0 < stop < 1")
    rng = random.Random(seed)
    n = max(1, int(round(hold * steps_per_day)))
    dt = hold / YEAR_DAYS / n
    t0 = days / YEAR_DAYS
    mid0 = bsm(spot, strike, t0, iv, kind, r, q)["price"]
    entry = mid0 * (1 + spread_pct / 2)
    k = math.exp(jump_mean + 0.5 * jump_vol ** 2) - 1  # jump compensator keeps the drift at mu
    kinds, rets = [], []
    for _ in range(paths):
        s, prem = spot, []
        for i in range(1, n + 1):
            s *= math.exp((mu - 0.5 * iv ** 2 - jump_intensity * k) * dt + iv * math.sqrt(dt) * rng.gauss(0, 1))
            if jump_intensity > 0 and rng.random() < jump_intensity * dt:
                s *= math.exp(rng.gauss(jump_mean, jump_vol))
            prem.append(bsm(s, strike, max(t0 - i * dt, 1e-9), iv, kind, r, q)["price"])
        kd, ret = _outcome(prem, entry, target, stop, spread_pct)
        kinds.append(kd)
        rets.append(ret)
    model = "GBM" if jump_intensity == 0 else "Merton jump-diffusion"
    assumptions = [f"underlying drift {mu}/yr", f"constant IV {iv}", f"r {r}, q {q}", "European BSM repricing",
                   f"entry at ask, exits at bid (spread {spread_pct:.2%} of mid)", f"{steps_per_day} checks per day; gaps fill at the next check",
                   f"time exit after {hold} trading days"]
    if jump_intensity:
        assumptions.append(f"jumps {jump_intensity}/yr, log-size N({jump_mean}, {jump_vol})")
    return _summarize(kinds, rets, model, assumptions, threshold_pct)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("ev", help="Monte Carlo EV of a stop/target structure on one option")
    for name, kind in (("spot", float), ("strike", float), ("days", float), ("iv", float)):
        ev.add_argument("--" + name, type=kind, required=True)
    ev.add_argument("--kind", choices=["call", "put"], default="call")
    ev.add_argument("--target", type=float, default=0.30)
    ev.add_argument("--stop", type=float, default=0.15)
    ev.add_argument("--hold-days", type=float)
    ev.add_argument("--spread-pct", type=float, default=0.0)
    ev.add_argument("--paths", type=int, default=5000)
    ev.add_argument("--seed", type=int, default=0)
    ev.add_argument("--threshold-pct", type=float)
    args = ap.parse_args(argv)
    result = option_structure(args.spot, args.strike, args.days, args.iv, args.kind, args.target, args.stop, args.hold_days,
                              args.spread_pct, paths=args.paths, seed=args.seed, threshold_pct=args.threshold_pct)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
