import math
import random
import unittest
from datetime import date, timedelta

from feed_pipeline import volcore as vc

D0 = date(2024, 1, 1)


def bars_from(closes, spread=0.01, gap=0.0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev * (1 + gap)
        hi, lo = max(o, c) * (1 + spread), min(o, c) * (1 - spread)
        out.append((D0 + timedelta(days=i), o, hi, lo, c))
        prev = c
    return out


def gbm(n, sigma, seed):
    rng, p, out = random.Random(seed), 100.0, []
    for _ in range(n):
        p *= math.exp(-0.5 * sigma ** 2 / 252 + sigma / math.sqrt(252) * rng.gauss(0, 1))
        out.append(p)
    return out


class ParseTest(unittest.TestCase):
    def test_bad_rows_are_skipped_not_filled(self):
        text = "DATE,OPEN,HIGH,LOW,CLOSE\n10/20/2026,10,11,9,10.5\n10/21/2026,10,9,11,10\n10/22/2026,,11,9,10\n10/23/2026,10,11,9,0\nbad,row,x,,\n10/26/2026,nan,inf,9,10\n10/27/2026,10,11\n"
        self.assertEqual(vc.parse_ohlc(text), [(date(2026, 10, 20), 10.0, 11.0, 9.0, 10.5)])
        with self.assertRaises(ValueError):
            vc.parse_ohlc("DATE,CLOSE\n10/20/2026,10\n")


class EstimatorTest(unittest.TestCase):
    def test_hand_calculations(self):
        # Alternating +1% / -1% log returns: close-to-close vol is sqrt(252) * sample sd.
        closes = [100 * math.exp(0.01 * (i % 2)) for i in range(21)]
        bars = bars_from(closes, spread=0.0)
        r = [0.01 * (1 if i % 2 else -1) for i in range(1, 21)]
        m = sum(r) / 20
        self.assertAlmostEqual(vc.close_to_close(bars), math.sqrt(sum((x - m) ** 2 for x in r) / 19 * 252) * 100, places=9)
        # Parkinson with a constant 2% high/low range: sqrt(ln(H/L)^2 / (4 ln 2) * 252).
        flat = [(D0 + timedelta(days=i), 100, 101, 99, 100) for i in range(20)]
        self.assertAlmostEqual(vc.parkinson(flat), math.sqrt(math.log(101 / 99) ** 2 / (4 * math.log(2)) * 252) * 100, places=9)

    def test_estimators_agree_on_simulated_gbm_and_need_history(self):
        closes = gbm(400, 0.20, 1)
        # Intraday ranges consistent with the daily vol so range estimators are comparable.
        rng = random.Random(2)
        bars, prev = [], closes[0]
        for i, c in enumerate(closes):
            o = prev
            ext = abs(rng.gauss(0, 0.2 / math.sqrt(252))) * 0.8
            bars.append((D0 + timedelta(days=i), o, max(o, c) * math.exp(ext), min(o, c) * math.exp(-ext), c))
            prev = c
        for f in (vc.close_to_close, vc.yang_zhang):
            self.assertTrue(8 < f(bars, 60) < 35, f.__name__)
        self.assertIsNone(vc.close_to_close(bars[:20]))
        self.assertIsNone(vc.yang_zhang(bars[:20]))
        self.assertIsNone(vc.parkinson(bars[:19]))

    def test_garch_recovers_known_parameters_roughly(self):
        rng, a, b, var = random.Random(5), 0.08, 0.90, 0.0001
        omega, h, rets = var * (1 - a - b), var, []
        for _ in range(1500):
            e = math.sqrt(h) * rng.gauss(0, 1)
            rets.append(e)
            h = omega + a * e * e + b * h
        g = vc.garch11(rets)
        self.assertAlmostEqual(g["persistence"], a + b, delta=0.04)
        self.assertIsNone(vc.garch11(rets[:100]))
        self.assertGreater(g["vol_1d"], 0)


class ComputeTest(unittest.TestCase):
    def setUp(self):
        closes = gbm(600, 0.18, 3)
        self.spx = bars_from(closes)
        days = [b[0] for b in self.spx]
        self.vix = [(d, 15 + 5 * math.sin(i / 30)) for i, d in enumerate(days)]
        self.vix9d = [(d, v - 1) for d, v in self.vix]
        self.vix3m = [(d, v + 1.5) for d, v in self.vix]
        self.skew = [(d, 140.0) for d in days]
        self.session = days[500]

    def test_point_in_time_no_lookahead(self):
        a = vc.compute(self.spx, self.vix, self.vix9d, self.vix3m, self.skew, self.session)
        future = [(d + timedelta(days=1000), *rest) for d, *rest in self.spx[-50:]]
        b = vc.compute(self.spx + future, self.vix + [(self.session + timedelta(days=5), 99.0)], self.vix9d, self.vix3m, self.skew, self.session)
        self.assertEqual(a, b, "rows after the session never change the output")
        self.assertEqual(a["as_of"]["spx"], self.session.isoformat())

    def test_outputs_and_nulls(self):
        out = vc.compute(self.spx, self.vix, self.vix9d, self.vix3m, self.skew, self.session)
        v = self.vix[500][1]
        self.assertAlmostEqual(out["term_slope_3m_minus_1m"], 1.5, places=2)
        self.assertAlmostEqual(out["exp_move_1d_pct"], round(v / math.sqrt(252), 3))
        self.assertTrue(0 <= out["iv_pctile_252"] <= 100 and 0 <= out["iv_rank_252"] <= 100)
        self.assertAlmostEqual(out["vrp_vol_pts"], round(v - out["rv20_pct"]["close_to_close"], 2), places=1)
        short = vc.compute(self.spx[:30], self.vix[:30], [], [], [], self.spx[29][0])
        self.assertIsNone(short["garch11"])
        self.assertIsNone(short["iv_pctile_252"])
        self.assertIsNone(short["term_slope_3m_minus_1m"])
        empty = vc.compute([], [], [], [], [], self.session)
        self.assertIsNone(empty["rv20_pct"]["yang_zhang"])
        self.assertIsNone(empty["vrp_vol_pts"])


if __name__ == "__main__":
    unittest.main()
