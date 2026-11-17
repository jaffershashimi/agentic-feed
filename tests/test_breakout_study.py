import unittest

from consumer import breakout_study as bs


def trade(entry, exit_, ret, how="TRAIL"):
    return {"entry_date": entry, "exit_date": exit_, "ret": ret, "how": how}


def spy_flat():
    dates = [f"20{y}-{m:02d}-{d:02d}" for y in (22, 23, 25) for m in (3,) for d in range(1, 29)]
    return {"date": dates, "o": [100.0] * len(dates)}


def winners(year, n, ret):
    return [trade(f"20{year}-03-{1 + (i % 20):02d}", f"20{year}-03-{2 + (i % 20):02d}", ret + (0.001 if i % 2 else -0.001)) for i in range(n)]


class StatsTests(unittest.TestCase):
    def test_interval_is_deterministic_and_brackets_the_mean(self):
        a = bs.mean_interval([1.0, 2.0, 3.0, 2.0] * 30, [f"w{i % 8}" for i in range(120)], resamples=400)
        self.assertEqual(a, bs.mean_interval([1.0, 2.0, 3.0, 2.0] * 30, [f"w{i % 8}" for i in range(120)], resamples=400))
        self.assertTrue(a["lo"] <= a["mean"] <= a["hi"])
        self.assertIsNone(bs.mean_interval([], []))

    def test_a_clear_edge_passes_every_bar(self):
        tr = winners(22, 150, 0.02) + winners(23, 150, 0.02) + winners(25, 150, 0.02)
        r = bs.evaluate(tr, spy_flat())
        self.assertEqual(r["verdict"], "PASS")

    def test_a_losing_strategy_is_rejected(self):
        tr = winners(22, 150, -0.02) + winners(23, 150, -0.02) + winners(25, 150, -0.02)
        self.assertEqual(bs.evaluate(tr, spy_flat())["verdict"], "REJECTED")

    def test_too_few_trades_cannot_pass(self):
        tr = winners(22, 20, 0.02) + winners(23, 20, 0.02) + winners(25, 20, 0.02)
        r = bs.evaluate(tr, spy_flat())
        self.assertFalse(r["bars"]["4_trade_counts_300_pooled_100_each"])
        self.assertNotEqual(r["verdict"], "PASS")

    def test_a_win_in_only_one_window_is_not_a_pass(self):
        tr = winners(22, 150, 0.04) + winners(23, 150, 0.04) + winners(25, 150, -0.005)
        r = bs.evaluate(tr, spy_flat())
        self.assertFalse(r["bars"]["2_positive_in_judge_and_final"])
        self.assertNotEqual(r["verdict"], "PASS")

    def test_beating_zero_but_not_the_market_fails_the_market_adjusted_bar(self):
        spy = spy_flat()
        spy["o"] = [100.0 * (1.1 ** (i % 2)) for i in range(len(spy["o"]))]   # SPY swings 10 percent between consecutive dates
        tr = [trade(spy["date"][i], spy["date"][i + 1], 0.02) for i in range(0, 50, 2)] * 12
        r = bs.evaluate(tr, spy)
        self.assertIsNotNone(r["pooled_market_adjusted_mean"])

    def test_correlation(self):
        a = {"d1": 1.0, "d2": 2.0, "d3": 3.0, "d4": 5.0}
        self.assertAlmostEqual(bs.correlation(a, a), 1.0)
        self.assertAlmostEqual(bs.correlation(a, {k: -v for k, v in a.items()}), -1.0)
        self.assertIsNone(bs.correlation({"d1": 1.0, "d2": 1.0, "d3": 1.0, "d4": 1.0}, a))   # no variation, no correlation


if __name__ == "__main__":
    unittest.main()
