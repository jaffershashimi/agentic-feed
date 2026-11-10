import random
import unittest
from datetime import date, timedelta

from consumer import etf_trend_lab as lab


def make(prices, firsts=None, n=700):
    """Synthetic frame: weekdays from 2000-01-03; prices: {sym: callable(i) -> close}; open = previous close."""
    days, d0 = [], date(2000, 1, 3)
    while len(days) < n:
        if d0.weekday() < 5:
            days.append(d0)
        d0 += timedelta(days=1)
    o, c, first = {}, {}, {}
    for s, fn in prices.items():
        f = (firsts or {}).get(s, 0)
        c[s] = [fn(i) if i >= f else None for i in range(n)]
        o[s] = [(c[s][i - 1] if i > f else c[s][i]) if i >= f else None for i in range(n)]
        first[s] = f
    return {"days": days, "open": o, "close": c, "first": first, "universe": list(prices)}


def walk(seed, drift=0.0004, vol=0.01, n=700):
    rng = random.Random(seed)
    x, out = 100.0, []
    for _ in range(n):
        x *= 1 + drift + rng.gauss(0, vol)
        out.append(x)
    return lambda i: out[i]


class LabTests(unittest.TestCase):
    def test_month_end_indices(self):
        d = make({"SPY": walk(1)}, n=80)
        idx = lab.month_end_indices(d["days"])
        self.assertEqual(idx[-1], len(d["days"]) - 1)
        for i in idx[:-1]:
            self.assertNotEqual(d["days"][i].month, d["days"][i + 1].month)

    def test_eligibility_needs_253_bars(self):
        d = make({"SPY": walk(1), "QQQ": walk(2)}, firsts={"QQQ": 400})
        self.assertFalse(lab.eligible(d, "QQQ", 600))
        self.assertTrue(lab.eligible(d, "QQQ", 400 + lab.MIN_BARS - 1))
        self.assertTrue(lab.eligible(d, "SPY", 300))

    def test_targets_use_only_data_up_to_the_decision_day(self):
        up = walk(3)
        a = make({"SPY": up, "TLT": walk(4)})
        i = 520
        b = make({"SPY": lambda k: up(k) if k <= i else up(k) * 0.3, "TLT": lambda k, w=walk(4): w(k) if k <= i else w(k) * 3})
        self.assertEqual(lab.targets(a, i, 252, "invvol"), lab.targets(b, i, 252, "invvol"))

    def test_trend_filter_drops_losers_and_control_sums_to_one(self):
        d = make({"SPY": lambda i: 100 * 1.001 ** i, "TLT": lambda i: 100 * 0.999 ** i, "GLD": walk(5)})
        i = 600
        ctl = lab.targets(d, i, 252, "invvol", trend=False)
        t = lab.targets(d, i, 252, "invvol")
        self.assertAlmostEqual(sum(ctl.values()), 1.0)
        self.assertIn("SPY", t)
        self.assertNotIn("TLT", t)
        for s, w in t.items():
            self.assertAlmostEqual(w, ctl[s])  # base weights over ALL eligible, not renormalized
        self.assertLess(sum(t.values()), 1.0)

    def test_equal_weighting(self):
        d = make({"SPY": walk(1), "TLT": walk(2)})
        w = lab.base_weights(d, 400, "equal")
        self.assertAlmostEqual(w["SPY"], 0.5)

    def test_trade_happens_at_next_open_not_the_decision_close(self):
        n = 700
        closes = [100.0] * n
        d = make({"SPY": lambda i: closes[i]}, n=n)
        d["open"]["SPY"] = [100.0] * n
        d["open"]["SPY"][301] = 100.0
        d["close"]["SPY"][301] = 110.0
        start = d["days"][300]
        rets, turns = lab.simulate(d, start, d["days"][320], cost=0.0, spy_only=True)
        self.assertAlmostEqual(rets[0], 0.0)
        self.assertAlmostEqual(rets[1], 0.10, places=6)  # bought at the day-301 open (100), marked at its close (110)

    def test_costs_reduce_end_equity(self):
        d = make({"SPY": walk(1), "TLT": walk(2), "GLD": walk(3)})
        st, en = d["days"][300], d["days"][650]
        r0, _ = lab.simulate(d, st, en, cost=0.0)
        r1, _ = lab.simulate(d, st, en, cost=0.002)
        e0 = e1 = 1.0
        for a, b in zip(r0, r1):
            e0 *= 1 + a
            e1 *= 1 + b
        self.assertLess(e1, e0)

    def test_flat_prices_give_flat_equity(self):
        d = make({"SPY": lambda i: 100.0, "TLT": lambda i: 50.0})
        rets, _ = lab.simulate(d, d["days"][300], d["days"][400], cost=0.0)
        self.assertTrue(all(abs(r) < 1e-12 for r in rets))

    def test_bootstrap_is_deterministic_and_zero_for_identical_series(self):
        rng = random.Random(1)
        a = [rng.gauss(0.0004, 0.01) for _ in range(400)]
        b = [rng.gauss(0.0002, 0.01) for _ in range(400)]
        self.assertEqual(lab.boot_sharpe_diff(a, b, resamples=300), lab.boot_sharpe_diff(a, b, resamples=300))
        lo, hi = lab.boot_sharpe_diff(a, a, resamples=300)
        self.assertEqual((lo, hi), (0.0, 0.0))

    def test_verdict_logic(self):
        def win(v0_sh, spy_sh, dd, cagr, spy_cagr, c10_sh, lo=0.1):
            return {"V0": {"sharpe": v0_sh, "max_drawdown": dd, "cagr": cagr}, "SPY": {"sharpe": spy_sh, "cagr": spy_cagr},
                    "V0_c10": {"sharpe": c10_sh}, "boot_V0_minus_C0": [lo, 1.0]}
        good = {"A": win(0.9, 0.5, 0.2, 0.08, 0.07, 0.85), "B": win(0.9, 0.5, 0.2, 0.08, 0.09, 0.85)}
        self.assertEqual(lab.verdict(good)[0], "PASS")
        inc = {"A": win(0.9, 0.5, 0.2, 0.08, 0.07, 0.85, lo=-0.2), "B": good["B"]}
        self.assertEqual(lab.verdict(inc)[0], "INCONCLUSIVE")
        bad = {"A": win(0.4, 0.5, 0.2, 0.08, 0.07, 0.4), "B": good["B"]}
        self.assertEqual(lab.verdict(bad)[0], "REJECTED")
        low_cagr = {"A": good["A"], "B": win(0.9, 0.5, 0.2, 0.05, 0.09, 0.85)}
        self.assertEqual(lab.verdict(low_cagr)[0], "REJECTED")


if __name__ == "__main__":
    unittest.main()
