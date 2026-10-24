import json
import math
import unittest

from feed_pipeline import risksim as rs

BASE = dict(size=0.10, stop=0.15, target=0.30, trades_per_day=2, stop_after_first_loss=False, brake=None)


class RiskSimTest(unittest.TestCase):
    def test_validation(self):
        for bad in [dict(size=0), dict(size=1.5), dict(stop=float("nan")), dict(trades_per_day=0), dict(brake=1.0), dict(gap_prob=2), dict(cost=-0.01)]:
            with self.assertRaises(ValueError):
                rs.simulate(rs.Rules(**dict(BASE, **bad)), 0.5, days=5, paths=10)
        with self.assertRaises(ValueError):
            rs.simulate(rs.Rules(**BASE), 1.5, days=5, paths=10)

    def test_kelly_and_breakeven_closed_forms(self):
        self.assertAlmostEqual(rs.kelly_fraction(0.6, 1.0, 1.0), 0.2)  # p - q for even odds
        self.assertAlmostEqual(rs.kelly_fraction(0.5, 0.30, 0.15), 0.5 / 0.15 - 0.5 / 0.30)
        self.assertEqual(rs.kelly_fraction(0.2, 0.30, 0.15), 0.0)
        r = rs.Rules(**dict(BASE, gap_prob=0.05, gap_loss=1.0, cost=0.02))
        p = rs.breakeven_win_prob(r)
        self.assertAlmostEqual(p, (0.95 * 0.17 + 0.05 * 1.02) / (0.28 + 0.95 * 0.17 + 0.05 * 1.02))
        self.assertAlmostEqual(rs.simulate(r, p, days=1, paths=2)["expected_return_per_trade_pct"], 0.0, places=6)

    def test_certain_wins_compound_exactly(self):
        out = rs.simulate(rs.Rules(**BASE), 1.0, days=10, paths=5)
        self.assertAlmostEqual(out["terminal_equity"]["p50"], round((1 + 0.10 * 0.30) ** 20, 4))
        self.assertEqual((out["p_brake_within_horizon"], out["max_drawdown_pct"]["p99"]), (0.0, 0.0))

    def test_mean_terminal_equity_matches_the_analytic_value(self):
        p, days, paths = 0.45, 20, 6000
        r = rs.Rules(**BASE)
        # The simulator reports percentiles, so check the mean with the same per-trade model run directly.
        import random
        rng, vals = random.Random(1), []
        for _ in range(paths):
            e = 1.0
            for _ in range(days * 2):
                e *= 1 + 0.03 if rng.random() < p else 1 - 0.015
            vals.append(e)
        mean = sum(vals) / paths
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (paths - 1))
        analytic = (1 + 0.10 * (p * 0.30 - (1 - p) * 0.15)) ** (days * 2)
        self.assertLess(abs(mean - analytic), 4 * sd / math.sqrt(paths))
        median = rs.simulate(r, p, days, paths, seed=1)["terminal_equity"]["p50"]
        self.assertLess(abs(median - sorted(vals)[paths // 2]), 0.05, "same model, independent draws: medians agree")

    def test_first_day_brake_matches_the_closed_form(self):
        gap = dict(stop_after_first_loss=True, brake=0.15, gap_prob=0.05, gap_loss=1.0, cost=0.02)
        small = rs.simulate(rs.Rules(**dict(BASE, **gap)), 0.4, days=1, paths=20000, seed=3)
        self.assertEqual(small["p_brake_first_day"], 0.0, "10% size: the worst day loses 10.2%, under the 15% brake")
        big = rs.simulate(rs.Rules(**dict(BASE, size=0.15, **gap)), 0.4, days=1, paths=20000, seed=3)
        expected = 0.6 * 0.05 * (1 + 0.4)  # gap on trade one, or a win then a gap on trade two
        self.assertAlmostEqual(big["p_brake_first_day"], expected, delta=4 * math.sqrt(expected * (1 - expected) / 20000))
        self.assertEqual(big["worst_single_trade_loss_pct"], 15.3)

    def test_seeded_and_report_is_public_safe(self):
        r = rs.Rules(**dict(BASE, brake=0.15))
        self.assertEqual(rs.simulate(r, 0.4, 30, 200, seed=9), rs.simulate(r, 0.4, 30, 200, seed=9))
        rep = rs.report(paths=300, days=60)
        text = json.dumps(rep)
        self.assertEqual(rep["schema"], "risk-sim/1")
        for word in ("balance", "total_value", "positions", "account"):
            self.assertNotIn(word, text)
        self.assertEqual(len(rep["rule_sets"]["size_score_8"]["results"]), 3)


if __name__ == "__main__":
    unittest.main()
