import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from consumer import etf_momentum_lab as ml
from consumer import etf_trend_lab as tl


def make_csv(n=400, drift=None):
    """Weekday bars. Sector k drifts at (k - 4) basis points a day, so XLY, XLV and XLU lead; SPY and the other tickers drift slightly."""
    drift = drift or {s: (i - 4) * 4e-4 for i, s in enumerate(ml.SECTORS)}
    days, d = [], date(1999, 1, 4)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    path = Path(tempfile.mkdtemp()) / "daily.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume"])
        for s in ml.ALL:
            px = 100.0
            for k, dt in enumerate(days):
                px *= 1 + drift.get(s, 1e-4) + (0.002 if k % 2 else -0.002)
                w.writerow([s, dt.isoformat(), px * 0.999, px * 1.01, px * 0.99, px, 1000000])
    return path, days


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.path, self.days = make_csv()
        self.d = tl.load(self.path, ml.ALL)

    def test_top_three_by_twelve_minus_one_are_chosen_at_equal_weight(self):
        i = len(self.days) - 1
        t = ml.momentum_targets(self.d, i, ml.SECTORS, 252, 3, False)
        self.assertEqual(len(t), 3)
        self.assertTrue(all(abs(w - 1 / 3) < 1e-9 for w in t.values()))
        lead = sorted(ml.SECTORS, key=lambda s: -(self.d["close"][s][i - 21] / self.d["close"][s][i - 252]))[:3]
        self.assertEqual(set(t), set(lead))

    def test_a_decision_never_uses_a_later_price(self):
        i = len(self.days) - 60
        before = ml.momentum_targets(self.d, i, ml.SECTORS, 252, 3, False)
        for s in ml.SECTORS:                      # wreck every future price
            for k in range(i + 1, len(self.days)):
                self.d["close"][s][k] = 0.01
        self.assertEqual(before, ml.momentum_targets(self.d, i, ml.SECTORS, 252, 3, False))

    def test_absolute_filter_leaves_a_falling_leader_in_cash(self):
        drift = {s: -3e-4 for s in ml.SECTORS}
        drift["XLK"] = -1e-4                      # best of a falling group
        path, days = make_csv(drift=drift)
        d = tl.load(path, ml.ALL)
        t = ml.momentum_targets(d, len(days) - 1, ml.SECTORS, 252, 3, True)
        self.assertLess(sum(t.values()), 1.0)

    def test_not_enough_history_means_no_targets(self):
        self.assertEqual(ml.momentum_targets(self.d, 100, ml.SECTORS, 252, 3, False), {})


class SimulationTests(unittest.TestCase):
    def test_costs_lower_the_result_and_trading_happens_at_the_next_open(self):
        path, days = make_csv(n=700)
        d = tl.load(path, ml.ALL)
        st, en = days[300], days[-1]
        fn = lambda dd, i: ml.momentum_targets(dd, i, ml.SECTORS, 252, 3, False)
        r0, t0 = ml.simulate(d, st, en, fn, 0.0)
        r1, t1 = ml.simulate(d, st, en, fn, 0.002)
        self.assertEqual(len(r0), len(r1))
        self.assertLess(sum(r1), sum(r0))
        self.assertEqual(r0[0], 0.0)              # nothing is held on the decision day itself

    def test_the_equal_weight_control_holds_every_sector(self):
        path, days = make_csv(n=400)
        d = tl.load(path, ml.ALL)
        t = ml.equal_targets(d, len(days) - 1, ml.SECTORS)
        self.assertEqual(set(t), set(ml.SECTORS))


class VerdictTests(unittest.TestCase):
    def res(self, **over):
        base = {"V0": {"sharpe": 0.8, "max_drawdown": 0.3, "cagr": 0.12}, "V0_c10": {"sharpe": 0.75}, "SPY": {"sharpe": 0.5, "max_drawdown": 0.5, "cagr": 0.13},
                "boot_V0_minus_C0": [0.05, 0.4]}
        r = {w: {k: dict(v) if isinstance(v, dict) else list(v) for k, v in base.items()} for w in ("A", "B", "FULL")}
        for path, val in over.items():
            w, k, f = path.split("__")
            r[w][k][f] = val
        return r

    def test_pass_inconclusive_and_rejected(self):
        self.assertEqual(ml.verdict(self.res())[0], "PASS")
        r = self.res(); r["FULL"]["boot_V0_minus_C0"] = [-0.1, 0.4]
        self.assertEqual(ml.verdict(r)[0], "INCONCLUSIVE")
        self.assertEqual(ml.verdict(self.res(B__V0__cagr=0.09))[0], "REJECTED")       # P5: 9 percent against SPY 13 percent minus 2
        self.assertEqual(ml.verdict(self.res(A__V0__sharpe=0.4))[0], "REJECTED")      # P1
        self.assertEqual(ml.verdict(self.res(B__V0__max_drawdown=0.6))[0], "REJECTED")   # P2: worse than SPY


if __name__ == "__main__":
    unittest.main()
