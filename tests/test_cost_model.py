import unittest

from consumer import cost_model as cm


class CostTests(unittest.TestCase):
    def test_impact_scales_with_the_square_root_of_participation(self):
        a = cm.sqrt_impact_bps(1_000, 100_000_000, 0.03)
        b = cm.sqrt_impact_bps(4_000, 100_000_000, 0.03)
        self.assertAlmostEqual(b / a, 2.0)
        self.assertAlmostEqual(cm.sqrt_impact_bps(1_000, 10_000_000, 0.02), 0.02 * (1e-4 ** 0.5) * 1e4)

    def test_cost_is_spread_plus_impact_and_cheaper_names_cost_more(self):
        liquid = cm.expected_cost_bps(120, 1000, 5e8, 0.02)
        thin = cm.expected_cost_bps(6, 1000, 1.2e7, 0.04)
        self.assertGreater(thin, liquid)
        self.assertGreater(liquid, cm.SPREAD_FLOOR_BPS)

    def test_realized_slippage_signs(self):
        self.assertAlmostEqual(cm.realized_slippage_bps("buy", 100.0, 100.10), 10.0)
        self.assertAlmostEqual(cm.realized_slippage_bps("sell", 100.0, 99.90), 10.0)
        self.assertLess(cm.realized_slippage_bps("buy", 100.0, 99.90), 0)

    def test_inputs_are_checked(self):
        for call in (lambda: cm.sqrt_impact_bps(0, 1e6, 0.02), lambda: cm.realized_slippage_bps("buy", 0, 1), lambda: cm.daily_vol([1, 2, 3])):
            with self.assertRaises(ValueError):
                call()

    def test_daily_vol_and_summary(self):
        closes = [100 * (1.01 if i % 2 else 0.99) ** 1 for i in range(30)]
        self.assertGreater(cm.daily_vol(closes), 0.01)
        self.assertEqual(cm.summarize([]), {"n": 0})
        self.assertEqual(cm.summarize([1, 3, 2])["median_bps"], 2)


if __name__ == "__main__":
    unittest.main()
