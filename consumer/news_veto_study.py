"""S-050 study: does a news veto help S-042? TEST ONLY. Spec frozen in Agentic-Lab system/06-research/specs/S-050-s042-news-veto.md (merged before any news was joined to a trade).

For every as-implemented S-042 V3 trade, flag fresh informative news on the signal day and compare the mean return of the trades WITHOUT news against the ones WITH news.

  python3 -m consumer.news_veto_study PANEL_DIR TRADES.json OUT_DIR

TRADES.json comes from `python3 -m engine.runner backtest PANEL OUT --dump-trades --slip-bps 9`. News comes from the Alpaca news API and is cached in OUT_DIR (private: Alpaca data terms).
Output: s050_results.json and s050_results.md (aggregate numbers only; no symbols).
"""
import json
import random
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import news_events as ne
from consumer import oversold_panel as op

ET = ZoneInfo("America/New_York")
EVENT_GROUPS = {"GUIDANCE", "EARNINGS", "LEGAL", "REGULATORY_FDA", "MANAGEMENT"}
VARIANTS = ("V1_any_informative", "V2_event_types")
RESAMPLES, SEED = 5000, 7
MIN_D_PP = 0.30                 # bar 1: pooled Judge+Final difference, percentage points
MIN_KEEP = 0.60                 # bar 3: share of trades the NO_NEWS group must keep
FLAG_RANGE = (0.05, 0.95)       # bar 4: flagged share per window
LO_Q, HI_Q = 0.0125, 0.9875     # 97.5 percent interval (two variants, Bonferroni)


def prev_session(day, calendar):
    """Last calendar date before `day` (calendar is a sorted list of session dates)."""
    before = [d for d in calendar if d < day]
    return before[-1] if before else None


def news_window(entry_date, calendar):
    """(start, end) in UTC: from the 16:00 ET close of the session before the signal day to 09:50 ET on the entry date. Nothing later is used."""
    signal = prev_session(entry_date, calendar)
    before = prev_session(signal, calendar) if signal else None
    if not signal or not before:
        return None
    start = datetime.combine(datetime.fromisoformat(before).date(), time(16, 0), tzinfo=ET)
    end = datetime.combine(datetime.fromisoformat(entry_date).date(), time(9, 50), tzinfo=ET)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def items_in_window(items, sym, start, end):
    out = []
    for n in items:
        if sym in (n.get("symbols") or []) and start <= ne.parse_ts(n["created_at"]) <= end:
            out.append(n)
    return out


def flags(items):
    """(V1, V2). V1: any informative item. V2: an informative item of an event type (not OTHER)."""
    info = [n for n in items if ne.is_informative(n)]
    return bool(info), any(ne.subgroup(n) in EVENT_GROUPS for n in info)


def diff_interval(rows, resamples=RESAMPLES, seed=SEED):
    """rows: dicts with week, ret_pp, flagged. D = mean(not flagged) - mean(flagged), cluster bootstrap by week. None when a group is empty."""
    weeks = {}
    for r in rows:
        w = weeks.setdefault(r["week"], [0.0, 0, 0.0, 0])       # news sum, news n, no-news sum, no-news n
        if r["flagged"]:
            w[0] += r["ret_pp"]; w[1] += 1
        else:
            w[2] += r["ret_pp"]; w[3] += 1
    cl = list(weeks.values())
    sn, nn, sq, nq = sum(c[0] for c in cl), sum(c[1] for c in cl), sum(c[2] for c in cl), sum(c[3] for c in cl)
    if not nn or not nq:
        return None
    rnd, m, ds = random.Random(seed), len(cl), []
    for _ in range(resamples):
        a = b = c = d = 0
        for __ in range(m):
            x = cl[rnd.randrange(m)]
            a += x[0]; b += x[1]; c += x[2]; d += x[3]
        if b and d:
            ds.append(c / d - a / b)
    ds.sort()
    if not ds:
        return None
    return {"d": sq / nq - sn / nn, "lo": ds[int(LO_Q * len(ds))], "hi": ds[int(HI_Q * len(ds)) - 1], "n_news": nn, "n_no_news": nq, "mean_news": sn / nn, "mean_no_news": sq / nq,
            "mean_all": (sn + sq) / (nn + nq), "share_flagged": nn / (nn + nq), "weeks": m}


def by_window(rows):
    out = {}
    for w in op.WINDOWS:
        lo, hi = op.WINDOWS[w]
        out[w] = [r for r in rows if lo <= r["entry_date"] <= hi]
    out["Judge+Final"] = out["Judge"] + out["Final"]
    return out


def evaluate(rows):
    """rows: one per trade with entry_date, week, ret_pp, v1, v2. Returns per-variant results and the pre-registered verdict."""
    res = {}
    for variant, key in zip(VARIANTS, ("v1", "v2")):
        rs = [{**r, "flagged": r[key]} for r in rows]
        wins = {w: diff_interval(x) for w, x in by_window(rs).items()}
        pooled, judge, final = wins["Judge+Final"], wins["Judge"], wins["Final"]
        bars = {
            "1_pooled_d_at_least_0.30_and_interval_above_zero": bool(pooled and pooled["d"] >= MIN_D_PP and pooled["lo"] > 0),
            "2_positive_in_judge_and_final": bool(judge and final and judge["d"] > 0 and final["d"] > 0),
            "3_no_news_keeps_60_percent": bool(pooled and pooled["n_no_news"] / (pooled["n_no_news"] + pooled["n_news"]) >= MIN_KEEP),
            "4_flagged_share_between_5_and_95_percent_in_each_window": all(wins[w] and FLAG_RANGE[0] <= wins[w]["share_flagged"] <= FLAG_RANGE[1] for w in op.WINDOWS),
        }
        if all(bars.values()):
            verdict = "PASS"
        elif pooled and pooled["d"] <= 0:
            verdict = "REJECTED"
        else:
            verdict = "INCONCLUSIVE"
        res[variant] = {"windows": wins, "bars": bars, "verdict": verdict}
    return res


def trade_rows(trades, calendar, news_for):
    """news_for(symbol, start, end) -> list of news items. Returns rows, plus a count of trades that had no usable window."""
    rows, skipped = [], 0
    for t in trades:
        win = news_window(t["entry_date"], calendar)
        if not win:
            skipped += 1
            continue
        items = items_in_window(news_for(t["symbol"], *win), t["symbol"], *win)
        v1, v2 = flags(items)
        rows.append({"entry_date": t["entry_date"], "week": op.week_key(t["entry_date"]), "ret_pp": 100.0 * t["ret"], "v1": v1, "v2": v2})
    return rows, skipped


def make_news_cache(call, path):
    """Fetch-and-cache news per (symbol, window). The cache is a JSONL file in the private output directory, so an interrupted run resumes."""
    done = {}
    if Path(path).exists():
        for r in ne.load_jsonl(path):
            done[(r["sym"], r["start"], r["end"])] = r["items"]
    fh = open(path, "a", encoding="utf-8")

    def news_for(sym, start, end):
        key = (sym, start.isoformat(), end.isoformat())
        if key not in done:
            items = list(ne.fetch_news(call, start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"), symbols=[sym], sleep=0.2))
            done[key] = items
            fh.write(json.dumps({"sym": sym, "start": key[1], "end": key[2], "items": items}) + "\n")
            fh.flush()
        return done[key]
    return news_for


def render(res, n_trades, skipped):
    lines = ["# S-050 results: news veto for S-042", "", f"{n_trades} trades analysed, {skipped} without a usable news window. Verdicts are from the pre-registered bar; D is NO_NEWS minus NEWS, in percentage points per trade.", ""]
    for v, r in res.items():
        lines += [f"## {v}: {r['verdict']}", "", "| Window | Trades | Flagged | Mean NEWS | Mean NO_NEWS | Mean all | D | 97.5% interval |", "|---|---|---|---|---|---|---|---|"]
        for w, x in r["windows"].items():
            if x:
                lines.append(f"| {w} | {x['n_news'] + x['n_no_news']} | {x['share_flagged']:.1%} | {x['mean_news']:+.2f} | {x['mean_no_news']:+.2f} | {x['mean_all']:+.2f} | {x['d']:+.2f} | {x['lo']:+.2f} to {x['hi']:+.2f} |")
            else:
                lines.append(f"| {w} | n/a | n/a | n/a | n/a | n/a | n/a | one group is empty |")
        lines += ["", "Bars: " + "; ".join(f"{k}: {'met' if ok else 'MISSED'}" for k, ok in r["bars"].items()), ""]
    return "\n".join(lines)


def main(argv=None):
    from consumer import alpaca_paper as ap
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 3:
        print(__doc__)
        return 2
    panel, trades_path, out = Path(a[0]), Path(a[1]), Path(a[2])
    out.mkdir(parents=True, exist_ok=True)
    data, _, digest = op.load_panel(panel)
    calendar = sorted(data["SPY"]["date"])
    trades = json.loads(trades_path.read_text())
    rows, skipped = trade_rows(trades, calendar, make_news_cache(ap.make_client(), out / "news_cache.jsonl"))
    res = evaluate(rows)
    summary = {"panel_sha256": digest, "trades": len(rows), "skipped": skipped, "results": res}
    (out / "s050_results.json").write_text(json.dumps(summary, indent=1))
    (out / "s050_results.md").write_text(render(res, len(rows), skipped))
    print(render(res, len(rows), skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
