import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import orb_study as ob

ET = ZoneInfo("America/New_York")


def make_day(date="2025-03-04", base=100.0, spec=None):
    """78 five-minute bars. spec: {index: (o, h, l, c, v)} overrides; default flat bars at `base` with volume 1000."""
    out = []
    start = datetime.fromisoformat(date).replace(hour=9, minute=30, tzinfo=ET)
    for i in range(78):
        o, h, l, c, v = (spec or {}).get(i, (base, base + 0.05, base - 0.05, base, 1000))
        t = (start + timedelta(minutes=5 * i)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})
    return out


RANGE = {0: (100, 101, 99, 100, 1000), 1: (100, 101, 99.5, 100.5, 1000), 2: (100.5, 101, 99, 100.2, 1000)}  # range high 101, low 99, midpoint 100


class RuleTests(unittest.TestCase):
    def test_rounding_helpers(self):
        self.assertEqual(ob.floor_cents(358.325), 358.32)
        self.assertEqual(ob.round_cents(370.045), 370.05)

    def test_only_full_sessions_are_used(self):
        day = make_day()
        self.assertEqual(list(ob.sessions(day)), ["2025-03-04"])
        self.assertEqual(ob.sessions(day[:-1]), {})

    def test_trigger_needs_a_close_above_the_range_high_not_a_wick(self):
        spec = dict(RANGE)
        spec[3] = (100.2, 101.5, 100.0, 100.9, 1000)  # wick above 101, close below
        spec[4] = (100.9, 101.6, 100.8, 101.4, 1000)  # first close above
        i, hi, lo = ob.breakout(make_day(spec=spec))
        self.assertEqual((i, hi, lo), (4, 101.0, 99.0))

    def test_volume_filter_skips_a_thin_trigger_bar(self):
        spec = dict(RANGE)
        spec[3] = (100.9, 101.6, 100.8, 101.4, 500)    # thin: below the range average of 1000
        spec[5] = (101.4, 101.9, 101.3, 101.8, 2000)
        self.assertEqual(ob.breakout(make_day(spec=spec), None)[0], 3)
        self.assertEqual(ob.breakout(make_day(spec=spec), 1.0)[0], 5)

    def test_target_hit_gives_plus_one_r_before_slippage(self):
        spec = dict(RANGE)
        spec[3] = (100.9, 101.6, 100.8, 101.4, 1000)
        spec[4] = (101.4, 101.5, 101.3, 101.4, 1000)    # entry bar open 101.4
        spec[5] = (101.4, 103.9, 101.3, 103.5, 1000)    # reaches the target
        t = ob.evaluate_day(make_day(spec=spec), "V1")
        entry = 101.4 * 1.0002
        risk = entry - 100.0
        self.assertEqual(t["how"], "TARGET")
        self.assertAlmostEqual(t["entry"], entry)
        self.assertAlmostEqual(t["target"], round(entry + risk, 2), places=2)
        self.assertGreater(t["r"], 0.9)
        self.assertLess(t["r"], 1.01)

    def test_stop_first_when_a_bar_touches_both_and_gap_fills_at_the_open(self):
        spec = dict(RANGE)
        spec[3] = (100.9, 101.6, 100.8, 101.4, 1000)
        spec[4] = (101.4, 101.5, 101.3, 101.4, 1000)
        spec[5] = (101.4, 105.0, 99.0, 101.0, 1000)     # touches stop 100 and target
        t = ob.evaluate_day(make_day(spec=spec), "V1")
        self.assertEqual((t["how"], t["exit"] < 100.0), ("STOP", True))
        spec[5] = (98.0, 99.0, 97.0, 98.5, 1000)         # gaps below the stop: fills at the open 98, worse than 100
        t = ob.evaluate_day(make_day(spec=spec), "V1")
        self.assertAlmostEqual(t["exit"], 98.0 * (1 - ob.SLIP))
        self.assertLess(t["r"], -1.0)

    def test_flat_exit_and_no_trigger_and_tight_risk_skip(self):
        spec = dict(RANGE)
        spec[3] = (100.9, 101.6, 100.8, 101.4, 1000)
        spec[4] = (101.4, 101.5, 101.3, 101.4, 1000)
        t = ob.evaluate_day(make_day(spec=spec), "V1")   # later bars are flat around 100: the stop at 100 is touched
        self.assertIn(t["how"], ("STOP", "FLAT"))
        self.assertIsNone(ob.evaluate_day(make_day(spec=RANGE), "V1"))
        tight = {0: (100, 100.02, 99.98, 100, 1000), 1: (100, 100.02, 99.98, 100, 1000), 2: (100, 100.02, 99.98, 100, 1000), 3: (100.0, 100.05, 100.0, 100.03, 1000)}
        self.assertIsNone(ob.evaluate_day(make_day(spec=tight), "V1"))  # entry within 0.05 percent of the midpoint stop

    def test_verdict_rules(self):
        good = {"n": 400, "mean": 0.1, "lo": 0.02, "hi": 0.2}
        v = {"Judge": {"mean": 0.1}, "Final": {"mean": 0.08}, "Judge+Final": good}
        self.assertEqual(ob.verdict(v), "PROMISING")
        self.assertEqual(ob.verdict(dict(v, **{"Judge+Final": dict(good, mean=-0.01)})), "REJECTED")
        self.assertEqual(ob.verdict(dict(v, **{"Judge+Final": dict(good, lo=-0.01)})), "INCONCLUSIVE")


class EndToEnd(unittest.TestCase):
    def test_run_is_deterministic_and_windowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "c").mkdir()
            spec = dict(RANGE)
            spec[3] = (100.9, 101.6, 100.8, 101.4, 1000)
            spec[4] = (101.4, 101.5, 101.3, 101.4, 1000)
            spec[5] = (101.4, 103.9, 101.3, 103.5, 1000)
            bars = []
            for d in ("2025-03-04", "2025-03-05", "2026-03-04", "2024-03-04"):
                bars += make_day(d, spec=spec)
            (tmp / "c" / "AAA.json").write_text(json.dumps(bars))
            (tmp / "c" / "SPY.json").write_text(json.dumps(make_day("2025-03-04")))
            a = ob.run(["AAA"], tmp / "c", tmp / "o1")
            b = ob.run(["AAA"], tmp / "c", tmp / "o2")
            self.assertEqual(a, b)
            self.assertEqual(a["variants"]["V1"]["Development"]["n"], 1)
            self.assertEqual(a["variants"]["V1"]["Judge"]["n"], 2)  # 2025-03-04 and 2025-03-05
            self.assertEqual(a["variants"]["V1"]["Final"]["n"], 1)
            self.assertIn("S-046 results", (tmp / "o1" / "s046_results.md").read_text())


if __name__ == "__main__":
    unittest.main()
