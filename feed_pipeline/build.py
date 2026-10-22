"""Builds feed/desk-feed.json, feed/health.json and feed/desk-feed.md.

Run: python -m feed_pipeline.build [--out feed] [--now ISO8601]
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from . import calendar, features
from .sources import CBOE_SERIES, FRED_SERIES, FetchResult, fetch_cboe, fetch_fred

SCHEMA = "agentic-feed/1"
MAX_FEED_BYTES = 6000
CORE = ("VIX", "VIX3M", "SPX")
# How far behind the target session a series may be before it counts as stale.
MAX_SESSIONS_BEHIND = {"cboe": 1, "DGS2": 2, "DGS10": 2, "T10Y2Y": 2, "BAMLH0A0HYM2": 2}
MAX_DAYS_BEHIND = {"NFCI": 10, "DTWEXBGS": 10}


def sessions_behind(as_of: date, target: date) -> int:
    count, day = 0, target
    while day > as_of:
        day = calendar.previous_session(day)
        count += 1
    return count


def is_stale(result: FetchResult, target: date) -> bool:
    if not result.ok:
        return False
    if result.name in MAX_DAYS_BEHIND:
        return (target - result.as_of).days > MAX_DAYS_BEHIND[result.name]
    limit = MAX_SESSIONS_BEHIND.get(result.name, MAX_SESSIONS_BEHIND.get(result.provider, 1))
    return sessions_behind(result.as_of, target) > limit


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def build_feed(results: dict[str, FetchResult], now: datetime) -> tuple[dict, dict]:
    session = calendar.last_completed_session(now)
    series = {name: features.as_of_slice(r.series, session) for name, r in results.items()}
    get = lambda name: features.latest(series.get(name, []))  # noqa: E731

    stale = sorted(n for n, r in results.items() if is_stale(r, session))
    failed = sorted(n for n, r in results.items() if not r.ok)
    core_ok = all(n in results and results[n].ok and n not in stale for n in CORE)

    vix, vix3m, spx = get("VIX"), get("VIX3M"), get("SPX")
    rv20 = features.realized_vol(series.get("SPX", []), 20)
    sma200 = features.sma(series.get("SPX", []), 200)
    regime, reasons = features.regime_v0(vix, vix3m, spx, sma200)
    score, tilt, macro_missing = features.macro_score(series.get("BAMLH0A0HYM2", []), series.get("NFCI", []), series.get("DTWEXBGS", []))

    feed = {
        "schema": SCHEMA,
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "session": session.isoformat(),
        "calendar": calendar.CALENDAR_VERSION,
        "regime": {"label": regime, "reasons": reasons, "model": "v0-rules (unvalidated)"},
        "vol": {
            "vix": _r(vix), "vix9d": _r(get("VIX9D")), "vix3m": _r(vix3m), "vvix": _r(get("VVIX")), "skew": _r(get("SKEW")),
            "term_3m_over_1m": _r(features.ratio(vix3m, vix), 3), "term_9d_over_1m": _r(features.ratio(get("VIX9D"), vix), 3),
            "spx_rv20": _r(rv20), "vix_over_rv20": _r(features.ratio(vix, rv20), 3),
            "exp_move_1d_pct": _r(features.expected_move_pct(vix, 1)), "exp_move_5d_pct": _r(features.expected_move_pct(vix, 5)),
        },
        "trend": {"spx": _r(spx), "spx_sma200": _r(sma200), "above_sma200": None if spx is None or sma200 is None else spx > sma200},
        "macro": {
            "score": _r(score), "tilt": tilt, "model": "v0 z-score composite (unvalidated)", "missing": macro_missing,
            "dgs2": _r(get("DGS2")), "dgs10": _r(get("DGS10")), "t10y2y": _r(get("T10Y2Y")), "hy_oas": _r(get("BAMLH0A0HYM2")),
            "nfci": _r(get("NFCI"), 3), "usd_broad": _r(get("DTWEXBGS")),
        },
        "events": calendar.upcoming_events(session, 10),
        "data_health": {"core_ok": core_ok, "stale": stale, "failed": failed},
        "note": "Market data only. v0 labels are heuristics, not validated signals. Null means unavailable.",
    }
    health = {
        "schema": "agentic-feed-health/1",
        "generated_at": feed["generated_at"],
        "session": feed["session"],
        "core_ok": core_ok,
        "providers": [
            {"provider": r.provider, "name": n, "ok": r.ok, "as_of": r.as_of.isoformat() if r.as_of else None,
             "stale": n in stale, "error": r.error, "url": r.url}
            for n, r in sorted(results.items())
        ],
    }
    return feed, health


def encode(feed: dict) -> str:
    return json.dumps(feed, separators=(",", ":"), sort_keys=True)


def validate(feed: dict) -> list[str]:
    """Structural checks the consumer relies on. Returns problems; empty means valid."""
    problems = []
    if feed.get("schema") != SCHEMA:
        problems.append("schema mismatch")
    for key in ("generated_at", "session", "regime", "vol", "trend", "macro", "events", "data_health"):
        if key not in feed:
            problems.append(f"missing {key}")
    size = len(encode(feed).encode("utf-8"))
    if size > MAX_FEED_BYTES:
        problems.append(f"feed is {size} bytes, budget {MAX_FEED_BYTES}")
    return problems


def is_fresh(feed: dict, now: datetime) -> bool:
    """Consumer rule: use the feed only if it describes the last completed session and core data is healthy."""
    return (not validate(feed)) and feed["session"] == calendar.last_completed_session(now).isoformat() and feed["data_health"]["core_ok"] is True


def render_markdown(feed: dict) -> str:
    v, m, t, h = feed["vol"], feed["macro"], feed["trend"], feed["data_health"]
    fmt = lambda x: "n/a" if x is None else x  # noqa: E731
    lines = [
        f"# Agentic feed for session {feed['session']}",
        f"Generated {feed['generated_at']}. Core data healthy: {h['core_ok']}. Stale: {', '.join(h['stale']) or 'none'}. Failed: {', '.join(h['failed']) or 'none'}.",
        "",
        f"- Regime (v0, unvalidated): **{feed['regime']['label']}** ({'; '.join(feed['regime']['reasons'])})",
        f"- VIX {fmt(v['vix'])}, VIX9D {fmt(v['vix9d'])}, VIX3M {fmt(v['vix3m'])}, VVIX {fmt(v['vvix'])}, SKEW {fmt(v['skew'])}",
        f"- Term structure 3M/1M {fmt(v['term_3m_over_1m'])}; S&P 500 20d realized vol {fmt(v['spx_rv20'])}%; VIX/RV20 {fmt(v['vix_over_rv20'])}",
        f"- Implied 1-sigma move: 1 day {fmt(v['exp_move_1d_pct'])}%, 5 days {fmt(v['exp_move_5d_pct'])}%",
        f"- S&P 500 {fmt(t['spx'])} vs 200d average {fmt(t['spx_sma200'])}",
        f"- Macro (v0): score {fmt(m['score'])}, tilt {m['tilt']}; 2y {fmt(m['dgs2'])}, 10y {fmt(m['dgs10'])}, HY OAS {fmt(m['hy_oas'])}, NFCI {fmt(m['nfci'])}",
        "",
        "Events: " + ("; ".join(f"{e['date']} {e['type']}" + (f" {e['time_pt']} PT" if e["time_pt"] else "") for e in feed["events"]) or "none"),
        "",
        "Market data only. Not advice. Heuristic labels are not validated signals.",
    ]
    return "\n".join(lines) + "\n"


def collect(fetch_cboe_fn=fetch_cboe, fetch_fred_fn=fetch_fred) -> dict[str, FetchResult]:
    results = {name: fetch_cboe_fn(name) for name in CBOE_SERIES}
    results.update({name: fetch_fred_fn(name) for name in FRED_SERIES})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="feed")
    parser.add_argument("--now", default=None, help="ISO8601 timestamp to build as (testing)")
    args = parser.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    feed, health = build_feed(collect(), now)
    problems = validate(feed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Health is always written so failures are visible; the feed is written only when structurally valid.
    (out / "health.json").write_text(json.dumps(health, indent=1, sort_keys=True) + "\n")
    if problems:
        print("Feed invalid, not published:", problems)
        return 1
    (out / "desk-feed.json").write_text(encode(feed) + "\n")
    (out / "desk-feed.md").write_text(render_markdown(feed))
    print(f"Feed for session {feed['session']}: {len(encode(feed))} bytes, core_ok={feed['data_health']['core_ok']}, failed={feed['data_health']['failed']}, stale={feed['data_health']['stale']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
