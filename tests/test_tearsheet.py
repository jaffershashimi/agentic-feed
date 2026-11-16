import random
import re
import unittest
from xml.dom import minidom

from consumer import tearsheet as ts


def sample(n=600, seed=2):
    rnd = random.Random(seed)
    dates = [f"{2022 + i // 250}-{1 + (i % 250) // 21:02d}-{1 + i % 21:02d}" for i in range(n)]
    return dates, [rnd.gauss(0.0006, 0.01) for _ in range(n)]


class TearSheetTests(unittest.TestCase):
    def test_equity_and_drawdown(self):
        eq = ts.equity([0.1, -0.1])
        self.assertAlmostEqual(eq[-1], 0.99)
        dd = ts.drawdown(eq)
        self.assertAlmostEqual(dd[-1], 0.99 / 1.1 - 1)
        self.assertEqual(dd[0], 0.0)

    def test_stats_known_values(self):
        s = ts.stats([0.01] * 252 + [-0.01] * 252)
        self.assertEqual(s["days"], 504)
        self.assertAlmostEqual(s["hit"], 0.5)
        self.assertAlmostEqual(ts.stats([0.01, 0.0, 0.0, -0.01])["hit"], 0.5)  # flat days are not counted
        self.assertLess(s["max_dd"], 0)
        with self.assertRaises(ValueError):
            ts.stats([0.1])

    def test_yearly_compounds_within_year(self):
        y = ts.yearly(["2023-01-02", "2023-06-01", "2024-01-02"], [0.1, 0.1, 0.5])
        self.assertAlmostEqual(y["2023"], 0.21)
        self.assertAlmostEqual(y["2024"], 0.5)

    def test_render_is_valid_deterministic_svg_with_windows_and_benchmark(self):
        d, r = sample()
        _, b = sample(seed=3)
        a = ts.render("Title & more", "sub", d, r, benchmark=b, windows={"Judge": (d[100], d[300])}, notes=["note one", "note two"])
        self.assertEqual(a, ts.render("Title & more", "sub", d, r, benchmark=b, windows={"Judge": (d[100], d[300])}, notes=["note one", "note two"]))
        minidom.parseString(a)
        self.assertIn("Title &amp; more", a)
        self.assertIn("Judge", a)
        self.assertIn("benchmark", a)
        self.assertNotIn("nan", a.lower().replace("financial", ""))

    def test_render_rejects_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            ts.render("t", "s", ["2024-01-01"], [0.1, 0.2])


if __name__ == "__main__":
    unittest.main()
