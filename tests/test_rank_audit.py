import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from consumer import rank_audit as ra

HEADER = ["event_id", "mode", "arm", "trade_id", "kind", "occurred_at", "quote_time", "symbol", "instrument", "side", "qty",
          "price", "ref_price", "multiplier", "fees", "policy_hash", "decision_id", "score", "source"]


def row(**kw):
    base = dict.fromkeys(HEADER, "")
    base.update(mode="REPLAY", arm="REPLAY-B-R2-SPY", instrument="equity", multiplier="1", policy_hash="p", decision_id="d",
                source="REPLAY_R2;R2_x;ENGINE")
    base.update(kw)
    return base


def trade(rows, tid, day, score, entry, exit_price=None, symbol="SPY", exit_source="REPLAY_R2;R2_x;EXIT_STOP", qty="10",
          hour="14:30:00", rule="R2", arm_letter="B", instrument="equity", mode="REPLAY"):
    arm = f"REPLAY-{arm_letter}-{rule}-{symbol}"
    src = f"REPLAY_{rule};{rule}_x;ENGINE"
    rows.append(row(event_id=tid + "-E", trade_id=tid, kind="entry", occurred_at=f"{day}T{hour}+00:00", symbol=symbol, side="buy",
                    qty=qty, price=str(entry), fees="0", score=str(score), arm=arm, source=src, instrument=instrument, mode=mode))
    rows.append(row(event_id=tid + "-S", trade_id=tid, kind="stop_set", occurred_at=f"{day}T{hour}+00:00", symbol=symbol, side="sell",
                    qty=qty, price=str(entry * 0.8), score=str(score), arm=arm, source=src, instrument=instrument, mode=mode))
    if exit_price is not None:
        rows.append(row(event_id=tid + "-X", trade_id=tid, kind="exit", occurred_at=f"{day}T20:00:00+00:00", symbol=symbol, side="sell",
                        qty=qty, price=str(exit_price), score=str(score), arm=arm, source=exit_source, instrument=instrument, mode=mode))


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.path = Path(self._td.name) / "ledger.csv"

    def load(self, rows, **kw):
        write(self.path, rows)
        return ra.load_trades(self.path, **kw)

    def test_cohort_filters_rule_arm_instrument_mode_and_dates(self):
        rows = []
        trade(rows, "R2B-A-1", "2026-11-03", 7, 100, 101)                       # in
        trade(rows, "R2B-A-2", "2026-10-30", 7, 100, 101)                       # before the window
        trade(rows, "R3B-A-3", "2026-11-03", 7, 100, 101, rule="R3")            # other rule set
        trade(rows, "R2A-A-4", "2026-11-03", 7, 100, 101, arm_letter="A")       # option arm
        trade(rows, "R2B-A-5", "2026-11-03", 7, 100, 101, instrument="option")  # not equity
        trade(rows, "PB-A-6", "2026-11-03", 7, 100, 101, mode="PAPER")          # paper
        got = self.load(rows, since=date(2026, 11, 2))
        self.assertEqual([t["trade_id"] for t in got], ["R2B-A-1"])

    def test_pacific_date_of_a_utc_stamp_and_entry_time(self):
        rows = []
        trade(rows, "R2B-A-1", "2026-11-03", 7, 100, 101, hour="06:30:00")  # 06:30Z is 22:30 PT on Nov 2 (UTC-8)
        got = self.load(rows)
        self.assertEqual((got[0]["session"], got[0]["entry_time"]), ("2026-11-02", "22:30"))

    def test_status_and_returns(self):
        rows = []
        trade(rows, "R2B-A-1", "2026-11-03", 8, 100, 110)                                    # +10 percent
        trade(rows, "R2B-A-2", "2026-11-03", 8, 100)                                         # open
        trade(rows, "R2B-A-3", "2026-11-03", 8, 100, 100, exit_source="REPLAY_R2;EXIT_UNRESOLVED_CLOSED")
        got = {t["trade_id"]: t for t in self.load(rows)}
        self.assertEqual(got["R2B-A-1"]["status"], "CLOSED")
        self.assertAlmostEqual(got["R2B-A-1"]["ret_pct"], 10.0)
        self.assertEqual(got["R2B-A-1"]["net_usd"], 100.0)
        self.assertEqual((got["R2B-A-2"]["status"], got["R2B-A-3"]["status"]), ("OPEN", "UNRESOLVED_CLOSED"))
        self.assertNotIn("ret_pct", got["R2B-A-3"])  # a close-out has no real return and is never averaged


class MathTests(unittest.TestCase):
    def test_trimmed_and_winsorized_means(self):
        xs = list(range(1, 11)) + [1000]  # 11 values: 10 percent trims one from each end
        self.assertAlmostEqual(ra.trimmed_mean(xs), sum(range(2, 11)) / 9)
        self.assertAlmostEqual(ra.winsorized_mean(xs), (2 + sum(range(2, 11)) + 10) / 11)
        self.assertEqual(ra.trimmed_mean([1, 2, 3]), 2)  # too few to trim

    def test_spearman_with_ties_matches_a_known_value(self):
        self.assertAlmostEqual(ra.spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]), 1.0)
        self.assertAlmostEqual(ra.spearman([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]), -1.0)
        self.assertAlmostEqual(ra.spearman([1, 1, 2, 2], [1, 2, 1, 2]), 0.0)
        self.assertIsNone(ra.spearman([1, 1, 1], [1, 2, 3]))  # no variation in the score: no rank information
        self.assertEqual(ra.ranks([5, 5, 1]), [2.5, 2.5, 1.0])

    def test_bucket_stats_concentration_and_leave_one_out(self):
        ts = [{"session": f"s{i}", "symbol": f"X{i}", "ret_pct": r, "net_usd": r * 10, "bucket": "8"}
              for i, r in enumerate([10.0, -1.0, -1.0, -1.0])]
        s = ra.bucket_stats(ts)
        self.assertEqual((s["trades"], s["sessions"], s["symbols"]), (4, 4, 4))
        self.assertAlmostEqual(s["mean_pct"], 1.75)
        self.assertAlmostEqual(s["median_pct"], -1.0)
        self.assertAlmostEqual(s["top_share_of_sum"]["top1"], 10 / 7, places=3)  # one trade carries more than the whole sum
        self.assertTrue(s["leave_one_trade_out_mean"]["min"] < 0 < s["leave_one_trade_out_mean"]["max"])
        self.assertFalse(s["leave_one_session_out_mean"]["sign_stable"])

    def test_empty_bucket_is_just_zero(self):
        self.assertEqual(ra.bucket_stats([]), {"trades": 0, "sessions": 0, "symbols": 0})


class AuditTests(unittest.TestCase):
    def build(self):
        rows = []
        for i in range(6):
            trade(rows, f"R2B-S{i}-1", f"2026-11-0{i + 2 if i < 4 else i + 4}", 7, 100, 99 + i * 0.5, symbol=f"S{i}")
        trade(rows, "R2B-HI-1", "2026-11-03", 9, 100, 120, symbol="HI")
        trade(rows, "R2B-HI-2", "2026-11-04", 9, 100)                                                  # open
        trade(rows, "R2B-HI-3", "2026-11-05", 10, 100, 100, exit_source="REPLAY_R2;EXIT_UNRESOLVED_CLOSED", symbol="HI3")
        return rows

    def test_audit_reports_completeness_and_the_sign_flip_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "l.csv"
            write(p, self.build())
            trades = ra.load_trades(p)
            rep = ra.audit(trades, "x")
        self.assertEqual(rep["status_counts"], {"CLOSED": 7, "OPEN": 1, "UNRESOLVED_CLOSED": 1})
        top = rep["completeness_by_bucket"]["9-10"]
        self.assertEqual((top["entries"], top["closed"], top["open"], top["unresolved_closed"], top["closure_share"]), (3, 1, 1, 1, 0.333))
        flip = rep["sign_flip"]
        self.assertEqual(flip["incomplete_entries"], 2)
        total = sum(t["ret_pct"] for t in trades if t["status"] == "CLOSED")
        self.assertAlmostEqual(flip["avg_return_incomplete_needed_to_flip_pct"], round(-total / 2, 4), places=3)

    def test_bootstrap_is_deterministic_and_session_clustered(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "l.csv"
            write(p, self.build())
            a = ra.audit(ra.load_trades(p), "x")
            b = ra.audit(ra.load_trades(p), "x")
        self.assertEqual(a["spearman"], b["spearman"])

    def test_trades_with_no_score_are_kept_out_of_buckets_not_dropped_from_the_cohort(self):
        rows = []
        trade(rows, "R2B-A-1", "2026-11-03", 5, 100, 101)  # score 5 is below every bucket
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "l.csv"
            write(p, rows)
            rep = ra.audit(ra.load_trades(p), "x")
        self.assertEqual((rep["cohort_entries"], rep["buckets"]["6"]["trades"]), (1, 0))

    def test_cli_writes_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, j = Path(tmp) / "l.csv", Path(tmp) / "out.json"
            write(p, self.build())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(ra.main([str(p), "--since", "2026-11-02", "--json", str(j)]), 0)
            self.assertIn("| 9-10 |", buf.getvalue())
            self.assertEqual(json.loads(j.read_text())["cohort"]["since"], "2026-11-02")


if __name__ == "__main__":
    unittest.main()
