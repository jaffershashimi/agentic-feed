"""S-044 study: big single-stock drops, news vs no news, what happens over the next 5 and 10 sessions? TEST ONLY. Spec frozen in Agentic-Lab
system/06-research/specs/S-044-news-drop-drift.md. Reads the private daily panel, the event list and the cached news; writes JSON and markdown.

  python3 -m consumer.news_drop_study PANEL_DIR EVENTS.json NEWS.jsonl OUT_DIR
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

from consumer import news_events as ne
from consumer import oversold_panel as op

COST = 0.0005
WINDOWS = op.WINDOWS


def classify(sym, news_items):
    """('NEWS'|'ANALYST'|'NO_NEWS', subgroup|None) from the cached items of one event day."""
    mine = sorted((n for n in news_items if sym in (n.get("symbols") or [])), key=lambda n: n["created_at"])
    info = [n for n in mine if ne.is_informative(n)]
    if info:
        return "NEWS", ne.subgroup(info[0])
    if any(ne.is_analyst(n) for n in mine):
        return "ANALYST", None
    return "NO_NEWS", None


def window_of(day):
    for w, (lo, hi) in WINDOWS.items():
        if lo <= day <= hi:
            return w
    return None


def event_returns(data, spy_idx, spy, ev):
    d = data.get(ev["sym"])
    if not d or ev["date"] not in d["date"]:
        return None
    i = d["date"].index(ev["date"])
    if i + 10 >= len(d["c"]) or ev["date"] not in spy_idx:
        return None
    si = spy_idx[ev["date"]]
    if spy["date"][si + 10] != d["date"][i + 10]:
        return None  # calendars disagree: skip rather than misalign
    out = {}
    for name, entry, spy_entry in (("C0", d["c"][i], spy["c"][si]), ("O1", d["o"][i + 1], spy["o"][si + 1])):
        for k in (5, 10):
            xi = i + k
            out[f"{name}_{k}"] = (d["c"][xi] / entry - 1.0) - (spy["c"][si + k] / spy_entry - 1.0)
    return out


def summarize(rows, key):
    vals = [r["ret"][key] for r in rows]
    st = ne.cluster_bootstrap(vals, [r["date"] for r in rows])
    if st is None:
        return {"n": 0}
    s = sorted(vals)
    st.update({"median": s[len(s) // 2], "share_pos": sum(1 for v in vals if v > 0) / len(vals)})
    return st


def run(panel, events_path, news_path, out):
    data, universe, digest = op.load_panel(Path(panel))
    spy = data["SPY"]
    spy_idx = {d: i for i, d in enumerate(spy["date"])}
    events = json.loads(Path(events_path).read_text())
    by_day = defaultdict(list)
    for n in ne.load_jsonl(news_path):
        by_day[n["day"]].append(n)
    rows = []
    for ev in events:
        er = event_returns(data, spy_idx, spy, ev)
        if er is None:
            continue
        grp, sub = classify(ev["sym"], by_day.get(ev["date"], []))
        rows.append({"sym": ev["sym"], "date": ev["date"], "drop": ev["ret"], "group": grp, "sub": sub, "win": window_of(ev["date"]), "ret": er})
    res = {"data_sha256": digest, "events_total": len(events), "events_scored": len(rows), "groups": {}, "subgroups": {}, "verdict": {}}
    keys = [(g, w) for g in ("NEWS", "NO_NEWS", "ANALYST", "ALL") for w in ("Development", "Judge", "Final", "Judge+Final")]
    for g, w in keys:
        sel = [r for r in rows if (g == "ALL" or r["group"] == g) and (r["win"] in w.split("+"))]
        res["groups"][f"{g}|{w}"] = {k: summarize(sel, k) for k in ("C0_5", "C0_10", "O1_5", "O1_10")}
    for sg in [n for n, _ in ne.GROUPS] + ["OTHER"]:
        sel = [r for r in rows if r["group"] == "NEWS" and r["sub"] == sg and r["win"] in ("Judge", "Final")]
        res["subgroups"][sg] = summarize(sel, "O1_10")
    ps = [(sg, v.get("p_ge_zero", 1.0)) for sg, v in res["subgroups"].items() if v.get("n")]
    for (sg, _), adj in zip(ps, op.holm([p for _, p in ps])):
        res["subgroups"][sg]["holm_p_continuation"] = adj
    res["verdict"] = verdict(res)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "s044_results.json").write_text(json.dumps(res, indent=1))
    (out / "s044_results.md").write_text(render(res))
    return res


def verdict(res):
    g = res["groups"]

    def m(grp, w):
        return g[f"{grp}|{w}"]["O1_10"].get("mean")

    pooled = g["NEWS|Judge+Final"]["O1_10"]
    short = "INCONCLUSIVE"
    if pooled.get("n", 0) >= 100:
        if (m("NEWS", "Judge") or 0) <= -0.01 and (m("NEWS", "Final") or 0) <= -0.01 and pooled["hi"] < 0:
            short = "PROMISING"
        elif (pooled["mean"] or 0) > 0:
            short = "REJECTED"
    pn = g["NO_NEWS|Judge+Final"]["O1_10"]
    reb = "INCONCLUSIVE"
    if pn.get("n", 0) >= 100:
        if (m("NO_NEWS", "Judge") or 0) >= 0.01 and (m("NO_NEWS", "Final") or 0) >= 0.01 and pn["lo"] > 0:
            reb = "PROMISING"
        elif (pn["mean"] or 0) < 0:
            reb = "REJECTED"
    return {"news_drift_short": short, "no_news_rebound_long": reb}


def render(res):
    L = ["# S-044 results (TEST ONLY)", "", f"Panel digest `{res['data_sha256'][:12]}`. Events {res['events_total']}, scored {res['events_scored']}. Returns are market-adjusted (minus SPY), long view, no costs; a short earns the negative minus 0.10% round trip.", "",
         f"**Verdicts:** news-drift short {res['verdict']['news_drift_short']}; no-news rebound long {res['verdict']['no_news_rebound_long']}.", "",
         "| Group | Window | n | O1 to t+5 | O1 to t+10 | interval t+10 | C0 to t+10 |", "|---|---|---|---|---|---|---|"]
    for k, v in res["groups"].items():
        g, w = k.split("|")
        a, b, c = v["O1_5"], v["O1_10"], v["C0_10"]
        if not b.get("n"):
            continue
        L.append(f"| {g} | {w} | {b['n']} | {a['mean'] * 100:+.2f}% | {b['mean'] * 100:+.2f}% | {b['lo'] * 100:+.2f}% to {b['hi'] * 100:+.2f}% | {c['mean'] * 100:+.2f}% |")
    L += ["", "NEWS subgroups, Judge+Final, O1 to t+10:", "", "| Subgroup | n | mean | interval | Holm p (continuation) |", "|---|---|---|---|---|"]
    for sg, v in res["subgroups"].items():
        if v.get("n"):
            L.append(f"| {sg} | {v['n']} | {v['mean'] * 100:+.2f}% | {v['lo'] * 100:+.2f}% to {v['hi'] * 100:+.2f}% | {v.get('holm_p_continuation', float('nan')):.3f} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    res = run(a[0], a[1], a[2], a[3])
    print(json.dumps(res["verdict"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
