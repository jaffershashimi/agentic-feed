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


if __name__ == "__main__":
    unittest.main()
