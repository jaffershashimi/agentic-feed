"""Paired rule-set and tag comparison: does a rule change or a tag really change outcomes? Standard library only,
read-only, no network.

Reads the same fills CSV as paper_ledger.py (one mode at a time, never pooled) and answers two questions.

1. Paired rules (for example R2 vs R3). A setup is one decision: arm side (A or B), symbol and entry time. Each
   setup that either rule set traded is compared with itself: test net minus base net, where a rule set that did not
   trade the setup scores 0 (cash) and an open, unresolved, rejected or fee-unknown trade makes the pair unknown.
   Only (session, symbol) pairs both rule sets replayed count: pass --sessions replay_sessions.csv (keys
   RULE|YYYY-MM-DD|SYM); without it, sessions where both rule sets have a trade are used. Rule sets are read from
   the arm: REPLAY-A-R2-NVDA is side A, rule set R2; an arm without one (REPLAY-B-NVDA) is R1.
2. Tag splits (optional --tags variant_tags.csv with trade_id,rule_set,binary_event,options_checklist): per rule set
   and instrument, the mean net of closed trades tagged YES minus NO (binary_event) and PASS minus FAIL
   (options_checklist, options only).

Every interval is a 95% session-cluster bootstrap (whole entry sessions are resampled, because same-day trades move
together), and every p-value comes from the same bootstrap. Because many splits are tested at once, p-values are
also Holm-adjusted across every test this run made. Verdicts: insufficient_evidence below --min-pairs (or
--min-trades per group) or --min-sessions, otherwise test_better / base_better / no_difference (paired) and
higher / lower / no_difference (tags). A verdict describes this ledger only and changes no rule.

    python3 -I -m consumer.compare_arms fills.csv [--mode REPLAY] [--base R2] [--test R3] [--sessions FILE]
        [--tags FILE] [--min-pairs 30] [--min-trades 15] [--min-sessions 10] [--resamples 2000] [--seed 7]
        [--alpha 0.05]
"""
import argparse
import csv
import json
import random
import re
import sys
from collections import defaultdict

from consumer.paper_ledger import PT, load
from consumer.score_check import trades

ARM = re.compile(r"^(?:REPLAY|PAPER)-([A-Z])-(?:(R\d+[A-Z]?)-)?(.+)$")
TAGS = {"binary_event": ("YES", "NO"), "options_checklist": ("PASS", "FAIL")}


def parse_arm(arm):
    """'REPLAY-A-R2-NVDA' -> ('A', 'R2', 'NVDA'); 'REPLAY-B-NVDA' -> ('B', 'R1', 'NVDA'); anything else -> None."""
    m = ARM.match(arm)
    return (m.group(1), m.group(2) or "R1", m.group(3)) if m else None


def _r4(x):
    return None if x is None else round(x, 4)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def bootstrap(groups, stat, resamples, rng):
    """95% interval and two-sided p-value of stat over resamples that redraw whole sessions with replacement.

    groups maps session -> items; stat takes a list of items and returns a number or None."""
    days = sorted(groups)
    if len(days) < 2:
        return None, None
    values = []
    for _ in range(resamples):
        v = stat([x for _d in days for x in groups[rng.choice(days)]])
        if v is not None:
            values.append(v)
    if len(values) < resamples // 2:
        return None, None
    values.sort()
    ci = [round(values[int(0.025 * (len(values) - 1))], 4), round(values[int(0.975 * (len(values) - 1))], 4)]
    below, above = sum(v <= 0 for v in values), sum(v >= 0 for v in values)
    return ci, max(1 / len(values), min(1.0, 2 * min(below, above) / len(values)))


def holm(pvalues):
    """Holm step-down adjusted p-values, in the input order (None stays None and is not counted)."""
    known = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    out, running = [None] * len(pvalues), 0.0
    for rank, (p, i) in enumerate(known):
        running = max(running, min(1.0, (len(known) - rank) * p))
        out[i] = round(running, 4)
    return out


def setups(rows, mode):
    """Every trade in mode keyed by setup, with its outcome (net and return, or None when not measurable)."""
    closed, _skipped = trades(rows, mode)
    known = {(t["arm"], t["trade_id"]): t for t in closed}
    first = {}
    for r in rows:
        if r["mode"] == mode and r["kind"] == "entry":
            key = (r["arm"], r["trade_id"])
            if key not in first or r["at"] < first[key]["at"]:
                first[key] = r
    out = []
    for (arm, tid), r in first.items():
        parsed = parse_arm(arm)
        if not parsed:
            continue
        side, rule, sym = parsed
        t = known.get((arm, tid))
        out.append({"rule": rule, "setup": (side, sym, r["at"].astimezone(PT).strftime("%Y-%m-%dT%H:%M")),
                    "session": r["at"].astimezone(PT).date().isoformat(), "symbol": sym, "trade_id": tid,
                    "instrument": r["instrument"], "net": t["net"] if t else None, "ret": t["ret"] if t else None})
    return out


def load_sessions(path):
    keys = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            parts = (row.get("session_key") or "").split("|")
            if len(parts) == 3:
                keys.add(tuple(parts))
    return keys


def paired(items, base, test, covered, min_pairs, min_sessions, resamples, rng):
    by_setup = defaultdict(dict)
    for s in items:
        if s["rule"] in (base, test):
            by_setup[s["setup"]][s["rule"]] = s
    if covered is None:  # sessions where both rule sets traded something
        days = {r: {s["session"] for s in items if s["rule"] == r} for r in (base, test)}
        covered_fn = lambda day, _sym: day in days[base] and day in days[test]  # noqa: E731
    else:
        covered_fn = lambda day, sym: (base, day, sym) in covered and (test, day, sym) in covered  # noqa: E731
    counts = defaultdict(int)
    pairs = []
    for key, sides in sorted(by_setup.items()):
        any_side = next(iter(sides.values()))
        if not covered_fn(any_side["session"], any_side["symbol"]):
            counts["not_covered"] += 1
            continue
        b, t = sides.get(base), sides.get(test)
        if (b and b["net"] is None) or (t and t["net"] is None):
            counts["unknown"] += 1
            continue
        counts["both_traded" if b and t else f"{base}_only" if b else f"{test}_only"] += 1
        pairs.append({"session": any_side["session"], "instrument": (t or b)["instrument"],
                      "diff": (t["net"] if t else 0.0) - (b["net"] if b else 0.0),
                      "diff_ret": (t["ret"] if t else 0.0) - (b["ret"] if b else 0.0)})
    results = []
    for label, group in [("all", pairs)] + [(i, [p for p in pairs if p["instrument"] == i])
                                             for i in sorted({p["instrument"] for p in pairs})]:
        sessions = defaultdict(list)
        for p in group:
            sessions[p["session"]].append(p)
        ci, pval = bootstrap(sessions, lambda xs: _mean([x["diff"] for x in xs]), resamples, rng)
        results.append({"group": label, "pairs": len(group), "sessions": len(sessions),
                        "mean_diff_usd": _r4(_mean([p["diff"] for p in group])), "mean_diff_ci95": ci,
                        "mean_diff_return_pct": _r4(_mean([p["diff_ret"] for p in group])), "p": _r4(pval),
                        "enough": len(group) >= min_pairs and len(sessions) >= min_sessions})
    return {"base": base, "test": test, "counts": dict(counts), "results": results}


def load_tags(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return {(r.get("trade_id") or "").strip(): r for r in csv.DictReader(fh)}


def tag_splits(items, tags, min_trades, min_sessions, resamples, rng):
    out = []
    closed = [s for s in items if s["net"] is not None and s["trade_id"] in tags]
    for rule in sorted({s["rule"] for s in closed}):
        for inst in sorted({s["instrument"] for s in closed if s["rule"] == rule}):
            group = [s for s in closed if s["rule"] == rule and s["instrument"] == inst]
            for tag, (yes, no) in TAGS.items():
                if tag == "options_checklist" and inst != "option":
                    continue
                vals = [dict(s, tag=(tags[s["trade_id"]].get(tag) or "").strip().upper()) for s in group]
                a, b = [v for v in vals if v["tag"] == yes], [v for v in vals if v["tag"] == no]
                sessions = defaultdict(list)
                for v in a + b:
                    sessions[v["session"]].append(v)
                gap = lambda xs: (None if not any(x["tag"] == yes for x in xs) or not any(x["tag"] == no for x in xs)  # noqa: E731
                                  else _mean([x["net"] for x in xs if x["tag"] == yes]) - _mean([x["net"] for x in xs if x["tag"] == no]))
                ci, pval = bootstrap(sessions, gap, resamples, rng)
                out.append({"rule_set": rule, "instrument": inst, "tag": tag, f"{yes.lower()}_trades": len(a),
                            f"{no.lower()}_trades": len(b), f"mean_net_{yes.lower()}": _r4(_mean([v["net"] for v in a])),
                            f"mean_net_{no.lower()}": _r4(_mean([v["net"] for v in b])), "difference": _r4(gap(a + b)),
                            "difference_ci95": ci, "p": _r4(pval), "sessions": len(sessions),
                            "enough": min(len(a), len(b)) >= min_trades and len(sessions) >= min_sessions})
    return out


def _verdict(row, ci, alpha, up, down):
    if not row["enough"]:
        return "insufficient_evidence"
    if row["p_holm"] is None or row["p_holm"] >= alpha or ci is None:
        return "no_difference"
    return up if ci[0] > 0 else down if ci[1] < 0 else "no_difference"


def analyze(rows, mode="REPLAY", base="R2", test="R3", sessions=None, tags=None, min_pairs=30, min_trades=15,
            min_sessions=10, resamples=2000, seed=7, alpha=0.05):
    rng = random.Random(seed)
    items = setups(rows, mode)
    pair = paired(items, base, test, sessions, min_pairs, min_sessions, resamples, rng)
    splits = tag_splits(items, tags, min_trades, min_sessions, resamples, rng) if tags is not None else []
    every = pair["results"] + splits
    for row, p in zip(every, holm([r["p"] for r in every])):
        row["p_holm"] = p
    for row in pair["results"]:
        row["verdict"] = _verdict(row, row["mean_diff_ci95"], alpha, "test_better", "base_better")
    for row in splits:
        row["verdict"] = _verdict(row, row["difference_ci95"], alpha, "higher", "lower")
    return {"mode": mode, "paired": pair, "tag_splits": splits, "tests_run": len(every),
            "tests_with_p": sum(r["p"] is not None for r in every), "alpha": alpha,
            "thresholds": {"min_pairs": min_pairs, "min_trades_per_group": min_trades, "min_sessions": min_sessions}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--mode", choices=("PAPER", "REPLAY"), default="REPLAY")
    ap.add_argument("--base", default="R2")
    ap.add_argument("--test", default="R3")
    ap.add_argument("--sessions")
    ap.add_argument("--tags")
    ap.add_argument("--min-pairs", type=int, default=30)
    ap.add_argument("--min-trades", type=int, default=15)
    ap.add_argument("--min-sessions", type=int, default=10)
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args(argv)
    rows, errors = load(a.path)
    try:
        sessions = load_sessions(a.sessions) if a.sessions else None
        tags = load_tags(a.tags) if a.tags else None
    except (OSError, csv.Error) as exc:
        print(json.dumps({"errors": [f"input: {exc}"]}))
        return 2
    out = analyze(rows, a.mode, a.base, a.test, sessions, tags, a.min_pairs, a.min_trades, a.min_sessions,
                  a.resamples, a.seed, a.alpha)
    out["errors"] = errors
    out["note"] = ("Paired: each setup against itself, a rule set that skipped it scores 0 (cash), unknown outcomes "
                   "are left out. Intervals and p-values resample whole sessions; p_holm adjusts for every test in "
                   "this run. A verdict describes this ledger, not a future edge, and changes no rule.")
    print(json.dumps(out, indent=1))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
