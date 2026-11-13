"""Stock-only rank and influence audit of the R2 entry score (TEST ONLY, descriptive).

Implements the frozen audit designs ChatGPT's Research Student proposed on 2026-11-12 and 2026-11-13
(R2_STOCK_ONLY_RANK_AUDIT_V3 and R2_TOP_BUCKET_INFLUENCE_AUDIT_V1) on a ledger CSV. It changes no rule, weight,
threshold or policy and writes nothing to the ledger.

Why it exists: the pinned score_check.py has no asset-class, arm, rule-set or date filter, reports no median,
trimmed mean or leave-one-out range, and silently drops trades that are not closed. A headline such as "the score 9
to 10 bucket averaged +5.77 percent" can rest on a dozen trades from a few sessions. This tool states, for each score
bucket of a fixed cohort: trades, sessions, symbols, mean, median, 10 percent trimmed and winsorized means, the share
of the bucket's return sum carried by its top 1, 2 and 3 trades, the range of the mean when each session (or each
trade) is left out, and the gap between the 9 to 10 bucket and the 8 bucket with a session-cluster bootstrap
interval. It also reports the Spearman rank correlation (average ranks for ties) with a session-cluster interval, and
for the whole entered cohort how many trades are closed, still open, or UNRESOLVED_CLOSED, by bucket, and what average
return the incomplete trades would need for the overall mean to change sign.

Cohort (all fixed by arguments, printed in the output): mode REPLAY, instrument equity, the stock arm (REPLAY-B-<rule>-),
source starting REPLAY_<rule set>, entry date (Pacific time) on or after --since and on or before --until.
Net USD = (exit - entry) * qty * multiplier - fees on both rows (a blank fee counts as 0); return percent is net USD
over entry notional. Bootstrap: 2,000 resamples, seed 7, whole entry sessions resampled together.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
BUCKETS = ("6", "7", "8", "9-10")
RESAMPLES = 2000
SEED = 7


def bucket_of(score: int) -> str | None:
    if score >= 9:
        return "9-10"
    return str(score) if score in (6, 7, 8) else None


def _num(text, default=0.0):
    text = (text or "").strip()
    return float(text) if text else default


def load_trades(path, rule_set="R2", since=None, until=None):
    """One dict per stock-arm replay trade of the rule set, with status CLOSED, OPEN or UNRESOLVED_CLOSED."""
    entries, exits = {}, {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["mode"] != "REPLAY" or r["instrument"] != "equity":
                continue
            if not r["arm"].startswith(f"REPLAY-B-{rule_set}-") or not r["source"].startswith(f"REPLAY_{rule_set}"):
                continue
            if r["kind"] == "entry":
                entries[r["trade_id"]] = r
            elif r["kind"] == "exit":
                exits[r["trade_id"]] = r
    trades = []
    for tid, e in entries.items():
        day = datetime.fromisoformat(e["occurred_at"]).astimezone(PT).date()
        if (since and day < since) or (until and day > until):
            continue
        score = int(e["score"]) if (e["score"] or "").strip() else None
        t = {"trade_id": tid, "symbol": e["symbol"], "session": day.isoformat(), "score": score,
             "bucket": bucket_of(score) if score is not None else None,
             "entry_time": datetime.fromisoformat(e["occurred_at"]).astimezone(PT).strftime("%H:%M")}
        x = exits.get(tid)
        if x is None:
            t["status"] = "OPEN"
        elif "UNRESOLVED_CLOSED" in x["source"]:
            t["status"] = "UNRESOLVED_CLOSED"
        else:
            t["status"] = "CLOSED"
            mult = _num(e["multiplier"], 1.0)
            qty, ep, xp = _num(e["qty"]), _num(e["price"]), _num(x["price"])
            net = (xp - ep) * qty * mult - _num(e["fees"]) - _num(x["fees"])
            t["net_usd"] = net
            t["ret_pct"] = net / (ep * qty * mult) * 100 if ep * qty * mult else 0.0
        trades.append(t)
    return trades


def trimmed_mean(xs, frac=0.10):
    xs = sorted(xs)
    k = int(len(xs) * frac)
    core = xs[k:len(xs) - k] if len(xs) - 2 * k > 0 else xs
    return sum(core) / len(core)


def winsorized_mean(xs, frac=0.10):
    xs = sorted(xs)
    k = int(len(xs) * frac)
    if k and len(xs) - 2 * k > 0:
        xs = [xs[k]] * k + xs[k:len(xs) - k] + [xs[len(xs) - k - 1]] * k
    return sum(xs) / len(xs)


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def _ci(vals):
    vals = sorted(v for v in vals if v is not None)
    if len(vals) < 20:
        return None
    return [round(vals[int(0.025 * (len(vals) - 1))], 4), round(vals[int(0.975 * (len(vals) - 1))], 4)]


def cluster_resamples(closed, stat, n=RESAMPLES, seed=SEED):
    """stat(list_of_trades) evaluated on resamples of whole sessions."""
    by = {}
    for t in closed:
        by.setdefault(t["session"], []).append(t)
    sessions = sorted(by)
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        pick = [rng.choice(sessions) for _ in sessions]
        out.append(stat([t for s in pick for t in by[s]]))
    return out


def bucket_stats(trades):
    rets = [t["ret_pct"] for t in trades]
    n = len(rets)
    out = {"trades": n, "sessions": len({t["session"] for t in trades}), "symbols": len({t["symbol"] for t in trades})}
    if not n:
        return out
    total = sum(rets)
    out.update(mean_pct=round(total / n, 4), median_pct=round(statistics.median(rets), 4),
               trimmed10_pct=round(trimmed_mean(rets), 4), winsor10_pct=round(winsorized_mean(rets), 4),
               mean_usd=round(sum(t["net_usd"] for t in trades) / n, 2), win_share=round(sum(r > 0 for r in rets) / n, 3))
    top = sorted(rets, reverse=True)
    out["top_share_of_sum"] = {f"top{k}": (round(sum(top[:k]) / total, 3) if total else None) for k in (1, 2, 3) if k <= n}
    sessions = sorted({t["session"] for t in trades})
    loso = [sum(r for t, r in zip(trades, rets) if t["session"] != s) / (n - sum(t["session"] == s for t in trades))
            for s in sessions if n - sum(t["session"] == s for t in trades) > 0]
    if loso:
        out["leave_one_session_out_mean"] = {"min": round(min(loso), 4), "max": round(max(loso), 4),
                                             "sign_stable": all(v > 0 for v in loso) or all(v < 0 for v in loso)}
    if n > 1:
        lo = [(total - r) / (n - 1) for r in rets]
        out["leave_one_trade_out_mean"] = {"min": round(min(lo), 4), "max": round(max(lo), 4),
                                           "sign_stable": all(v > 0 for v in lo) or all(v < 0 for v in lo)}
    return out


def audit(trades, label=""):
    closed = [t for t in trades if t["status"] == "CLOSED" and t["bucket"]]
    rep = {"label": label, "cohort_entries": len(trades), "status_counts": {}, "buckets": {}, "completeness_by_bucket": {}}
    for t in trades:
        rep["status_counts"][t["status"]] = rep["status_counts"].get(t["status"], 0) + 1
    for b in BUCKETS:
        rep["buckets"][b] = bucket_stats([t for t in closed if t["bucket"] == b])
        mine = [t for t in trades if t["bucket"] == b]
        c = sum(t["status"] == "CLOSED" for t in mine)
        rep["completeness_by_bucket"][b] = {"entries": len(mine), "closed": c,
                                            "open": sum(t["status"] == "OPEN" for t in mine),
                                            "unresolved_closed": sum(t["status"] == "UNRESOLVED_CLOSED" for t in mine),
                                            "closure_share": round(c / len(mine), 3) if mine else None}
    if closed:
        rep["closed_overall"] = bucket_stats(closed)
        rho = spearman([t["score"] for t in closed], [t["ret_pct"] for t in closed])
        rep["spearman"] = {"rho": round(rho, 4) if rho is not None else None,
                           "ci95_session_cluster": _ci(cluster_resamples(closed, lambda ts: spearman([t["score"] for t in ts], [t["ret_pct"] for t in ts])))}
        def gap(ts):
            hi = [t["ret_pct"] for t in ts if t["bucket"] == "9-10"]
            lo = [t["ret_pct"] for t in ts if t["bucket"] == "8"]
            return (sum(hi) / len(hi) - sum(lo) / len(lo)) if hi and lo else None
        g = gap(closed)
        rep["gap_9_10_minus_8_pct"] = {"gap": round(g, 4) if g is not None else None,
                                       "ci95_session_cluster": _ci(cluster_resamples(closed, gap))}
        incomplete = [t for t in trades if t["status"] != "CLOSED"]
        total = sum(t["ret_pct"] for t in closed)
        rep["sign_flip"] = {"closed_mean_pct": round(total / len(closed), 4), "incomplete_entries": len(incomplete),
                            "avg_return_incomplete_needed_to_flip_pct": (round(-total / len(incomplete), 4) if incomplete else None)}
    return rep


def render_markdown(rep):
    lines = [f"### {rep['label']}", "", f"Entered cohort {rep['cohort_entries']}; status counts {rep['status_counts']}.", "",
             "| Score | Trades | Sessions | Symbols | Mean % | Median % | Trim10 % | Winsor10 % | Top1 / Top2 / Top3 share of sum | Leave-one-session-out mean % (min to max, sign stable) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for b in BUCKETS:
        s = rep["buckets"][b]
        if not s.get("trades"):
            lines.append(f"| {b} | 0 | | | | | | | | |")
            continue
        ts = s.get("top_share_of_sum", {})
        lo = s.get("leave_one_session_out_mean", {})
        lines.append(f"| {b} | {s['trades']} | {s['sessions']} | {s['symbols']} | {s['mean_pct']} | {s['median_pct']} | {s['trimmed10_pct']} | "
                     f"{s['winsor10_pct']} | {ts.get('top1')} / {ts.get('top2')} / {ts.get('top3')} | {lo.get('min')} to {lo.get('max')}, {lo.get('sign_stable')} |")
    if "spearman" in rep:
        lines += ["", f"Spearman rho {rep['spearman']['rho']} (95% session-cluster {rep['spearman']['ci95_session_cluster']}). "
                      f"Gap 9-10 minus 8: {rep['gap_9_10_minus_8_pct']['gap']} points (95% {rep['gap_9_10_minus_8_pct']['ci95_session_cluster']}). "
                      f"Sign flip: the {rep['sign_flip']['incomplete_entries']} incomplete entries would need an average return of "
                      f"{rep['sign_flip']['avg_return_incomplete_needed_to_flip_pct']}% to move the closed mean ({rep['sign_flip']['closed_mean_pct']}%) to zero."]
    lines += ["", "| Score | Entries | Closed | Open | UNRESOLVED_CLOSED | Closure share |", "|---|---|---|---|---|---|"]
    for b in BUCKETS:
        c = rep["completeness_by_bucket"][b]
        lines.append(f"| {b} | {c['entries']} | {c['closed']} | {c['open']} | {c['unresolved_closed']} | {c['closure_share']} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ledger")
    ap.add_argument("--rule-set", default="R2", choices=("R2", "R3", "R4"))
    ap.add_argument("--since", type=date.fromisoformat, help="first entry date, Pacific time (inclusive)")
    ap.add_argument("--until", type=date.fromisoformat, help="last entry date, Pacific time (inclusive)")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", help="also write the report as JSON to this path")
    a = ap.parse_args(argv)
    trades = load_trades(a.ledger, a.rule_set, a.since, a.until)
    label = a.label or f"{a.rule_set} stock arm, entries {a.since or 'start'} to {a.until or 'end'} (Pacific)"
    rep = audit(trades, label)
    rep["cohort"] = {"mode": "REPLAY", "instrument": "equity", "arm": f"REPLAY-B-{a.rule_set}-*", "since": str(a.since), "until": str(a.until)}
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(render_markdown(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
