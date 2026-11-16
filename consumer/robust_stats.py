"""Robust performance statistics for judging backtests honestly. Standard library only. TEST ONLY.

Implements published methods, written from the papers (no code copied):
  * Probabilistic Sharpe Ratio (PSR) and Deflated Sharpe Ratio (DSR): Bailey and Lopez de Prado, "The Sharpe Ratio Efficient Frontier"
    (2012) and "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality" (2014).
  * Minimum Track Record Length (same 2012 paper).
  * Probability of Backtest Overfitting by combinatorially symmetric cross-validation (CSCV): Bailey, Borwein, Lopez de Prado and Zhu,
    "The Probability of Backtest Overfitting" (2015).
  * Stationary bootstrap: Politis and Romano (1994).
All Sharpe ratios here are per period unless a function says annualized. Inputs are lists of period returns (for example daily). A single PBO from one dataset is noisy (pure noise can read 0.1 to 0.8 and averages 0.5), so read it with that in mind.
"""
from __future__ import annotations

import itertools
import math
import random
from statistics import NormalDist

N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def moments(xs):
    n = len(xs)
    if n < 4:
        raise ValueError("need at least 4 returns")
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        raise ValueError("returns have zero variance")
    m3 = sum((x - m) ** 3 for x in xs) / n
    m4 = sum((x - m) ** 4 for x in xs) / n
    pop_sd = math.sqrt(sum((x - m) ** 2 for x in xs) / n)
    return {"n": n, "mean": m, "sd": sd, "skew": m3 / pop_sd ** 3, "kurt": m4 / pop_sd ** 4}


def sharpe(xs, periods_per_year=1):
    mo = moments(xs)
    return mo["mean"] / mo["sd"] * math.sqrt(periods_per_year)


def psr(xs, benchmark=0.0):
    """P(true Sharpe > benchmark), per-period Sharpe, adjusted for skew and kurtosis (raw kurtosis, 3 for a normal)."""
    mo = moments(xs)
    sr = mo["mean"] / mo["sd"]
    denom = 1 - mo["skew"] * sr + (mo["kurt"] - 1) / 4 * sr ** 2
    if denom <= 0:
        return float("nan")
    return N.cdf((sr - benchmark) * math.sqrt(mo["n"] - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials, sharpe_variance):
    """Expected maximum per-period Sharpe among n_trials independent zero-skill trials with the given variance of Sharpe estimates."""
    if n_trials < 2:
        return 0.0
    return math.sqrt(sharpe_variance) * ((1 - EULER_GAMMA) * N.inv_cdf(1 - 1 / n_trials) + EULER_GAMMA * N.inv_cdf(1 - 1 / (n_trials * math.e)))


def dsr(xs, n_trials, trial_sharpe_variance=None):
    """Deflated Sharpe Ratio: PSR against the Sharpe the best of n_trials would show by luck alone.

    trial_sharpe_variance defaults to the large-sample variance of one Sharpe estimate, 1/(T-1), when the other trials' Sharpes are unknown."""
    mo = moments(xs)
    var = trial_sharpe_variance if trial_sharpe_variance is not None else 1.0 / (mo["n"] - 1)
    return psr(xs, expected_max_sharpe(n_trials, var))


def min_track_record(xs, benchmark=0.0, confidence=0.95):
    """Periods needed before the observed Sharpe beats the benchmark at the given confidence, with this return distribution."""
    mo = moments(xs)
    sr = mo["mean"] / mo["sd"]
    if sr <= benchmark:
        return float("inf")
    z = N.inv_cdf(confidence)
    return 1 + (1 - mo["skew"] * sr + (mo["kurt"] - 1) / 4 * sr ** 2) * (z / (sr - benchmark)) ** 2


def pbo_cscv(matrix, n_blocks=16, max_combinations=None, seed=7):
    """Probability of Backtest Overfitting. matrix: list of equal-length return series, one per strategy (rows = strategies).

    The time axis is cut into n_blocks equal blocks. For every way of choosing half of them as in-sample, the strategy with the best in-sample
    Sharpe is ranked on the other half; PBO is the share of splits where that strategy ends at or below the out-of-sample median."""
    n_strat, T = len(matrix), len(matrix[0])
    if n_strat < 2 or n_blocks % 2 or T < n_blocks * 2:
        raise ValueError("need at least 2 strategies, an even number of blocks and 2 observations per block")
    size = T // n_blocks
    stats = []  # per strategy, per block: (n, sum, sumsq)
    for row in matrix:
        bl = []
        for b in range(n_blocks):
            seg = row[b * size:(b + 1) * size]
            bl.append((len(seg), sum(seg), sum(x * x for x in seg)))
        stats.append(bl)
    combos = list(itertools.combinations(range(n_blocks), n_blocks // 2))
    if max_combinations and len(combos) > max_combinations:
        combos = random.Random(seed).sample(combos, max_combinations)
    def sr(bl, idx):
        n = sum(bl[i][0] for i in idx); s = sum(bl[i][1] for i in idx); ss = sum(bl[i][2] for i in idx)
        m = s / n
        var = (ss - n * m * m) / (n - 1)
        return m / math.sqrt(var) if var > 0 else 0.0
    logits = []
    for ins in combos:
        outs = tuple(i for i in range(n_blocks) if i not in ins)
        sr_in = [sr(stats[k], ins) for k in range(n_strat)]
        best = max(range(n_strat), key=lambda k: (sr_in[k], -k))
        sr_out = [sr(stats[k], outs) for k in range(n_strat)]
        rank = sum(1 for v in sr_out if v < sr_out[best]) + 1  # 1 = worst out of sample
        w = rank / (n_strat + 1)
        logits.append(math.log(w / (1 - w)))
    return {"pbo": sum(1 for l in logits if l <= 0) / len(logits), "splits": len(logits), "median_logit": sorted(logits)[len(logits) // 2],
            "strategies": n_strat, "blocks": n_blocks}


def stationary_bootstrap_sharpe(xs, mean_block=10, resamples=2000, seed=7, periods_per_year=1):
    """Politis-Romano stationary bootstrap interval for the Sharpe ratio (blocks of geometric random length, wrapping around the sample)."""
    n = len(xs)
    rnd, p = random.Random(seed), 1.0 / mean_block
    vals = []
    for _ in range(resamples):
        out, i = [], rnd.randrange(n)
        while len(out) < n:
            out.append(xs[i])
            i = rnd.randrange(n) if rnd.random() < p else (i + 1) % n
        m = sum(out) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in out) / (n - 1))
        vals.append(m / sd * math.sqrt(periods_per_year) if sd > 0 else 0.0)
    vals.sort()
    return {"sharpe": sharpe(xs, periods_per_year), "lo": vals[int(0.025 * resamples)], "hi": vals[int(0.975 * resamples) - 1],
            "p_le_zero": sum(1 for v in vals if v <= 0) / resamples}
