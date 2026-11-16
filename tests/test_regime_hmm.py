import unittest

from consumer import regime_hmm as rh


class HmmTests(unittest.TestCase):
    def test_fit_recovers_two_regimes_and_orders_them_by_variance(self):
        xs, _ = rh.synthetic()
        p = rh.fit(xs, 40)
        self.assertLess(p["var"][0], p["var"][1])
        self.assertAlmostEqual(p["var"][0] ** 0.5, 0.007, delta=0.003)
        self.assertAlmostEqual(p["var"][1] ** 0.5, 0.025, delta=0.008)
        self.assertGreater(p["A"][0][0], 0.9)

    def test_filtered_probability_separates_regimes(self):
        xs, st = rh.synthetic()
        p = rh.fit(xs, 40)
        pr = rh.filter_probs(xs, p)
        hit = sum((q > 0.5) == bool(s) for q, s in zip(pr, st)) / len(xs)
        self.assertGreater(hit, 0.85)

    def test_filter_is_causal_changing_the_future_does_not_change_the_past(self):
        xs, _ = rh.synthetic(800)
        p = rh.fit(xs[:500], 20)
        a = rh.filter_probs(xs, p)
        ys = xs[:600] + [0.2] * 200
        b = rh.filter_probs(ys, p)
        self.assertEqual(a[:600], b[:600])

    def test_rolling_never_uses_the_future_to_fit(self):
        xs, _ = rh.synthetic(1100)
        base = rh.rolling_stress_probability(xs, train=500, refit_every=100, n_iter=15)
        ys = xs[:900] + [0.3, -0.3] * 100
        alt = rh.rolling_stress_probability(ys, train=500, refit_every=100, n_iter=15)
        self.assertEqual(base[:900], alt[:900])
        self.assertTrue(all(v is None for v in base[:500]))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in base[500:]))

    def test_too_little_data_is_rejected(self):
        with self.assertRaises(ValueError):
            rh.fit([0.01] * 10)


if __name__ == "__main__":
    unittest.main()
