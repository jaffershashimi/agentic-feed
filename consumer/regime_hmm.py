"""Causal two-state Gaussian hidden Markov regime detector (calm vs stressed), stdlib only.

Why: the S-042 pullback edge lives in market stress, and the stress flag it uses is a simple realised-vol rule. A hidden Markov model gives a
probability of being in the stressed regime instead of a hard threshold. The common mistake with HMMs is lookahead: smoothed or Viterbi
states use the future. This module only ever outputs FILTERED probabilities (forward pass, information up to day t) and refits on an
expanding window that ends before the day it is used on.

Research only. Output is a diagnostic, not a trading rule, until Jaffer approves a rule change.
References: Hamilton (1989) regime switching; Rabiner (1989) HMM tutorial; Nystrup et al. on online/filtered regime inference.
"""
import math
import random

LOG2PI = math.log(2.0 * math.pi)


def _logpdf(x, mu, var):
    return -0.5 * (LOG2PI + math.log(var) + (x - mu) ** 2 / var)


def _logsumexp(a, b):
    m = max(a, b)
    return m + math.log(math.exp(a - m) + math.exp(b - m)) if m > -1e300 else m


def init_params(xs):
    """State 0 = calm (low variance), state 1 = stressed. Start from the median absolute split."""
    n = len(xs)
    mean = sum(xs) / n
    ab = sorted(abs(x - mean) for x in xs)
    cut = ab[n // 2]
    lo = [x for x in xs if abs(x - mean) <= cut] or xs
    hi = [x for x in xs if abs(x - mean) > cut] or xs
    v = lambda ys: max(sum((y - sum(ys) / len(ys)) ** 2 for y in ys) / len(ys), 1e-10)
    return {"mu": [sum(lo) / len(lo), sum(hi) / len(hi)], "var": [v(lo), v(hi)], "A": [[0.97, 0.03], [0.06, 0.94]], "pi": [0.9, 0.1]}


def fit(xs, n_iter=40):
    """Baum-Welch EM. Returns params with state 0 the lower-variance state."""
    if len(xs) < 60:
        raise ValueError("need at least 60 observations")
    p = init_params(xs)
    n = len(xs)
    for _ in range(n_iter):
        lb = [[_logpdf(x, p["mu"][k], p["var"][k]) for k in (0, 1)] for x in xs]
        la = [[math.log(max(p["A"][i][j], 1e-12)) for j in (0, 1)] for i in (0, 1)]
        alpha = [[math.log(max(p["pi"][k], 1e-12)) + lb[0][k] for k in (0, 1)]]
        for t in range(1, n):
            alpha.append([_logsumexp(alpha[-1][0] + la[0][j], alpha[-1][1] + la[1][j]) + lb[t][j] for j in (0, 1)])
        beta = [[0.0, 0.0] for _ in range(n)]
        for t in range(n - 2, -1, -1):
            beta[t] = [_logsumexp(la[i][0] + lb[t + 1][0] + beta[t + 1][0], la[i][1] + lb[t + 1][1] + beta[t + 1][1]) for i in (0, 1)]
        ll = _logsumexp(alpha[-1][0], alpha[-1][1])
        gamma = []
        for t in range(n):
            g = [alpha[t][k] + beta[t][k] - ll for k in (0, 1)]
            gamma.append([math.exp(g[0]), math.exp(g[1])])
        xi = [[0.0, 0.0], [0.0, 0.0]]
        for t in range(n - 1):
            for i in (0, 1):
                for j in (0, 1):
                    xi[i][j] += math.exp(alpha[t][i] + la[i][j] + lb[t + 1][j] + beta[t + 1][j] - ll)
        w = [sum(g[k] for g in gamma) for k in (0, 1)]
        mu = [sum(g[k] * x for g, x in zip(gamma, xs)) / max(w[k], 1e-12) for k in (0, 1)]
        var = [max(sum(g[k] * (x - mu[k]) ** 2 for g, x in zip(gamma, xs)) / max(w[k], 1e-12), 1e-10) for k in (0, 1)]
        A = [[xi[i][j] / max(xi[i][0] + xi[i][1], 1e-12) for j in (0, 1)] for i in (0, 1)]
        p = {"mu": mu, "var": var, "A": A, "pi": gamma[0]}
    if p["var"][0] > p["var"][1]:
        p = {"mu": p["mu"][::-1], "var": p["var"][::-1], "A": [[p["A"][1][1], p["A"][1][0]], [p["A"][0][1], p["A"][0][0]]], "pi": p["pi"][::-1]}
    return p


def filter_probs(xs, p):
    """P(stressed | returns up to t) for every t. Uses only the past and present of xs."""
    out = []
    prev = None
    for x in xs:
        if prev is None:
            pri = list(p["pi"])
        else:
            pri = [prev[0] * p["A"][0][j] + prev[1] * p["A"][1][j] for j in (0, 1)]
        like = [math.exp(_logpdf(x, p["mu"][k], p["var"][k])) for k in (0, 1)]
        post = [pri[k] * like[k] for k in (0, 1)]
        z = post[0] + post[1]
        post = [post[0] / z, post[1] / z] if z > 0 else pri
        out.append(post[1])
        prev = post
    return out


def rolling_stress_probability(xs, train=756, refit_every=63, n_iter=30):
    """Walk-forward: refit every `refit_every` days on all data strictly before that block, filter the block with those parameters.
    Returns a list the same length as xs; entries before the first fit are None. Never uses a return from the day it scores or later
    to estimate the parameters used on that day."""
    out = [None] * len(xs)
    t = train
    while t < len(xs):
        p = fit(xs[:t], n_iter)
        end = min(t + refit_every, len(xs))
        # filter from the start of history so the state carries over, but score only the new block
        probs = filter_probs(xs[:end], p)
        out[t:end] = probs[t:end]
        t = end
    return out


def synthetic(n=1500, seed=5, calm=(0.0005, 0.007), stress=(-0.001, 0.025), stay=(0.98, 0.93)):
    """Two-regime generator for tests. Returns (returns, true_state)."""
    rnd = random.Random(seed)
    s, xs, st = 0, [], []
    for _ in range(n):
        if rnd.random() > stay[s]:
            s = 1 - s
        mu, sd = (calm, stress)[s]
        xs.append(rnd.gauss(mu, sd))
        st.append(s)
    return xs, st
