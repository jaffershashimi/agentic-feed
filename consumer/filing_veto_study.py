"""S-053 study: does an SEC filing veto help S-042? TEST ONLY. Spec frozen in Agentic-Lab system/06-research/specs/S-053-s042-filing-veto.md (merged before any filing was joined to a return).

For every as-implemented S-042 V3 trade, flag a qualifying 8-K accepted by the SEC in the trade's window and compare the mean return of the trades WITHOUT a filing against the ones WITH one.
Same statistic, windows and bars as S-050 (consumer.news_veto_study), with the news source replaced by SEC EDGAR (consumer.edgar_events) and two coverage conditions added.

  python3 -m consumer.filing_veto_study PANEL_DIR TRADES.json CACHE_DIR OUT_DIR
Output: s053_results.json and s053_results.md (aggregate numbers only; no symbols).
"""
import json
import sys
from pathlib import Path

from consumer import edgar_events as ee
from consumer import news_veto_study as nv
from consumer import oversold_panel as op

MIN_MAPPED = 0.90          # at least 90 percent of the trades must map to an SEC company, or the test is not interpretable
MIN_FLAGGED_POOLED = 60    # at least 60 flagged trades in the pooled Judge+Final window, per variant


def build_rows(trades, client, calendar):
    """One row per mappable trade. Returns rows, count of trades, count mapped."""
    tk = client.ticker_map()
    cache, rows, mapped = {}, [], 0
    for t in trades:
        cik = ee.lookup(tk, t["symbol"])
        win = nv.news_window(t["entry_date"], calendar)
        if cik is None or not win:
            continue
        mapped += 1
        if cik not in cache:
            cache[cik] = client.filings(cik)
        v1, v2 = ee.flags(ee.in_window(cache[cik], *win))
        rows.append({"entry_date": t["entry_date"], "week": op.week_key(t["entry_date"]), "ret_pp": 100.0 * t["ret"], "v1": v1, "v2": v2})
    return rows, len(trades), mapped


def evaluate(rows, n_trades, mapped):
    res = nv.evaluate(rows)
    mapped_share = mapped / n_trades if n_trades else 0.0
    for variant, key in zip(nv.VARIANTS, ("v1", "v2")):
        r = res[variant]
        pooled = r["windows"]["Judge+Final"]
        flagged = pooled["n_news"] if pooled else 0
        r["coverage"] = {"mapped_share": round(mapped_share, 4), "mapped_ok": mapped_share >= MIN_MAPPED, "flagged_pooled": flagged, "flagged_ok": flagged >= MIN_FLAGGED_POOLED}
        if not (r["coverage"]["mapped_ok"] and r["coverage"]["flagged_ok"]):
            r["verdict"] = "BLOCKED"        # not interpretable: reported, never counted as a rejection
    return res


def render(res, n_trades, mapped):
    lines = ["# S-053 results: SEC filing veto for S-042", "",
             f"{n_trades} trades, {mapped} mapped to an SEC company. D is NO_FILING minus FILING, percentage points per trade; 97.5 percent week-clustered interval. Verdicts are from the pre-registered bar.", ""]
    for v, r in res.items():
        name = v.replace("V1_any_informative", "V1_any_substantive_8K").replace("V2_event_types", "V2_event_item_8K")
        lines += [f"## {name}: {r['verdict']}", "", "| Window | Trades | Flagged | Mean FILING | Mean NO_FILING | Mean all | D | 97.5% interval |", "|---|---|---|---|---|---|---|---|"]
        for w, x in r["windows"].items():
            if x:
                lines.append(f"| {w} | {x['n_news'] + x['n_no_news']} | {x['share_flagged']:.1%} | {x['mean_news']:+.2f} | {x['mean_no_news']:+.2f} | {x['mean_all']:+.2f} | {x['d']:+.2f} | {x['lo']:+.2f} to {x['hi']:+.2f} |")
            else:
                lines.append(f"| {w} | n/a | n/a | n/a | n/a | n/a | n/a | one group is empty |")
        c = r["coverage"]
        lines += ["", "Bars: " + "; ".join(f"{k}: {'met' if ok else 'MISSED'}" for k, ok in r["bars"].items()),
                  f"Coverage: mapped {c['mapped_share']:.1%} ({'met' if c['mapped_ok'] else 'MISSED'}), flagged in Judge+Final {c['flagged_pooled']} ({'met' if c['flagged_ok'] else 'MISSED'})", ""]
    return "\n".join(lines)


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 4:
        print(__doc__)
        return 2
    panel, trades_path, cache, out = Path(a[0]), Path(a[1]), Path(a[2]), Path(a[3])
    out.mkdir(parents=True, exist_ok=True)
    data, _, digest = op.load_panel(panel)
    calendar = sorted(data["SPY"]["date"])
    trades = json.loads(trades_path.read_text())
    rows, n, mapped = build_rows(trades, ee.Client(cache), calendar)
    res = evaluate(rows, n, mapped)
    (out / "s053_results.json").write_text(json.dumps({"panel_sha256": digest, "trades": n, "mapped": mapped, "results": res}, indent=1))
    text = render(res, n, mapped)
    (out / "s053_results.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
