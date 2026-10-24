"""Simulation history collection (issue P1-1, history store part). Market and model data only; never account data.

Each publish run condenses feed/research.json, feed/risk-sim.json, feed/vol-core.json and feed/desk-feed.json into one
record and appends it to research-history.jsonl on the `data` branch, so the daily simulation and stress-test findings
accumulate outside main's history. A run that changes nothing appends nothing (the record key is the session plus the
hashes of every input). It also rewrites RESEARCH_LOG.md there: the latest findings and a trend table, written so a
person or another assistant can read it as a standing reference.

    python3 -m feed_pipeline.history --feed feed --dir ../data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

SCHEMA = "research-history/1"
HISTORY = "research-history.jsonl"
LOG = "RESEARCH_LOG.md"
TREND_ROWS = 30
HOLDOUT_FIELDS = ("n", "win_rate", "mean_net", "mean_ci95", "ci_excludes_zero", "payoff_ratio", "p05", "worst",
                  "max_drawdown_at_10pct_allocation", "non_overlapping_trades", "resampled_outcomes")


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a missing or broken input becomes null in the record, never a guess


def _digest(obj) -> str | None:
    if obj is None:
        return None
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def summarize(research, risk, vol, feed, lab=None) -> dict:
    research = research if isinstance(research, dict) and research.get("schemaVersion") == "research-run/1" else None
    risk = risk if isinstance(risk, dict) and risk.get("schema") == "risk-sim/1" else None
    vol = vol if isinstance(vol, dict) and vol.get("schema") == "vol-core/1" else None
    feed = feed if isinstance(feed, dict) and feed.get("schema") == "agentic-feed/1" else None
    lab = lab if isinstance(lab, dict) and lab.get("schemaVersion") == "paper-lab/1" else None
    rec = {"schema": SCHEMA, "session": (feed or vol or {}).get("session") or (research or {}).get("availableAt")}
    if research:
        rec["research"] = {
            "runKey": research.get("runKey"), "codeCommit": research.get("codeCommit"),
            "availableAt": research.get("availableAt"), "uniqueObservationCount": research.get("uniqueObservationCount"),
            "scenarioCount": research.get("scenarioCount"), "policy": research.get("policy"),
            "holdout": {rule: {k: m.get("holdout", {}).get(k) for k in HOLDOUT_FIELDS}
                        for rule, m in sorted((research.get("metrics") or {}).items())},
            "tailStress": research.get("tailStress"),
        }
    if risk:
        rec["riskSim"] = {name: {"rules": rs.get("rules"), "results": [
            {k: r.get(k) for k in ("win_prob_assumed", "breakeven_win_prob", "expected_return_per_trade_pct",
                                   "p_brake_within_horizon", "p_ruin_within_horizon", "terminal_equity", "max_drawdown_pct")}
            for r in rs.get("results", [])]} for name, rs in sorted((risk.get("rule_sets") or {}).items())}
    if vol:
        rec["volCore"] = {k: vol.get(k) for k in ("session", "rv20_pct", "garch11", "vrp_vol_pts", "iv_rank_252",
                                                   "iv_pctile_252", "term_slope_3m_minus_1m", "skew_index", "rv_note")}
    if feed:
        rec["feed"] = {"vix": (feed.get("vol") or {}).get("vix"), "vix_over_rv20": (feed.get("vol") or {}).get("vix_over_rv20"),
                       "term_3m_over_1m": (feed.get("vol") or {}).get("term_3m_over_1m"),
                       "regime_v0": (feed.get("regime") or {}).get("label"), "above_sma200": (feed.get("trend") or {}).get("above_sma200")}
    if lab:
        rec["paperLab"] = {"availableAt": lab.get("availableAt"), "tradeDraws": (lab.get("synthetic") or {}).get("tradeDraws"),
                           "contractsAffordable": lab.get("contractsAffordable"),
                           "holdout": {k: {f: (v.get("holdout") or {}).get(f) for f in ("n", "win_rate", "mean_net", "mean_ci95")}
                                       for k, v in sorted(((lab.get("historical") or {}).get("results") or {}).items())},
                           "synthetic": {k: {f: v.get(f) for f in ("terminal_equity", "p_loss", "p_drawdown_over_15pct")}
                                         for k, v in sorted(((lab.get("synthetic") or {}).get("results") or {}).items())}}
    rec["key"] = _digest([rec.get("session"), (research or {}).get("runKey"), _digest(risk), _digest(vol), _digest(rec.get("feed")),
                          _digest(rec.get("paperLab"))])
    return rec


def read_history(path: Path) -> list:
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a damaged line is skipped, never rewritten
        if isinstance(row, dict) and row.get("schema") == SCHEMA:
            out.append(row)
    return out


def append(path: Path, rec: dict) -> bool:
    """Append rec unless a record with the same key exists. Returns True when a line was written."""
    if any(r.get("key") == rec["key"] for r in read_history(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
    return True


def _pct(x, d=1):
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def _num(x, d=2):
    return "n/a" if x is None else f"{x:.{d}f}"


def render_log(rows: list) -> str:
    out = ["# Research log (agentic-feed simulations and stress tests)", "",
           "Market and model data only. Option prices in the research runner are Black-Scholes values from the VIX close "
           "on S&P 500 history, not historical option quotes or any account's fills. Exits are checked at daily closes. "
           "Resampled outcomes are not new observations. Nothing here is a forecast, a measured live edge or advice, "
           "and no result changes a trading rule by itself.", ""]
    if not rows:
        return "\n".join(out + ["No runs recorded yet.", ""])
    last = rows[-1]
    out += [f"Runs recorded: {len(rows)}. Latest session: {last.get('session')}.", ""]
    res = last.get("research")
    if res:
        out += ["## Latest research run (holdout split)", "",
                f"Run key {str(res.get('runKey'))[:12]}, code {str(res.get('codeCommit'))[:7]}, data through {res.get('availableAt')}. "
                f"{res.get('uniqueObservationCount')} unique observations, {res.get('scenarioCount')} resampled scenarios.", "",
                "| rule | n | win rate | mean net | 95% interval | payoff | p05 | max DD at 10% |",
                "|---|---|---|---|---|---|---|---|"]
        for rule, h in res.get("holdout", {}).items():
            ci = h.get("mean_ci95")
            ci_txt = f"{_pct(ci[0])} to {_pct(ci[1])}" if isinstance(ci, list) and len(ci) == 2 else "n/a"
            out.append(f"| {rule} | {h.get('n')} | {_pct(h.get('win_rate'))} | {_pct(h.get('mean_net'))} | {ci_txt} | "
                       f"{_num(h.get('payoff_ratio'))} | {_pct(h.get('p05'))} | {_pct(h.get('max_drawdown_at_10pct_allocation'))} |")
        ts = res.get("tailStress") or {}
        if ts.get("results"):
            out += ["", f"Tail stress ({ts.get('label')}: IV x{ts.get('iv_multiplier')}, overnight gap {_pct(ts.get('overnight_gap'))}): " +
                    "; ".join(f"{k} mean {_pct(v.get('mean_net'))}, p05 {_pct(v.get('p05'))}" for k, v in sorted(ts["results"].items())) + "."]
        out.append("")
    risk = last.get("riskSim")
    if risk:
        out += ["## Rule-level risk simulation", "", "| rule set | assumed win rate | breakeven | expected per trade | P(brake) |",
                "|---|---|---|---|---|"]
        for name, rs in risk.items():
            for r in rs.get("results", []):
                out.append(f"| {name} | {_pct(r.get('win_prob_assumed'), 0)} | {_pct(r.get('breakeven_win_prob'))} | "
                           f"{_num(r.get('expected_return_per_trade_pct'))}% | {_pct(r.get('p_brake_within_horizon'), 0)} |")
        out.append("")
    vol, feed = last.get("volCore") or {}, last.get("feed") or {}
    if vol or feed:
        rv = (vol.get("rv20_pct") or {}).get("close_to_close")
        out += ["## Volatility context", "",
                f"VIX {_num(feed.get('vix'))}, 20-day realized {_num(rv)}, VIX/RV20 {_num(feed.get('vix_over_rv20'))}, "
                f"VRP {_num(vol.get('vrp_vol_pts'))} vol points, VIX percentile (252d) {_num(vol.get('iv_pctile_252'), 1)}, "
                f"term 3m/1m {_num(feed.get('term_3m_over_1m'), 3)}, v0 regime {feed.get('regime_v0')} (unvalidated).", ""]
    lab = last.get("paperLab")
    if lab:
        c = lab.get("contractsAffordable") or {}
        out += ["## Paper lab ($10,000 paper account, cost scenarios; modeled options)", "",
                f"{lab.get('tradeDraws')} synthetic trade draws (bounded at one million). Whole ATM SPY contracts affordable: "
                f"{c.get('score_8')} at 10% size, {c.get('score_9_10')} at 15% size (premium about ${c.get('atm_call_premium_usd')}).", "",
                "| rule / scenario | holdout n | win rate | mean net | 95% interval |", "|---|---|---|---|---|"]
        for k, h in lab.get("holdout", {}).items():
            ci = h.get("mean_ci95")
            ci_txt = f"{_pct(ci[0])} to {_pct(ci[1])}" if isinstance(ci, list) and len(ci) == 2 and None not in ci else "n/a"
            out.append(f"| {k} | {h.get('n')} | {_pct(h.get('win_rate'))} | {_pct(h.get('mean_net'))} | {ci_txt} |")
        out.append("")
    out += ["## Trend (one row per recorded run, newest last)", "",
            "| session | call above SMA200 mean | its win rate | put every session mean | breakeven (size 10%) | VIX | VRP |",
            "|---|---|---|---|---|---|---|"]
    for r in rows[-TREND_ROWS:]:
        h = (r.get("research") or {}).get("holdout", {})
        be = next((x.get("breakeven_win_prob") for x in ((r.get("riskSim") or {}).get("size_score_8") or {}).get("results", [])), None)
        out.append(f"| {r.get('session')} | {_pct((h.get('call_above_sma200') or {}).get('mean_net'))} | "
                   f"{_pct((h.get('call_above_sma200') or {}).get('win_rate'))} | {_pct((h.get('put_every_session') or {}).get('mean_net'))} | "
                   f"{_pct(be)} | {_num((r.get('feed') or {}).get('vix'))} | {_num((r.get('volCore') or {}).get('vrp_vol_pts'))} |")
    return "\n".join(out + [""])


def _write_atomic(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run(feed_dir: Path, out_dir: Path) -> bool:
    rec = summarize(_load(feed_dir / "research.json"), _load(feed_dir / "risk-sim.json"),
                    _load(feed_dir / "vol-core.json"), _load(feed_dir / "desk-feed.json"), _load(feed_dir / "paper-lab.json"))
    out_dir.mkdir(parents=True, exist_ok=True)
    added = append(out_dir / HISTORY, rec)
    _write_atomic(out_dir / LOG, render_log(read_history(out_dir / HISTORY)))
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--feed", default="feed")
    ap.add_argument("--dir", required=True)
    args = ap.parse_args(argv)
    print("history: appended" if run(Path(args.feed), Path(args.dir)) else "history: unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
