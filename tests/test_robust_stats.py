import math
import random
import unittest

from consumer import robust_stats as rs


def gauss(n, mu, sd, seed):
    r = random.Random(seed)
    return [r.gauss(mu, sd) for _ in range(n)]


class MomentTests(unittest.TestCase):
    def test_sharpe_and_moments_known_values(self):
        xs = [0.01, 0.02, -0.01, 0.03, 0.0, 0.02]
        mo = rs.moments(xs)
        self.assertAlmostEqual(mo["mean"], 0.01166666, places=6)
        self.assertAlmostEqual(rs.sharpe(xs), mo["mean"] / mo["sd"])
        self.assertAlmostEqual(rs.sharpe(xs, 252), rs.sharpe(xs) * math.sqrt(252))
        with self.assertRaises(ValueError):
            rs.moments([1, 1, 1, 1])
        with self.assertRaises(ValueError):
            rs.moments([1, 2])


class PsrDsrTests(unittest.TestCase):
    def test_psr_matches_the_formula_and_orders_sensibly(self):
        xs = gauss(500, 0.001, 0.01, 1)
        mo = rs.moments(xs)
        sr = mo["mean"] / mo["sd"]
        manual = rs.N.cdf(sr * math.sqrt(499) / math.sqrt(1 - mo["skew"] * sr + (mo["kurt"] - 1) / 4 * sr ** 2))
        self.assertAlmostEqual(rs.psr(xs), manual)
        self.assertGreater(rs.psr(xs, -0.1), rs.psr(xs, 0.0))
        self.assertGreater(rs.psr(gauss(2000, 0.001, 0.01, 1)), rs.psr(gauss(200, 0.001, 0.01, 1)))

    def test_dsr_falls_as_the_number_of_trials_grows(self):
        xs = gauss(750, 0.0006, 0.01, 2)
        d1, d5, d50, d500 = (rs.dsr(xs, n) for n in (1, 5, 50, 500))
        self.assertAlmostEqual(d1, rs.psr(xs, 0.0))
        self.assertTrue(d1 > d5 > d50 > d500)
        self.assertAlmostEqual(rs.expected_max_sharpe(1, 0.01), 0.0)
        self.assertGreater(rs.expected_max_sharpe(100, 0.01), rs.expected_max_sharpe(10, 0.01))

    def test_pure_noise_does_not_survive_many_trials(self):
        best = max((gauss(500, 0.0, 0.01, s) for s in range(100)), key=rs.sharpe)
        self.assertLess(rs.dsr(best, 100), 0.95)
        self.assertGreater(rs.psr(best), 0.9)  # looks good on its own: that is the trap

    def test_min_track_record(self):
        good, weak = gauss(2000, 0.002, 0.01, 3), gauss(2000, 0.0003, 0.01, 3)
        self.assertLess(rs.min_track_record(good), rs.min_track_record(weak))
        self.assertEqual(rs.min_track_record(gauss(500, -0.001, 0.01, 4)), float("inf"))


class PboTests(unittest.TestCase):
    def test_noise_averages_about_half_and_a_dominant_strategy_gives_near_zero(self):
        # one noise dataset can land anywhere from about 0.1 to 0.8; the average over datasets is about 0.5
        ps = [rs.pbo_cscv([gauss(640, 0.0, 0.01, 1000 * d + k) for k in range(20)], 16, max_combinations=2000)["pbo"] for d in range(8)]
        self.assertTrue(0.35 < sum(ps) / len(ps) < 0.65)
        self.assertEqual(rs.pbo_cscv([gauss(640, 0.0, 0.01, k) for k in range(20)], 16)["splits"], 12870)
        dominant = [gauss(640, 0.004, 0.01, 100)] + [gauss(640, 0.0, 0.01, 200 + k) for k in range(9)]
        self.assertLess(rs.pbo_cscv(dominant, 16, max_combinations=2000)["pbo"], 0.1)

    def test_input_checks_and_sampling(self):
        with self.assertRaises(ValueError):
            rs.pbo_cscv([[0.1] * 100], 16)
        with self.assertRaises(ValueError):
            rs.pbo_cscv([[0.1] * 100, [0.2] * 100], 15)
        a = rs.pbo_cscv([gauss(320, 0, 0.01, 1), gauss(320, 0, 0.01, 2)], 16, max_combinations=500)
        self.assertEqual(a["splits"], 500)
        self.assertEqual(a, rs.pbo_cscv([gauss(320, 0, 0.01, 1), gauss(320, 0, 0.01, 2)], 16, max_combinations=500))


class BootstrapTests(unittest.TestCase):
    def test_interval_brackets_the_point_estimate_and_is_deterministic(self):
        xs = gauss(400, 0.001, 0.01, 7)
        a = rs.stationary_bootstrap_sharpe(xs, 10, 500)
        self.assertLess(a["lo"], a["sharpe"] + 0.05)
        self.assertGreater(a["hi"], a["sharpe"] - 0.05)
        self.assertEqual(a, rs.stationary_bootstrap_sharpe(xs, 10, 500))
        self.assertLess(a["lo"], a["hi"])


if __name__ == "__main__":
    unittest.main()
