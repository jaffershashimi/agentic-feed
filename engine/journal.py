"""Decision journal: every closed trade settled against what actually happened, with a deterministic review. Read only. Standard library only.

An institutional desk keeps a trade log that is settled against results and reviewed. This builds that from the broker's own orders (no memory file to lose):
per strategy, the closed trades, the result against SPY over the same days, how the exits went, and plain flags about what the record says so far.
It never trades and never changes a rule. Output: counts and percentages only (no symbols); the per-trade rows, which name stocks, go to a private file you choose.
No language model is involved: the review is arithmetic, so it costs nothing and cannot be talked into a verdict.

  python3 -m engine.runner journal [--private-out FILE]
"""
from datetime import datetime, timedelta, timezone

from consumer import breakout_forward as bf
from consumer import edgar_events as ee
from consumer import news_veto_study as nv
from consumer import oversold_forward as of

STRATEGIES = (("S-042", of.TAG), ("S-048 B55M", bf.TAG))
MIN_FOR_FLAGS = 15          # below this the journal says "too few to judge", the same line the protocols use
BETA_GAP_PP = 1.0           # raw minus market-adjusted mean larger than this: the result is mostly the market


def spy_return(spy, entry, exit_):
    """Percent move of SPY from the close of the entry day to the close of the exit day (the entry is at about 10:00 ET, so this is an approximation, stated as such)."""
    if not spy or entry not in spy or exit_ not in spy or not entry or not exit_:
        return None
    return 100 * (spy[exit_] / spy[entry] - 1)


def settle(trades, spy):
    rows = []
    for t in trades:
        m = spy_return(spy, t.get("entry_date"), t.get("exit_date"))
        rows.append({**t, "spy_ret_pct": None if m is None else round(m, 3), "adj_ret_pct": None if m is None else round(t["ret_pct"] - m, 3)})
    return rows


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def review(rows):
    """Aggregates and flags for one strategy. No symbols."""
    n = len(rows)
    out = {"closed": n}
    if not n:
        out["flags"] = ["no closed trades yet"]
        return out
    stops = [r for r in rows if r["how"] == "STOP"]
    rules = [r for r in rows if r["how"] != "STOP"]
    out.update({"win_rate_pct": round(100 * sum(1 for r in rows if r["ret_pct"] > 0) / n, 1), "mean_ret_pct": mean([r["ret_pct"] for r in rows]),
                "mean_adj_ret_pct": mean([r["adj_ret_pct"] for r in rows]), "stop_share_pct": round(100 * len(stops) / n, 1),
                "mean_ret_stop_exits_pct": mean([r["ret_pct"] for r in stops]), "mean_ret_rule_exits_pct": mean([r["ret_pct"] for r in rules]),
                "mean_entry_gap_bps": mean([r.get("entry_gap_bps") for r in rows]), "worst_ret_pct": min(r["ret_pct"] for r in rows)})
    flags = []
    if n < MIN_FOR_FLAGS:
        flags.append(f"only {n} closed trades: too few to judge anything")
    else:
        if out["mean_adj_ret_pct"] is not None and out["mean_ret_pct"] - out["mean_adj_ret_pct"] > BETA_GAP_PP:
            flags.append("the raw result is mostly the market (SPY) moving, not the strategy")
        if out["mean_adj_ret_pct"] is not None and out["mean_ret_pct"] > 0 >= out["mean_adj_ret_pct"]:
            flags.append("positive raw, not positive against SPY")
        if out["stop_share_pct"] >= 40:
            flags.append(f"{out['stop_share_pct']}% of trades end at the stop")
        if stops and rules and out["mean_ret_rule_exits_pct"] is not None and out["mean_ret_rule_exits_pct"] < 0:
            flags.append("trades that reach the rule exit are also negative on average")
        if not flags:
            flags.append("nothing unusual in the record so far")
    out["flags"] = flags
    return out


def attach_filing_flags(rows, client, calendar):
    """S-053 forward record. For each closed trade, was a qualifying 8-K accepted by the SEC between the close before the signal day and 09:50 ET on the entry date?
    Computed after the fact from the SEC's exact acceptance times (point in time), so nothing has to be logged at entry. Adds v1 and v2 (True, False or None when the stock
    cannot be mapped to an SEC company or the window cannot be built). Read only; an SEC failure leaves the flags as None and never breaks the journal."""
    try:
        tk = client.ticker_map()
    except Exception:
        return rows
    cache = {}
    for r in rows:
        r["v1"] = r["v2"] = None
        try:
            cik = ee.lookup(tk, r["sym"])
            win = nv.news_window(r.get("entry_date") or "", calendar)
            if cik is None or not win:
                continue
            if cik not in cache:
                cache[cik] = client.filings(cik)
            r["v1"], r["v2"] = ee.flags(ee.in_window(cache[cik], *win))
        except Exception:
            continue
    return rows


def filing_summary(rows):
    """Counts and means of FILING vs NO_FILING closed trades (V2, the event-item variant). No symbols."""
    known = [r for r in rows if r.get("v2") is not None]
    out = {"closed": len(rows), "mapped": len(known)}
    if not known:
        return out
    yes, no = [r for r in known if r["v2"]], [r for r in known if not r["v2"]]
    out.update({"flagged": len(yes), "flagged_share_pct": round(100 * len(yes) / len(known), 1),
                "mean_ret_filing_pct": mean([r["ret_pct"] for r in yes]), "mean_ret_no_filing_pct": mean([r["ret_pct"] for r in no]),
                "note": "too few flagged trades to judge" if len(yes) < 30 else "descriptive only; the S-053 bar needs far more trades"})
    return out


def build(call, now=None, edgar_client=None):
    now = now or datetime.now(timezone.utc)
    per, allrows = {}, {}
    spy = {}
    try:
        data = of.fetch_daily(call, ["SPY"], (now - timedelta(days=120)).date(), now - timedelta(minutes=20))
        spy = dict(zip(data["SPY"]["date"], data["SPY"]["c"]))
    except Exception:
        spy = {}
    for label, tag in STRATEGIES:
        trades, _ = of.closed_trades(call, tag)
        rows = settle(trades, spy)
        per[label], allrows[label] = review(rows), rows
        if label == "S-042" and edgar_client is not None and spy:
            attach_filing_flags(rows, edgar_client, sorted(spy))
            per[label]["filing_record"] = filing_summary(rows)
    return {"asof": now.date().isoformat(), "strategies": per, "rows": allrows}


def _p(x):
    return "n/a" if x is None else f"{x}%"


def render(j):
    lines = [f"## Decision journal {j['asof']} (counts and percentages; paper account)", ""]
    for label, r in j["strategies"].items():
        if not r["closed"]:
            lines.append(f"- **{label}**: no closed trades yet.")
            continue
        lines.append(f"- **{label}**: {r['closed']} closed, win rate {r['win_rate_pct']}%, mean {_p(r['mean_ret_pct'])} (against SPY {_p(r['mean_adj_ret_pct'])}), stop exits {r['stop_share_pct']}% "
                     f"(mean {_p(r['mean_ret_stop_exits_pct'])}), rule exits mean {_p(r['mean_ret_rule_exits_pct'])}, worst {_p(r['worst_ret_pct'])}. Review: " + "; ".join(r["flags"]) + ".")
    f = j["strategies"].get("S-042", {}).get("filing_record")
    if f and f.get("closed"):
        if f.get("mapped"):
            lines.append(f"- **S-042 filing record (S-053 forward data)**: {f['mapped']} of {f['closed']} closed trades matched to an SEC company; {f['flagged_share_pct']}% had an event 8-K in the window "
                         f"({f['flagged']} trades); mean {_p(f['mean_ret_filing_pct'])} with a filing, {_p(f['mean_ret_no_filing_pct'])} without. {f['note']}.")
        else:
            lines.append(f"- **S-042 filing record (S-053 forward data)**: 0 of {f['closed']} closed trades could be matched yet.")
    lines.append("")
    return "\n".join(lines)
