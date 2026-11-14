import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from consumer import oversold_panel as op


def bars(closes, opens=None, lows=None, highs=None):
    n = len(closes)
    return {"date": [f"2020-01-{i + 1:02d}" for i in range(n)], "o": opens or list(closes), "h": highs or [c * 1.01 for c in closes],
            "l": lows or [c * 0.99 for c in closes], "c": list(closes), "v": [1e6] * n}


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(op.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_rsi2_known_values(self):
        out = op.rsi2([10, 11, 10, 9, 12])
        # changes +1, -1, -1, +3; first averages over the first two changes: gain 0.5, loss 0.5 -> RSI 50
        self.assertAlmostEqual(out[2], 50.0)
        # next: gain 0, loss 1 -> avg gain 0.25, avg loss 0.75 -> RSI 25
        self.assertAlmostEqual(out[3], 25.0)
        # then gain 3 -> avg gain 1.625, avg loss 0.375 -> RSI 81.25
        self.assertAlmostEqual(out[4], 100 - 100 / (1 + 1.625 / 0.375))
        self.assertEqual(op.rsi2([1, 2, 3, 4])[3], 100.0)

    def test_zdev_needs_250_prior_values_and_uses_only_the_past(self):
        c = [100 + (i % 7) * 0.5 for i in range(400)]
        s25 = op.sma(c, 25)
        z = op.zdev_series(c, s25)
        self.assertIsNone(z[273])
        self.assertIsNotNone(z[274])
        c2 = c[:]
        c2[399] = 10  # a crash at the end must not change earlier z values
        z2 = op.zdev_series(c2, op.sma(c2, 25))
        self.assertEqual(z[:399], z2[:399])
        self.assertLess(z2[399], -5)

    def test_stress_flag_needs_history_and_marks_a_volatility_spike(self):
        calm = [100 + 0.01 * i for i in range(400)]
        vol = [calm[-1] * (1.05 if i % 2 else 0.95) for i in range(30)]
        f = op.stress_flags(calm + vol)
        self.assertFalse(any(f[:270]))
        self.assertTrue(f[-1])


class TradeTests(unittest.TestCase):
    def ind(self, d):
        return {"sma5": op.sma(d["c"], 5)}

    def test_signal_exit_when_close_above_sma5(self):
        d = bars([10, 10, 10, 10, 10, 10, 9, 9.0, 11.0, 11, 11])
        d["o"][7] = 9.2
        d["l"] = [c * 0.99 for c in d["c"]]
        t = op.trade(d, self.ind(d), 6, 0.0)  # signal at bar 6, entry at the open of bar 7
        self.assertEqual((t[0], t[2]), (7, 9.2))
        self.assertEqual((t[1], t[3]), (8, 11.0))  # bar 7 closes 9.0 (below SMA5 9.6), bar 8 closes 11.0 above it

    def test_stop_exits_at_stop_price_and_gap_exits_at_open(self):
        d = bars([10] * 12)
        d["l"][8] = 8.5  # intraday breach of the 10 percent stop (entry 10 -> stop 9)
        t = op.trade(d, self.ind(d), 7, 0.0)
        self.assertEqual((t[1], t[3]), (8, 9.0))
        d2 = bars([10] * 12)
        d2["o"][9], d2["l"][9] = 8.0, 7.5  # next day gaps below the stop: exit at the open
        t2 = op.trade(d2, self.ind(d2), 7, 0.0)
        self.assertEqual((t2[1], t2[3]), (9, 8.0))

    def test_time_exit_on_the_fifth_day_and_no_trade_past_the_data(self):
        d = bars([10, 10, 10, 10, 10, 10, 10, 10, 9, 9, 9, 9, 9, 9])
        d["c"] = [10] * 8 + [9.9, 9.8, 9.7, 9.6, 9.5, 9.4]
        t = op.trade(d, self.ind(d), 7, 0.0)
        self.assertEqual(t[1] - t[0] + 1, 5)  # nothing triggers earlier: close below SMA5 and above the stop every day
        self.assertEqual(t[3], d["c"][t[1]])
        short = bars([10, 10, 10, 10])
        self.assertIsNone(op.trade(short, self.ind(short), 3, 0.0))  # no next bar
        self.assertIsNone(op.trade(short, self.ind(short), 1, 0.0))  # path runs past the data without an exit

    def test_net_return_applies_cost_both_sides(self):
        self.assertAlmostEqual(op.net_return(100, 100, 0.001), (0.999 / 1.001) - 1)


class StatsTests(unittest.TestCase):
    def test_pf_and_holm(self):
        self.assertAlmostEqual(op.pf([0.1, -0.05, 0.05]), 3.0)
        self.assertEqual(op.pf([0.1]), float("inf"))
        adj = op.holm([0.01, 0.04, 0.03])
        self.assertAlmostEqual(adj[0], 0.03)
        self.assertAlmostEqual(adj[2], 0.06)
        self.assertAlmostEqual(adj[1], 0.06)

    def test_bootstrap_is_deterministic_and_brackets_the_mean(self):
        wk = {f"2022-{i:02d}": (0.01 * (1 if i % 2 else -1) + 0.004, 3) for i in range(1, 40)}
        a, b = op.bootstrap(wk, 500), op.bootstrap(wk, 500)
        self.assertEqual(a, b)
        self.assertLess(a["lo"], a["mean"] / 1.0 + 1e-9)
        self.assertGreater(a["hi"], a["lo"])

    def test_week_key(self):
        self.assertEqual(op.week_key("2022-01-03"), "2022-01")
        self.assertEqual(op.week_key("2021-01-01"), "2020-53")

    def test_verdict_rules(self):
        def mk(n=150, pf=1.3, lo=0.001, m10=0.002, holm=0.01, sh=1.0, spy=0.5, mdd=0.1, stress=0.001):
            w = {"cost": {str(c): {"n": n, "pf": pf, "mean": m10} for c in op.COSTS}, "mean_interval": {"lo": lo}, "stress_mean": stress,
                 "portfolio": {"sharpe": sh, "mdd": mdd}, "spy": {"sharpe": spy, "mdd": 0.3}}
            return {"windows": {"Judge": w, "Final": w}, "holm_p_final_vs_control": holm}
        self.assertEqual(op.verdict(mk())[0], "PASS")
        self.assertEqual(op.verdict(mk(holm=0.2))[0], "INCONCLUSIVE")
        self.assertEqual(op.verdict(mk(sh=0.2))[0], "INCONCLUSIVE")
        self.assertEqual(op.verdict(mk(stress=-0.001))[0], "INCONCLUSIVE")
        self.assertEqual(op.verdict(mk(lo=-0.001))[0], "REJECTED")
        self.assertEqual(op.verdict(mk(pf=1.1))[0], "REJECTED")
        self.assertEqual(op.verdict(mk(n=50))[0], "REJECTED")


class EndToEndTests(unittest.TestCase):
    def test_tiny_panel_runs_and_is_reproducible(self):
        import random
        rnd = random.Random(1)
        with tempfile.TemporaryDirectory() as tmp:
            panel = Path(tmp) / "panel"
            (panel / "daily").mkdir(parents=True)
            rows = []
            for sym in ("SPY", "AAA", "BBB"):
                px = 100.0
                for i in range(1600):
                    px *= 1 + rnd.gauss(0.0004, 0.015)
                    day = 1 + i
                    y, doy = 2017 + (i // 250), i % 250
                    m, dd = 1 + doy // 21, 1 + doy % 21
                    rows.append([sym, f"{y}-{min(m, 12):02d}-{dd:02d}", px * 0.999, px * 1.01, px * 0.99, px, 5e6])
            with gzip.open(panel / "daily" / "part-0000.csv.gz", "wt", newline="") as fh:
                w = csv.writer(fh); w.writerow(["symbol", "date", "o", "h", "l", "c", "v"]); w.writerows(rows)
            (panel / "universe_by_year.json").write_text(json.dumps({str(y): ["AAA", "BBB", "SPY"] for y in range(2017, 2027)}))
            out1, out2 = Path(tmp) / "o1", Path(tmp) / "o2"
            self.assertEqual(op.main([str(panel), str(out1)]), 0)
            self.assertEqual(op.main([str(panel), str(out2)]), 0)
            r1 = json.loads((out1 / "s042_results.json").read_text())
            self.assertEqual(r1, json.loads((out2 / "s042_results.json").read_text()))
            self.assertEqual(set(r1["variants"]), set(op.VARIANTS))
            self.assertIn("### V1", (out1 / "s042_results.md").read_text())


if __name__ == "__main__":
    unittest.main()
