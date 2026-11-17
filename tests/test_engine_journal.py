import unittest

from engine import journal


def tr(ret, how="RULE_EXIT", entry="2026-11-02", exit_="2026-11-06", gap=20.0):
    return {"sym": "AAA", "ret_pct": ret, "how": how, "pnl": ret * 10, "entry_date": entry, "exit_date": exit_, "entry_gap_bps": gap}


SPY = {"2026-11-02": 100.0, "2026-11-06": 103.0}


class JournalTests(unittest.TestCase):
    def test_settle_marks_each_trade_against_spy_over_the_same_days(self):
        rows = journal.settle([tr(5.0)], SPY)
        self.assertAlmostEqual(rows[0]["spy_ret_pct"], 3.0, places=2)
        self.assertAlmostEqual(rows[0]["adj_ret_pct"], 2.0, places=2)
        self.assertIsNone(journal.settle([tr(5.0, entry="2026-10-01")], SPY)[0]["adj_ret_pct"])

    def test_too_few_trades_say_so_and_make_no_other_claim(self):
        r = journal.review(journal.settle([tr(5.0)] * 3, SPY))
        self.assertEqual(r["closed"], 3)
        self.assertEqual(len(r["flags"]), 1)
        self.assertIn("too few", r["flags"][0])

    def test_flags_for_market_driven_results_and_heavy_stop_exits(self):
        rows = journal.settle([tr(3.2, "STOP")] * 8 + [tr(-2.0, "RULE_EXIT")] * 8, SPY)    # raw mean 0.6, SPY 3.0 each: against SPY the mean is -2.4
        r = journal.review(rows)
        self.assertTrue(any("against SPY" in f for f in r["flags"]))
        self.assertTrue(any("end at the stop" in f for f in r["flags"]))
        self.assertTrue(any("rule exit" in f for f in r["flags"]))
        self.assertEqual(r["stop_share_pct"], 50.0)

    def test_empty_and_rendering_has_no_symbols(self):
        self.assertEqual(journal.review([])["flags"], ["no closed trades yet"])
        j = {"asof": "2026-11-20", "strategies": {"S-042": journal.review(journal.settle([tr(1.0)] * 20, SPY)), "S-048 B55M": journal.review([])}, "rows": {}}
        text = journal.render(j)
        self.assertNotIn("AAA", text)
        self.assertIn("S-048 B55M**: no closed trades yet", text)


class FilingRecordTests(unittest.TestCase):
    def client(self):
        import tempfile
        from tests.test_edgar_events import FakeOpen, TICKERS, sub
        from consumer import edgar_events as ee
        table = {"company_tickers.json": TICKERS, "CIK0000320193.json": sub(["8-K"], ["2.02"], ["2026-11-12T12:00:00.000Z"])}
        return ee.Client(tempfile.mkdtemp(), opener=FakeOpen(table), sleep=lambda s: None)

    def test_flags_are_computed_from_the_exact_acceptance_time_and_unmapped_stay_unknown(self):
        rows = [tr(1.0, entry="2026-11-13"), dict(tr(2.0, entry="2026-11-13"), sym="NOPE")]
        rows[0]["sym"] = "AAPL"
        cal = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16"]
        journal.attach_filing_flags(rows, self.client(), cal)
        self.assertEqual((rows[0]["v1"], rows[0]["v2"]), (True, True))
        self.assertEqual((rows[1]["v1"], rows[1]["v2"]), (None, None))

    def test_summary_has_counts_and_means_only_and_says_when_too_few(self):
        rows = [{"ret_pct": 3.0, "v2": True}, {"ret_pct": 1.0, "v2": False}, {"ret_pct": 2.0, "v2": False}, {"ret_pct": 9.0, "v2": None}]
        s = journal.filing_summary(rows)
        self.assertEqual((s["closed"], s["mapped"], s["flagged"]), (4, 3, 1))
        self.assertEqual((s["mean_ret_filing_pct"], s["mean_ret_no_filing_pct"]), (3.0, 1.5))
        self.assertIn("too few", s["note"])
        self.assertEqual(journal.filing_summary([{"ret_pct": 1.0, "v2": None}]), {"closed": 1, "mapped": 0})

    def test_a_dead_sec_connection_never_breaks_the_journal(self):
        class Dead:
            def ticker_map(self):
                raise RuntimeError("blocked")
        rows = [tr(1.0)]
        self.assertEqual(journal.attach_filing_flags(rows, Dead(), ["2026-11-02"]), rows)

    def test_render_includes_the_record_without_symbols(self):
        j = {"asof": "2026-11-20", "strategies": {"S-042": dict(journal.review([]), filing_record={"closed": 20, "mapped": 18, "flagged": 3, "flagged_share_pct": 16.7,
                                                                                               "mean_ret_filing_pct": 1.0, "mean_ret_no_filing_pct": 2.0, "note": "too few flagged trades to judge"})}, "rows": {}}
        text = journal.render(j)
        self.assertIn("S-042 filing record", text)
        self.assertIn("18 of 20", text)


if __name__ == "__main__":
    unittest.main()
