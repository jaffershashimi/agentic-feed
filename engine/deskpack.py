"""The desk pack: one report that walks the nine stages of the daily process and says what is green, what is amber and what needs the owner.

An institution's desk starts the day with one pack, not a dozen tools. This builds it from the broker's own records (read only) plus, when the workflow has
them, the day's dry-run plan, the gate decision and the execution result. Counts, percentages and check names only: no symbols, no dollar amounts.

Stages: 1 data and health  2 signals  3 pre-trade gate  4 execution  5 post-trade cost  6 reconciliation  7 risk limits  8 model monitoring  9 news (information only)
"""
from datetime import datetime, timezone

from engine import health, monitor, recon, riskreport, tca
from consumer import oversold_forward as of

ORDER = {"OK": 0, "WARN": 1, "FAIL": 2}
NAMES = {"OK": "GREEN", "WARN": "AMBER", "FAIL": "RED"}


def _safe(fn, label):
    """One stage failing must not hide the others: it shows as FAIL with the reason (no secrets or symbols are in these messages)."""
    try:
        return fn(), None
    except Exception as exc:  # the pack is a report; it never raises
        return None, f"{label} could not run: {str(exc)[:100]}"


def build(call, now=None, plan=None, gate=None, sent=None):
    now = now or datetime.now(timezone.utc)
    stages = []

    def stage(n, name, status, detail):
        stages.append({"n": n, "stage": name, "status": status, "detail": detail})

    rows, err = _safe(lambda: health.check_all(call, now), "health")
    if err:
        stage(1, "Data and health", "FAIL", err)
    else:
        bad = [r["check"] for r in rows if r["status"] != "OK"]
        worst = max((r["status"] for r in rows), key=lambda s: ORDER[s])
        stage(1, "Data and health", worst, f"{len(rows) - len(bad)} of {len(rows)} checks OK" + (f"; not OK: {', '.join(bad)}" if bad else ""))

    if plan:
        stage(2, "Signals", "OK", f"stress={plan.get('stress')}, {plan.get('v3_signals')} signals, {plan.get('slots')} free slots, {len(plan.get('entries') or [])} planned entries, "
                                  f"{sum(1 for e in plan.get('exits') or [] if e.get('action') == 'EXIT')} planned exits")
    else:
        stage(2, "Signals", "OK", "not run in this pack")

    if gate:
        failed = [c["id"] for c in gate["checks"] if not c["ok"]]
        stage(3, "Pre-trade gate", "OK" if gate["decision"] == "PASS" else "WARN",
              f"{gate['decision']}" + (f"; failed: {', '.join(failed)}" if failed else "; all checks passed"))
    else:
        stage(3, "Pre-trade gate", "OK", "not run in this pack")

    if sent:
        stage(4, "Execution", "OK", f"mode={sent.get('mode')}, submit={sent.get('submit')}, orders sent={len(sent.get('orders') or [])}, skipped={len(sent.get('skipped') or [])}"
                                    + (f", stops re-placed={len(sent['stops'].get('placed') or [])} of {sent['stops'].get('missing')} missing" if sent.get("stops") else "")
                                    + (f", note: {sent['note']}" if sent.get("note") else ""))
    else:
        stage(4, "Execution", "OK", "no execution result in this pack")

    t, err = _safe(lambda: tca.analyze(call, now), "post-trade cost analysis")
    if err:
        stage(5, "Post-trade cost (TCA)", "FAIL", err)
    else:
        f = t["fills"]
        measured = ", ".join(f"{m} {v['entry_vs_arrival'].get('n', 0)}" for m, v in t["by_mode"].items())
        stage(5, "Post-trade cost (TCA)", t["status"], f"{t['detail']}; entries measured by mode: {measured}; fills {f['filled']}/{f['sent']} sent ({f['unfilled']} unfilled, {f['partial']} partial)")

    r, err = _safe(lambda: recon.analyze(call, now), "reconciliation")
    stage(6, "Reconciliation", "FAIL" if err else r["status"], err or recon.summary(r))

    k, err = _safe(lambda: riskreport.analyze(call), "risk report")
    if err:
        stage(7, "Risk limits (proposed, report only)", "FAIL", err)
    else:
        m = k["measures"]
        stage(7, "Risk limits (proposed, report only)", k["status"], f"day {m['day_result_pct']}%, drawdown {m['drawdown_from_peak_pct']}%, gross {m['gross_exposure_pct']}% of equity, "
                                                                     f"largest name {m['largest_position_pct']}%, {m['open_positions']} positions"
                                                                     + (f"; tiers not green: {', '.join(n for n, v in k['tiers'].items() if v != 'OK')}" if k["status"] != "OK" else ""))

    trades, err = _safe(lambda: of.closed_trades(call)[0], "closed trades")
    if err:
        stage(8, "Model monitoring", "FAIL", err)
    else:
        mon = monitor.evaluate(trades, t, rows)
        worst = max((x["status"] for x in mon), key=lambda s: ORDER[s])
        stage(8, "Model monitoring", worst, "; ".join(f"{x['trigger']} {x['status']}" for x in mon))
    stage(9, "News (information only, never gates a trade)", "OK", "see the news digest step")

    overall = max((s["status"] for s in stages), key=lambda s: ORDER[s])
    owner = [s for s in stages if s["status"] == "FAIL" and s["n"] in (5, 7, 8)]
    return {"as_of": now.isoformat(timespec="minutes"), "overall": overall, "stages": stages, "owner_decisions": [f"stage {s['n']} {s['stage']}: {s['detail']}" for s in owner]}


def render(pack):
    out = [f"## Desk pack {pack['as_of']}: {NAMES[pack['overall']]}", "", "| # | Stage | Status | Detail |", "|---|---|---|---|"]
    for s in pack["stages"]:
        out.append(f"| {s['n']} | {s['stage']} | {NAMES[s['status']]} | {s['detail'].replace('|', '/')} |")
    out.append("")
    if pack["owner_decisions"]:
        out.append("**Needs the owner (engineering does not act on these alone):**")
        out += [f"- {d}" for d in pack["owner_decisions"]]
    else:
        out.append("No owner decision needed from this pack.")
    return "\n".join(out)
