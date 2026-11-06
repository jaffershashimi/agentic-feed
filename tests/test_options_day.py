import unittest
from datetime import date, time

from consumer import options_day as od


def _bar(h, m, o, hi, lo, c, s="reg"):
    return {"t": time(h, m), "o": o, "h": hi, "l": lo, "c": c, "s": s}


def _under(move=0.02, confirm=True):
    prev = [_bar(12, 55, 100, 100, 100, 100)]
    pm = 100 * (1 + move)
    day = [_bar(6, 20, pm, pm, pm, pm, "pre"), _bar(6, 30, pm, pm + 1, pm - 1, pm)]
    day.append(_bar(6, 35, pm, pm + 1, pm - 1, pm))
    last = pm + 0.5 if confirm == (move > 0) else pm - 0.5
    day.append(_bar(6, 40, pm, pm + 1, pm - 1, last))
    return {"NVDA": {date(2026, 10, 26): prev, date(2026, 10, 27): day}}


def _opt(path):
    """path: list of (o, h, l, c) for bars from 06:45 every 5 minutes."""
    out, h, m = [], 6, 45
    for o, hi, lo, c in path:
        out.append({"t": time(h, m), "o": o, "h": hi, "l": lo, "c": c})
        m += 5
        if m == 60:
            h, m = h + 1, 0
    return out


class SignalTests(unittest.TestCase):
    def test_bull_bear_and_none(self):
        self.assertEqual(od.signals(_under(0.02))[0]["side"], "call")
        self.assertEqual(od.signals(_under(-0.02))[0]["side"], "put")
        self.assertEqual(od.signals(_under(0.005)), [])
        self.assertEqual(od.signals(_under(0.02, confirm=False)), [])


class SimulateTests(unittest.TestCase):
    def test_target_hit(self):
        fill, px, why = od.simulate(_opt([(5, 5, 5, 5), (5, 7, 5, 6.5)]), 0.20, -0.15)
        self.assertAlmostEqual(fill, 5 + 0.075)
        self.assertEqual(why, "EXIT_TARGET")
        self.assertAlmostEqual(px, fill * 1.2)

    def test_stop_before_target_same_bar(self):
        _, px, why = od.simulate(_opt([(5, 5, 5, 5), (5, 9, 3, 5)]), 0.20, -0.15)
        self.assertEqual(why, "EXIT_STOP")

    def test_gap_and_time_exit(self):
        _, px, why = od.simulate(_opt([(5, 5, 5, 5), (3, 3, 3, 3)]), 0.20, -0.15)
        self.assertEqual((px, why), (3, "EXIT_STOP_GAP"))
        flat = _opt([(5, 5.05, 4.95, 5)] * 80)
        self.assertEqual(od.simulate(flat, 0.5, -0.35)[2], "EXIT_TIME")
        hourly = [{"t": time(h, 0), "o": 5, "h": 5.1, "l": 4.9, "c": 5} for h in range(7, 13)]
        self.assertEqual(od.simulate(hourly, 0.5, -0.35, time(7, 0), time(12, 0))[2], "EXIT_TIME")

    def test_holm_monotone(self):
        self.assertEqual(od.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])


class ReplayTests(unittest.TestCase):
    def test_replay_grid_and_costs(self):
        sigs = od.signals(_under(0.02))
        key = (sigs[0]["day"], "NVDA", "call")
        bars = {key: _opt([(2, 2, 2, 2), (2, 3, 2, 2.9)])}
        meta = {key: {"occ": "X", "strike": 103.0, "expiry": "2026-10-30"}}
        tests, rows = od.replay(sigs, bars, meta)
        self.assertEqual(len(tests), 9)
        t20 = next(t for t in tests if t["test"] == "T20_S15")
        self.assertEqual(t20["trades"], 1)
        self.assertEqual(t20["verdict"], "insufficient_evidence")
        r = next(r for r in rows if r["test"] == "T20_S15")
        self.assertTrue(r["affordable_1k"])
        self.assertLess(r["pnl_1ct"], (2.03 * 1.2 - 2.03) * 100)  # spread and fees cost something


if __name__ == "__main__":
    unittest.main()
