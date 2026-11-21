"""S-057 evidence check: S&P 500 outcomes after monthly options expiration days with the VIX at or above 30. TEST ONLY, descriptive, standard library only.

Implements the frozen spec (Agentic-Lab system/06-research/specs/S-057-vix30-opex-reference-check.md) exactly: the typed table of 30 events, a transcription check against the
table's published summary rows, then five tests with 10,000 seeded resamples or simulations each:
  T1 naive event bootstrap, T2 episode-clustered bootstrap (primary), T3 share higher against the baseline (Monte Carlo and exact binomial),
  T4 further-downside shares, T5 empirical outcome spread for the unresolved 2026-11-20 event.
It is not a strategy: no rule, no pass bar, nothing is traded or changed. The data class is LITERATURE (a third-party table), evidence grade C.

    python3 -m consumer.vix_opex_evidence EVENTS_CSV OUT_DIR
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path

SEED = 20261121
N_SIMS = 10000
HORIZONS = ("d1", "w1", "w2", "m1", "m3", "m6", "y1")
LABEL = {"d1": "1 day", "w1": "1 week", "w2": "2 weeks", "m1": "1 month", "m3": "3 months", "m6": "6 months", "y1": "1 year"}
BASELINE_ALL_OPEX = {"d1": 50, "w1": 52, "w2": 62, "m1": 64, "m3": 71, "m6": 74, "y1": 80}  # share higher, all 442 opex days (screenshot small print)
BASELINE_ALL_DAYS = {"d1": 54, "w1": 58, "w2": 60, "m1": 64, "m3": 70, "m6": 75, "y1": 81}
PUB_MEAN = {"vix": 39.8, "d1": 0.0, "w1": 0.5, "w2": 1.3, "m1": 3.2, "m3": 6.8, "m6": 14.5, "y1": 25.7, "worst3m": -6.9, "vix_chg20": -4.6}
PUB_MEDIAN = {"vix": 35.0, "d1": -0.5, "w1": 0.3, "w2": 1.9, "m1": 4.4, "m3": 6.9, "m6": 17.4, "y1": 25.6, "worst3m": -4.9, "vix_chg20": -5.6}
PUB_HIGHER = {"d1": 50, "w1": 53, "w2": 67, "m1": 77, "m3": 77, "m6": 93, "y1": 90}


def load(path) -> list:
    rows, ep = [], -1
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            d = {k: (v if k == "date" else float(v)) for k, v in r.items()}
            d["first_in_run"] = int(d["first_in_run"])
            if d["first_in_run"] == 1:
                ep += 1
            d["episode"] = ep
            rows.append(d)
    if not rows or rows[0]["episode"] < 0:
        raise ValueError("the first event must start an episode (first_in_run = 1)")
    return rows


def episodes(rows) -> list:
    out = {}
    for r in rows:
        out.setdefault(r["episode"], []).append(r)
    return [out[k] for k in sorted(out)]


def pctl(xs, q):
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def ci(xs):
    return [round(pctl(xs, 0.025), 2), round(pctl(xs, 0.975), 2)]


def transcription_check(rows) -> dict:
    """Recompute the published summary rows from the typed events. Tolerance: 0.1 point on averages and medians, one event on share higher."""
    n = len(rows)
    out, bad = [], []
    for c in ("vix",) + HORIZONS + ("worst3m", "vix_chg20"):
        v = [r[c] for r in rows]
        a, m = statistics.mean(v), statistics.median(v)
        cell = {"col": c, "mean": round(a, 2), "pub_mean": PUB_MEAN[c], "median": round(m, 2), "pub_median": PUB_MEDIAN[c]}
        ok = abs(a - PUB_MEAN[c]) <= 0.1 + 1e-9 and abs(m - PUB_MEDIAN[c]) <= 0.1 + 1e-9
        if c in PUB_HIGHER:
            hi = sum(1 for x in v if x > 0)
            cell["higher_events"], cell["pub_higher_pct"] = hi, PUB_HIGHER[c]
            ok = ok and abs(100 * hi / n - PUB_HIGHER[c]) <= 100 / n + 0.5
        cell["ok"] = ok
        if not ok:
            bad.append(c)
        out.append(cell)
    return {"events": n, "episodes": len(episodes(rows)), "first_in_run": sum(r["first_in_run"] for r in rows), "cells": out, "off_tolerance": bad}


def exact_tail(n, k, p) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def mc_tail(n, k, p, rng, sims) -> float:
    hits = sum(1 for _ in range(sims) if sum(1 for _ in range(n) if rng.random() < p) >= k)
    return (hits + 1) / (sims + 1)


def run(rows, sims=N_SIMS, seed=SEED) -> dict:
    rng = random.Random(seed)
    eps = episodes(rows)
    E = len(eps)
    res = {"seed": seed, "sims": sims, "events": len(rows), "episodes": E}
    ev_vals = {h: [r[h] for r in rows] for h in HORIZONS}
    ep_vals = {h: [[r[h] for r in e] for e in eps] for h in HORIZONS}

    # T1: naive event bootstrap
    t1 = {h: ([], []) for h in HORIZONS}
    for _ in range(sims):
        for h in HORIZONS:
            s = [rng.choice(ev_vals[h]) for _ in ev_vals[h]]
            t1[h][0].append(sum(s) / len(s))
            t1[h][1].append(statistics.median(s))
    res["T1_event_bootstrap"] = {h: {"mean": round(statistics.mean(ev_vals[h]), 2), "mean_ci": ci(t1[h][0]), "median": round(statistics.median(ev_vals[h]), 2), "median_ci": ci(t1[h][1])} for h in HORIZONS}

    # T2: episode-clustered bootstrap (primary); the same resampled episodes feed every horizon and the worst-close shares
    t2 = {h: ([], []) for h in HORIZONS}
    worst = {-5.0: [], -10.0: []}
    for _ in range(sims):
        idx = [rng.randrange(E) for _ in range(E)]
        for h in HORIZONS:
            flat = [x for i in idx for x in ep_vals[h][i]]
            t2[h][0].append(sum(flat) / len(flat))
            t2[h][1].append(statistics.median(flat))
        wflat = [r["worst3m"] for i in idx for r in eps[i]]
        for thr in worst:
            worst[thr].append(100 * sum(1 for x in wflat if x <= thr) / len(wflat))
    res["T2_episode_bootstrap"] = {h: {"mean_ci": ci(t2[h][0]), "median_ci": ci(t2[h][1])} for h in HORIZONS}

    # T3: share higher against the baseline
    first = [r for r in rows if r["first_in_run"] == 1]
    t3 = {"all_events": {}, "first_in_run": {}}
    for label, grp in (("all_events", rows), ("first_in_run", first)):
        n = len(grp)
        for h in HORIZONS:
            k = sum(1 for r in grp if r[h] > 0)
            p = BASELINE_ALL_OPEX[h] / 100
            t3[label][h] = {"n": n, "higher": k, "share_pct": round(100 * k / n, 1), "baseline_pct": BASELINE_ALL_OPEX[h],
                            "p_mc": round(mc_tail(n, k, p, rng, sims), 4), "p_exact": round(exact_tail(n, k, p), 4)}
    res["T3_share_higher"] = t3

    # T4: further downside (worst close within 3 months, from the opex close)
    t4 = {}
    for thr in (-5.0, -10.0):
        ev = sum(1 for r in rows if r["worst3m"] <= thr)
        fi = sum(1 for r in first if r["worst3m"] <= thr)
        t4[str(int(thr))] = {"events": f"{ev} of {len(rows)}", "events_pct": round(100 * ev / len(rows), 1), "episode_boot_pct_ci": ci(worst[thr]),
                             "first_in_run": f"{fi} of {len(first)}", "first_in_run_pct": round(100 * fi / len(first), 1)}
    res["T4_downside"] = t4

    # T5: empirical outcome spread for the unresolved 2026-11-20 event (not a forecast)
    def spread(pick):
        m1, m3, wc = [], [], []
        for _ in range(sims):
            r = pick()
            m1.append(r["m1"])
            m3.append(r["m3"])
            wc.append(r["worst3m"])
        q = lambda xs: [round(pctl(xs, x), 1) for x in (0.10, 0.25, 0.50, 0.75, 0.90)]
        return {"m1_p10_p25_p50_p75_p90": q(m1), "m3_p10_p25_p50_p75_p90": q(m3), "p_m1_positive": round(100 * sum(1 for x in m1 if x > 0) / sims, 1),
                "p_m3_positive": round(100 * sum(1 for x in m3 if x > 0) / sims, 1), "p_worst_le_-10": round(100 * sum(1 for x in wc if x <= -10) / sims, 1),
                "p_worst_le_-5": round(100 * sum(1 for x in wc if x <= -5) / sims, 1)}
    res["T5_outcome_spread"] = {"episode_weighted": spread(lambda: rng.choice(eps[rng.randrange(E)])), "event_weighted": spread(lambda: rng.choice(rows))}

    # descriptive subgroups
    sub = {}
    for name, grp in (("VIX 30 to 40", [r for r in rows if r["vix"] < 40]), ("VIX 40 and above", [r for r in rows if r["vix"] >= 40])):
        sub[name] = {"n": len(grp), **{h + "_median": round(statistics.median([r[h] for r in grp]), 1) for h in ("m1", "m3", "m6", "y1")},
                     "worst3m_median": round(statistics.median([r["worst3m"] for r in grp]), 1)}
    res["subgroups"] = sub

    res["verdict"] = verdict(res)
    return res


def verdict(res) -> dict:
    met = []
    for h in ("m1", "m6"):
        lo = res["T2_episode_bootstrap"][h]["median_ci"][0]
        p = res["T3_share_higher"]["first_in_run"][h]["p_mc"]
        met.append({"horizon": h, "median_ci_low": lo, "p_first_in_run": p, "met": bool(lo > 0 and p < 0.05)})
    n = sum(1 for m in met if m["met"])
    label = "SUPPORTED" if n == 2 else "PARTLY_SUPPORTED" if n == 1 else "NOT_SUPPORTED"
    coin = {}
    for h in ("d1", "w1"):
        lo, hi = res["T2_episode_bootstrap"][h]["median_ci"]
        share = res["T3_share_higher"]["all_events"][h]["share_pct"]
        coin[h] = bool(lo <= 0 <= hi and 40 <= share <= 60)
    return {"label": label, "conditions": met, "near_term_coin_flip": coin}


def render(res, tc, data_sha) -> str:
    L = ["# S-057 results: VIX at or above 30 on monthly opex (reference evidence check)", "",
         f"TEST ONLY, descriptive, data class LITERATURE (a third-party table), evidence grade C. Data sha256 `{data_sha}`. Seed {res['seed']}, {res['sims']:,} resamples or simulations per test.",
         f"{res['events']} events in {res['episodes']} episodes. Not a strategy; changes no rule.", "",
         f"## Verdict by the frozen rules: **{res['verdict']['label']}**", ""]
    for c in res["verdict"]["conditions"]:
        L.append(f"- {LABEL[c['horizon']]}: episode-clustered median interval low end {c['median_ci_low']:+.2f}%, first-in-run p-value {c['p_first_in_run']}: {'met' if c['met'] else 'not met'}")
    L += [f"- Near-term coin flip (interval includes zero and share higher 40 to 60 percent): 1 day {res['verdict']['near_term_coin_flip']['d1']}, 1 week {res['verdict']['near_term_coin_flip']['w1']}", "",
          "## Transcription check", f"Off tolerance: {tc['off_tolerance'] or 'none'}. {tc['events']} events, {tc['episodes']} episodes, {tc['first_in_run']} first in a run.", "",
          "## T2 episode-clustered bootstrap (primary) against T1 naive event bootstrap, 95% intervals for the median S&P 500 change", "",
          "| Horizon | Median | Naive interval | Clustered interval | Share higher (all 30) | Baseline share (all opex days) |", "|---|---|---|---|---|---|"]
    for h in HORIZONS:
        a, b = res["T1_event_bootstrap"][h], res["T2_episode_bootstrap"][h]
        s = res["T3_share_higher"]["all_events"][h]
        L.append(f"| {LABEL[h]} | {a['median']:+.2f}% | {a['median_ci'][0]:+.2f} to {a['median_ci'][1]:+.2f} | {b['median_ci'][0]:+.2f} to {b['median_ci'][1]:+.2f} | {s['share_pct']}% | {s['baseline_pct']}% |")
    L += ["", "## T3 share higher against the baseline, one-sided Monte Carlo p-value (exact binomial in brackets)", "",
          "| Horizon | All 30 events | p | First in a run (12) | p |", "|---|---|---|---|---|"]
    for h in HORIZONS:
        a, b = res["T3_share_higher"]["all_events"][h], res["T3_share_higher"]["first_in_run"][h]
        L.append(f"| {LABEL[h]} | {a['higher']} of {a['n']} ({a['share_pct']}%) | {a['p_mc']} ({a['p_exact']}) | {b['higher']} of {b['n']} ({b['share_pct']}%) | {b['p_mc']} ({b['p_exact']}) |")
    L += ["", "## T4 further downside after the opex close (worst close within 3 months)", "", "| At or below | Events | Episode-bootstrap interval | First in a run |", "|---|---|---|---|"]
    for thr, d in res["T4_downside"].items():
        L.append(f"| {thr}% | {d['events']} ({d['events_pct']}%) | {d['episode_boot_pct_ci'][0]} to {d['episode_boot_pct_ci'][1]}% | {d['first_in_run']} ({d['first_in_run_pct']}%) |")
    L += ["", "## T5 empirical outcome spread for the unresolved 2026-11-20 event (30 past events, not a forecast)", "",
          "| Weighting | 1 month p10 / p25 / p50 / p75 / p90 | 3 months p10 / p25 / p50 / p75 / p90 | 1 month up | 3 months up | Worst close at or below -5% | at or below -10% |", "|---|---|---|---|---|---|---|"]
    for name, d in res["T5_outcome_spread"].items():
        L.append(f"| {name.replace('_', ' ')} | {' / '.join(str(x) for x in d['m1_p10_p25_p50_p75_p90'])} | {' / '.join(str(x) for x in d['m3_p10_p25_p50_p75_p90'])} | {d['p_m1_positive']}% | {d['p_m3_positive']}% | {d['p_worst_le_-5']}% | {d['p_worst_le_-10']}% |")
    L += ["", "## Descriptive subgroups (medians)", ""]
    for name, d in res["subgroups"].items():
        L.append(f"- {name} (n={d['n']}): 1 month {d['m1_median']:+.1f}%, 3 months {d['m3_median']:+.1f}%, 6 months {d['m6_median']:+.1f}%, 1 year {d['y1_median']:+.1f}%, worst close within 3 months {d['worst3m_median']:+.1f}%")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    a = sys.argv[1:] if argv is None else argv
    csv_path, out = Path(a[0]), Path(a[1])
    out.mkdir(parents=True, exist_ok=True)
    rows = load(csv_path)
    sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    tc = transcription_check(rows)
    res = run(rows)
    res["data_sha256"], res["transcription_check"] = sha, tc
    (out / "s057_results.json").write_text(json.dumps(res, indent=1))
    (out / "s057_results.md").write_text(render(res, tc, sha))
    print(json.dumps({"verdict": res["verdict"]["label"], "transcription_off": tc["off_tolerance"], "sims": res["sims"], "sha256": sha[:12]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
