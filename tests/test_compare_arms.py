import csv
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta

from consumer.compare_arms import analyze, holm, main, parse_arm
from consumer.paper_ledger import PT, load
from consumer.replay_r2 import LEDGER_FIELDS

# Synthetic trades only: invented symbols and prices, not market data.
START = date(2026, 11, 2)


def days(n):
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def trade(rule, side, sym, day, clock, net, instrument="equity", closed=True, fee="0"):
    """Rows for one long trade of 10 units at 10.00 that closes (or stays open) for the given net."""
    at = datetime.combine(day, clock, PT)
    arm = f"REPLAY-{side}-{rule}-{sym}" if rule != "R1" else f"REPLAY-{side}-{sym}"
    tid = f"{rule}{side}-{sym}-{day:%Y%m%d}-{clock:%H%M}"
    mult = "100" if instrument == "option" else "1"
    qty, price = ("1", "1.00") if instrument == "option" else ("10", "10.00")
    exit_price = (float(price) * float(mult) * float(qty) + net) / (float(mult) * float(qty))
    base = dict(mode="REPLAY", arm=arm, trade_id=tid, symbol=sym, instrument=instrument, multiplier=mult, qty=qty,
                policy_hash="demo", decision_id=tid, score="7", source="TEST")
    rows = [dict(base, event_id=tid + "-E", kind="entry", side="buy", occurred_at=at.isoformat(),
                 quote_time=at.isoformat(), price=price, ref_price=price, fees=fee),
            dict(base, event_id=tid + "-S", kind="stop_set", side="sell", occurred_at=at.isoformat(), quote_time="",
                 price="8.00" if instrument == "equity" else "0.85", ref_price="", fees="")]
    if closed:
        out = (at + timedelta(hours=2)).isoformat()
        rows.append(dict(base, event_id=tid + "-X", kind="exit", side="sell", occurred_at=out, quote_time=out,
                         price=f"{exit_price:.4f}", ref_price=f"{exit_price:.4f}", fees=fee))
    return rows


def ledger(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LEDGER_FIELDS)
    w.writeheader()
    w.writerows(rows)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as fh:
        fh.write(buf.getvalue())
    loaded, errors = load(fh.name)
    assert errors == [], errors
    return loaded, fh.name


def by_group(out):
    return {r["group"]: r for r in out["paired"]["results"]}


class CompareArmsTests(unittest.TestCase):
    def test_parse_arm(self):
        self.assertEqual(parse_arm("REPLAY-A-R2-NVDA"), ("A", "R2", "NVDA"))
        self.assertEqual(parse_arm("REPLAY-B-R3-BRK.B"), ("B", "R3", "BRK.B"))
        self.assertEqual(parse_arm("REPLAY-B-NVDA"), ("B", "R1", "NVDA"))
        self.assertEqual(parse_arm("REPLAY-D-R2-SPY"), ("D", "R2", "SPY"))
        self.assertIsNone(parse_arm("A"))

    def test_paired_difference_detects_a_consistent_improvement(self):
        rows = []
        for i, d in enumerate(days(12)):
            for j, sym in enumerate(("AAA", "BBB", "CCC")):
                noise = (i * 7 + j * 3) % 11 - 5
                rows += trade("R2", "B", sym, d, time(7, 0), noise)
                rows += trade("R3", "B", sym, d, time(7, 0), noise + 4)
        out = analyze(ledger(rows)[0], resamples=500)
        row = by_group(out)["all"]
        self.assertEqual(out["paired"]["counts"], {"both_traded": 36})
        self.assertEqual((row["pairs"], row["sessions"]), (36, 12))
        self.assertAlmostEqual(row["mean_diff_usd"], 4.0, places=3)
        self.assertEqual(row["verdict"], "test_better")

    def test_skip_counts_as_cash_and_unknown_pairs_are_left_out(self):
        d1, d2 = days(2)
        rows = trade("R2", "A", "AAA", d1, time(7, 0), -50, "option") + trade("R3", "A", "BBB", d1, time(7, 0), 20, "option")
        rows += trade("R2", "A", "BBB", d1, time(7, 0), 10, "option")  # both traded BBB: +10 more under R3
        rows += trade("R2", "A", "CCC", d2, time(8, 0), 5, "option") + trade("R3", "A", "CCC", d2, time(8, 0), 5, "option", closed=False)
        rows += trade("R3", "A", "DDD", d2, time(8, 0), 7, "option", fee="")  # unknown fee: unknown pair
        rows += trade("R2", "A", "EEE", d2, time(9, 0), 1, "option")
        out = analyze(ledger(rows)[0], resamples=200)
        self.assertEqual(out["paired"]["counts"], {"R2_only": 2, "both_traded": 1, "unknown": 2})
        row = by_group(out)["option"]
        self.assertEqual(row["pairs"], 3)  # AAA: 0 - (-50) = +50; BBB: 20 - 10 = +10; EEE: 0 - 1 = -1
        self.assertAlmostEqual(row["mean_diff_usd"], 59 / 3, places=3)
        self.assertEqual(row["verdict"], "insufficient_evidence")

    def test_sessions_file_limits_pairs_to_dates_both_rule_sets_replayed(self):
        d1, d2 = days(2)
        rows = trade("R2", "B", "AAA", d1, time(7, 0), 10) + trade("R2", "B", "AAA", d2, time(7, 0), 10)
        rows += trade("R3", "B", "AAA", d1, time(7, 0), 12)
        covered = {("R2", d1.isoformat(), "AAA"), ("R3", d1.isoformat(), "AAA"), ("R2", d2.isoformat(), "AAA")}
        out = analyze(ledger(rows)[0], sessions=covered, resamples=100)
        self.assertEqual(out["paired"]["counts"], {"both_traded": 1, "not_covered": 1})
        out = analyze(ledger(rows)[0], resamples=100)  # without the file: d2 has no R3 trade, so it is not covered
        self.assertEqual(out["paired"]["counts"], {"both_traded": 1, "not_covered": 1})

    def test_tag_splits_and_holm(self):
        rows, tags = [], {}
        for i, d in enumerate(days(12)):
            for j, (sym, tag, net) in enumerate((("AAA", "YES", -30), ("BBB", "NO", 10), ("CCC", "NO", 12))):
                rows += trade("R2", "A", sym, d, time(7, 0), net + (i % 3) - 1, "option")
                tid = f"R2A-{sym}-{d:%Y%m%d}-0700"
                tags[tid] = {"trade_id": tid, "rule_set": "R2", "binary_event": tag,
                             "options_checklist": "PASS" if j else "FAIL"}
        out = analyze(ledger(rows)[0], tags=tags, min_trades=10, resamples=500)
        split = {r["tag"]: r for r in out["tag_splits"]}
        self.assertEqual(set(split), {"binary_event", "options_checklist"})
        self.assertEqual((split["binary_event"]["yes_trades"], split["binary_event"]["no_trades"]), (12, 24))
        self.assertAlmostEqual(split["binary_event"]["difference"], -41.0, places=3)
        self.assertEqual(split["binary_event"]["verdict"], "lower")
        self.assertEqual(split["options_checklist"]["verdict"], "higher")
        self.assertEqual(out["tests_run"], 3)  # the empty paired "all" row (no R3 trades) and two tag splits
        self.assertEqual(holm([0.01, None, 0.04, 0.03]), [0.03, None, 0.06, 0.06])
        self.assertEqual(holm([0.5, 0.2]), [0.5, 0.4])

    def test_cli(self):
        d1, = days(1)
        _rows, path = ledger(trade("R2", "B", "AAA", d1, time(7, 0), 3) + trade("R3", "B", "AAA", d1, time(7, 0), 5))
        with tempfile.TemporaryDirectory() as tmp:
            sessions = pathlib.Path(tmp) / "replay_sessions.csv"
            sessions.write_text("session_key,session_date,symbol\n"
                                f"R2|{d1}|AAA,{d1},AAA\nR3|{d1}|AAA,{d1},AAA\n", encoding="utf-8")
            tags = pathlib.Path(tmp) / "variant_tags.csv"
            tags.write_text("trade_id,rule_set,binary_event,options_checklist,checklist_detail,source\n"
                            f"R2B-AAA-{d1:%Y%m%d}-0700,R2,NO,NA,,TEST\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([path, "--sessions", str(sessions), "--tags", str(tags), "--resamples", "50"])
            self.assertEqual(code, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["paired"]["counts"], {"both_traded": 1})
            self.assertEqual(out["errors"], [])
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(main([path, "--tags", str(pathlib.Path(tmp) / "missing.csv")]), 2)


if __name__ == "__main__":
    unittest.main()
