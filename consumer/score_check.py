"""Entry-score check: does a higher desk score lead to better trades? Standard library only, read-only, no network.

Reads the same fills CSV as paper_ledger.py, with an extra `score` column on entry rows. For one mode at a time
(PAPER or REPLAY, never pooled) it pairs each closed, valid, fully-costed trade with its entry score and reports:
- per score bucket: trades, distinct entry sessions, win rate, mean return % and mean net USD, with a 95% interval for
  the mean return from a session-cluster bootstrap (whole entry days are resampled, because trades entered on the
  same day move together and are not independent evidence);
- the Spearman rank correlation between score and return, with the same bootstrap interval;
- the gap between the top bucket and the one below it;
- the correlation in the earlier and later halves of the sessions, to see whether the direction holds over time.

Verdict: insufficient_evidence (too few trades or sessions), ranks (interval above zero), inverted (interval below
zero) or no_rank_evidence. A verdict describes this ledger only; it is not a trading edge and changes no rule.

    python3 -I consumer/score_check.py fills.csv [--mode REPLAY] [--buckets 6,7,8,9-10] [--min-trades 30]
        [--min-sessions 10] [--resamples 2000] [--seed 7]
"""
import argparse
import json
import math
import random
import sys
from collections import defaultdict

from consumer.paper_ledger import PT, _lifecycle, load

EPS = 1e-9


def parse_buckets(text):
    """'6,7,8,9-10' -> [(6, 6, '6'), (7, 7, '7'), (8, 8, '8'), (9, 10, '9-10')], ascending and non-overlapping."""
    out = []
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        lo, hi = int(lo), int(hi or lo)
        if hi < lo or (out and lo <= out[-1][1]):
            raise ValueError(f"bucket {part.strip()!r} is empty, out of order or overlapping")
        out.append((lo, hi, part.strip()))
    return out


def trades(rows, mode):
    """Closed, valid trades with known fees and one consistent entry score, plus the reasons others were left out."""
    groups, skipped = defaultdict(list), defaultdict(list)
    for r in rows:
        if r["mode"] == mode:
            groups[(r["arm"], r["trade_id"])].append(r)
    out = []
    for (arm, tid), rs in groups.items():
        entries, exits = [r for r in rs if r["kind"] == "entry"], [r for r in rs if r["kind"] == "exit"]
        if _lifecycle({"rows": rs}):
            skipped["rejected"].append(tid)
            continue
        if not exits or abs(sum(r["qty"] for r in entries) - sum(r["qty"] for r in exits)) > EPS:
            skipped["open"].append(tid)
            continue
        fees = [r["fee"] for r in entries + exits]
        if any(f is None for f in fees):
            skipped["fees_unknown"].append(tid)
            continue
        scores = {(r.get("score") or "").strip() for r in entries}
        try:
            (score,) = {float(s) for s in scores}
        except ValueError:  # blank, non-numeric or several different scores on one trade
            skipped["score_missing_or_mixed"].append(tid)
            continue
        cost = sum(r["qty"] * r["price"] * r["mult"] for r in entries)
        net = sum(r["qty"] * r["price"] * r["mult"] for r in exits) - cost - sum(fees)
        opened = min(r["at"] for r in entries)
        out.append({"arm": arm, "trade_id": tid, "score": score, "net": net, "ret": net / cost * 100,
                    "session": opened.astimezone(PT).date().isoformat()})
    return sorted(out, key=lambda t: (t["session"], t["trade_id"])), {k: sorted(v) for k, v in skipped.items()}


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks, i = [0.0] * len(xs), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):  # ties share their average rank
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def spearman(ts):
    if len(ts) < 3:
        return None
    a, b = _ranks([t["score"] for t in ts]), _ranks([t["ret"] for t in ts])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    var = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return None if var == 0 else cov / var


def _mean_ret(ts):
    return sum(t["ret"] for t in ts) / len(ts) if ts else None


def cluster_ci(ts, stat, resamples, rng):
    """95% percentile interval of stat over bootstrap samples that redraw whole entry sessions with replacement."""
    by_session = defaultdict(list)
    for t in ts:
        by_session[t["session"]].append(t)
    days = sorted(by_session)
    if len(days) < 2:
        return None
    values = []
    for _ in range(resamples):
        sample = [t for _d in days for t in by_session[rng.choice(days)]]
        v = stat(sample)
        if v is not None:
            values.append(v)
    if len(values) < resamples // 2:
        return None
    values.sort()
    return [round(values[int(0.025 * (len(values) - 1))], 4), round(values[int(0.975 * (len(values) - 1))], 4)]


def analyze(rows, mode="REPLAY", buckets="6,7,8,9-10", min_trades=30, min_sessions=10, resamples=2000, seed=7):
    spec = parse_buckets(buckets)
    ts, skipped = trades(rows, mode)
    rng = random.Random(seed)
    r4 = lambda x: None if x is None else round(x, 4)  # noqa: E731
    table, members = [], {}
    for lo, hi, label in spec:
        b = [t for t in ts if lo - EPS <= t["score"] <= hi + EPS]
        members[label] = b
        table.append({"bucket": label, "trades": len(b), "sessions": len({t["session"] for t in b}),
                      "win_rate": r4(sum(t["net"] > 0 for t in b) / len(b)) if b else None,
                      "mean_return_pct": r4(_mean_ret(b)), "mean_net_usd": r4(sum(t["net"] for t in b) / len(b)) if b else None,
                      "mean_return_ci95": cluster_ci(b, _mean_ret, resamples, rng)})
    scored = [t for t in ts if any(lo - EPS <= t["score"] <= hi + EPS for lo, hi, _l in spec)]
    sessions = sorted({t["session"] for t in scored})
    rho_ci = cluster_ci(scored, spearman, resamples, rng)
    top, below = members[spec[-1][2]], members[spec[-2][2]] if len(spec) > 1 else []
    gap = lambda s: (None if not [t for t in s if t in top] or not [t for t in s if t in below]  # noqa: E731
                     else _mean_ret([t for t in s if t in top]) - _mean_ret([t for t in s if t in below]))
    half = len(sessions) // 2
    early, late = set(sessions[:half]), set(sessions[half:])
    if len(scored) < min_trades or len(sessions) < min_sessions:
        verdict = "insufficient_evidence"
    elif rho_ci and rho_ci[0] > 0:
        verdict = "ranks"
    elif rho_ci and rho_ci[1] < 0:
        verdict = "inverted"
    else:
        verdict = "no_rank_evidence"
    return {
        "mode": mode, "trades": len(scored), "sessions": len(sessions), "buckets": table,
        "spearman": r4(spearman(scored)), "spearman_ci95": rho_ci,
        "top_minus_next_return_pct": r4(gap(scored)), "top_minus_next_ci95": cluster_ci(scored, gap, resamples, rng),
        "spearman_early_half": r4(spearman([t for t in scored if t["session"] in early])),
        "spearman_late_half": r4(spearman([t for t in scored if t["session"] in late])),
        "verdict": verdict, "thresholds": {"min_trades": min_trades, "min_sessions": min_sessions},
        "skipped": skipped, "unbucketed_scores": sorted({t["score"] for t in ts} - {t["score"] for t in scored}),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--mode", choices=("PAPER", "REPLAY"), default="REPLAY")
    ap.add_argument("--buckets", default="6,7,8,9-10")
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--min-sessions", type=int, default=10)
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    try:
        parse_buckets(a.buckets)
    except ValueError as exc:
        print(json.dumps({"errors": [f"settings: {exc}"]}))
        return 2
    rows, errors = load(a.path)
    out = analyze(rows, a.mode, a.buckets, a.min_trades, a.min_sessions, a.resamples, a.seed)
    out["errors"] = errors
    out["note"] = ("Returns are net of fees on closed trades only. Intervals resample whole entry sessions, so correlated "
                   "same-day trades count once per day. A verdict describes this ledger, not a future edge.")
    print(json.dumps(out, indent=1))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
