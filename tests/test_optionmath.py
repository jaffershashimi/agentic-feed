import math
import random
import unittest

from feed_pipeline import optionmath as om


class BsmTest(unittest.TestCase):
    def test_textbook_values_and_parity(self):
        # Hull's standard example: S=K=100, T=1, r=5%, sigma=20% gives a call of 10.4506 and a put of 5.5735.
        call = om.bsm(100, 100, 1, 0.2, "call", r=0.05)
        put = om.bsm(100, 100, 1, 0.2, "put", r=0.05)
        self.assertAlmostEqual(call["price"], 10.4506, places=4)
        self.assertAlmostEqual(put["price"], 5.5735, places=4)
        self.assertAlmostEqual(call["price"] - put["price"], 100 - 100 * math.exp(-0.05), places=10)
        self.assertAlmostEqual(call["delta"] - put["delta"], 1.0, places=10)
        self.assertAlmostEqual(call["gamma"], put["gamma"], places=12)

    def test_greeks_match_finite_differences(self):
        h = 1e-4
        g = om.bsm(100, 105, 0.25, 0.3, "call", r=0.02, q=0.01)
        up, dn = om.bsm(100 + h, 105, 0.25, 0.3, "call", 0.02, 0.01), om.bsm(100 - h, 105, 0.25, 0.3, "call", 0.02, 0.01)
        self.assertAlmostEqual(g["delta"], (up["price"] - dn["price"]) / (2 * h), places=6)
        self.assertAlmostEqual(g["gamma"], (up["price"] - 2 * g["price"] + dn["price"]) / h ** 2, places=3)
        self.assertAlmostEqual(g["vega"], (om.bsm(100, 105, 0.25, 0.3 + h, "call", 0.02, 0.01)["price"] - om.bsm(100, 105, 0.25, 0.3 - h, "call", 0.02, 0.01)["price"]) / (2 * h), places=5)
        self.assertAlmostEqual(g["theta"], -(om.bsm(100, 105, 0.25 + h, 0.3, "call", 0.02, 0.01)["price"] - om.bsm(100, 105, 0.25 - h, 0.3, "call", 0.02, 0.01)["price"]) / (2 * h), places=4)

    def test_invalid_inputs(self):
        for args in [(0, 100, 1, 0.2), (100, 100, 0, 0.2), (100, 100, 1, float("nan")), (100, -1, 1, 0.2)]:
            with self.assertRaises(ValueError):
                om.bsm(*args)
        with self.assertRaises(ValueError):
            om.bsm(100, 100, 1, 0.2, "straddle")


class ImpliedVolTest(unittest.TestCase):
    def test_round_trip_across_strikes_and_kinds(self):
        rng = random.Random(7)
        for _ in range(200):
            s, k, t, sig = 100, rng.uniform(60, 140), rng.uniform(0.02, 2), rng.uniform(0.05, 1.5)
            kind = rng.choice(["call", "put"])
            g = om.bsm(s, k, t, sig, kind, 0.03)
            if g["price"] < 1e-6:
                continue
            iv = om.implied_vol(g["price"], s, k, t, kind, 0.03)
            band = om.bsm(s, k, t, sig * 1.1, kind, 0.03)["price"] - om.bsm(s, k, t, sig * 0.9, kind, 0.03)["price"]
            if band < 0.005:
                self.assertIsNone(iv, "volatility is not identifiable from this price")
            else:
                self.assertAlmostEqual(iv, sig, places=4)

    def test_unreachable_prices_return_none(self):
        self.assertIsNone(om.implied_vol(4.0, 110, 100, 0.5, "call"))  # below intrinsic 10
        self.assertIsNone(om.implied_vol(150.0, 100, 100, 0.5, "call"))  # above the spot
        self.assertIsNone(om.implied_vol(0.0, 100, 100, 0.5))
        self.assertIsNone(om.implied_vol(float("nan"), 100, 100, 0.5))
        deep = om.bsm(100, 66.8, 1.558, 0.052, "call", 0.03)
        self.assertIsNone(om.implied_vol(deep["price"], 100, 66.8, 1.558, "call", 0.03), "vega ~1e-9: not identifiable")


class TouchTest(unittest.TestCase):
    def test_reflection_principle_matches_driftless_closed_form_and_monte_carlo(self):
        # Driftless log-price: P(touch) = 2 * P(end beyond the level).
        sigma, t, level = 0.3, 0.5, 110
        b = math.log(level / 100)
        p = om.touch_probability(100, level, t, sigma, mu=0.5 * sigma * sigma)
        self.assertAlmostEqual(p, 2 * (1 - om.norm_cdf(b / (sigma * math.sqrt(t)))), places=12)
        rng, hits, n, steps = random.Random(1), 0, 4000, 500
        for _ in range(n):
            x = 0.0
            for _ in range(steps):
                x += sigma * math.sqrt(t / steps) * rng.gauss(0, 1)
                if x >= b:
                    hits += 1
                    break
        # A discrete grid misses some touches, so the simulation sits slightly below the continuous value.
        self.assertLess(hits / n, p + 0.02)
        self.assertGreater(hits / n, p - 0.06)
        self.assertEqual(om.touch_probability(100, 100, 1, 0.2), 1.0)
        self.assertAlmostEqual(om.touch_probability(100, 90, 0.5, 0.3), 1 - (1 - om.touch_probability(100, 90, 0.5, 0.3)), places=12)


class StructureTest(unittest.TestCase):
    def test_driftless_30_15_structure_is_about_one_third(self):
        r = om.premium_martingale(0.30, 0.15, premium_vol=1.0, steps=400, paths=6000, seed=11)
        self.assertLess(r.p_time_exit, 0.02)
        self.assertAlmostEqual(r.p_target_first, 1 / 3, delta=0.03)
        self.assertAlmostEqual(r.ev_pct, 0.0, delta=4 * r.standard_error_pct + 1.0)

    def test_seeded_reproducibility_and_threshold(self):
        kw = dict(spot=100, strike=102, days=21, iv=0.35, hold_days=5, spread_pct=0.04, paths=400, seed=3, threshold_pct=3)
        a, b = om.option_structure(**kw), om.option_structure(**kw)
        self.assertEqual(a, b)
        self.assertIs(a.passes_threshold, a.ev_pct >= 3)
        self.assertAlmostEqual(a.p_target_first + a.p_stop_first + a.p_time_exit, 1.0)
        self.assertNotEqual(a, om.option_structure(**dict(kw, seed=4)))

    def test_spread_and_theta_cost_money_under_a_driftless_underlying(self):
        base = dict(spot=100, strike=100, days=10, iv=0.25, hold_days=8, paths=1500, seed=5)
        no_cost = om.option_structure(**base)
        with_spread = om.option_structure(**base, spread_pct=0.10)
        self.assertLess(with_spread.ev_pct, no_cost.ev_pct)
        self.assertLess(no_cost.ev_pct, 2 * no_cost.standard_error_pct + 1.0, "no free lunch from theta-decaying calls")

    def test_jump_diffusion_runs_and_labels_itself(self):
        r = om.option_structure(100, 95, 21, 0.3, "put", hold_days=5, paths=300, seed=2, jump_intensity=3, jump_mean=-0.05, jump_vol=0.08)
        self.assertEqual(r.model, "Merton jump-diffusion")
        self.assertTrue(any("jumps" in a for a in r.assumptions))
        with self.assertRaises(ValueError):
            om.option_structure(100, 100, 10, 0.2, hold_days=20, stop=1.2)


if __name__ == "__main__":
    unittest.main()
