import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from consumer import news_events as ne
from consumer import news_shock_study as st

SESSIONS = {"2024-04-12", "2024-04-15", "2024-04-16", "2024-04-17"}


def bars_for(d, start_px=100.0, drift=0.0):
    a, _ = st.day_bounds(d)
    out, px = [], start_px
    for i in range(390):
        o = px
        px = px * (1 + drift)
        out.append({"t": a + timedelta(minutes=i), "o": o, "h": max(o, px) * 1.0001, "l": min(o, px) * 0.9999, "c": px})
    return out


class SessionTests(unittest.TestCase):
    def test_session_types(self):
        t = datetime(2024, 4, 15, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Monday
        self.assertEqual(st.session_type(t, SESSIONS), ("RTH", date(2024, 4, 15)))
        self.assertEqual(st.session_type(datetime(2024, 4, 14, 1, 0, tzinfo=timezone.utc), SESSIONS)[0], "OTHER")  # Saturday night ET
        self.assertEqual(st.session_type(datetime(2024, 4, 15, 20, 0, tzinfo=timezone.utc), SESSIONS)[0], "OTHER")  # 16:00 ET, after 15:30
        self.assertEqual(st.other_entry_day(datetime(2024, 4, 14, 1, 0, tzinfo=timezone.utc), SESSIONS), date(2024, 4, 15))
        self.assertEqual(st.other_entry_day(datetime(2024, 4, 15, 11, 0, tzinfo=timezone.utc), SESSIONS), date(2024, 4, 15))  # 07:00 ET, before the open
        self.assertEqual(st.other_entry_day(datetime(2024, 4, 15, 20, 0, tzinfo=timezone.utc), SESSIONS), date(2024, 4, 16))


class ReturnTests(unittest.TestCase):
    def test_short_profits_when_spy_falls_and_costs_are_not_included(self):
        bars = bars_for(date(2024, 4, 15), drift=-0.0002)
        nxt = bars_for(date(2024, 4, 16), 90.0)
        r = st.short_returns(bars, nxt, 10)
        self.assertGreater(r["m60"], 0)
        self.assertGreater(r["close"], r["m30"])
        self.assertAlmostEqual(r["next_close"], -(nxt[-1]["c"] / bars[10]["o"] - 1.0))

    def test_bracket_stop_first_when_both_touch_and_target_when_falling(self):
        up = bars_for(date(2024, 4, 15), drift=0.0003)
        self.assertEqual(st.short_returns(up, None, 5)["bracket_hit"], "STOP")
        down = bars_for(date(2024, 4, 15), drift=-0.0003)
        r = st.short_returns(down, None, 5)
        self.assertEqual((r["bracket_hit"], r["bracket"]), ("TARGET", st.TARGET))

    def test_entry_index_and_bad_inputs(self):
        bars = bars_for(date(2024, 4, 15))
        self.assertEqual(st.entry_index(bars, bars[7]["t"]), 7)
        self.assertIsNone(st.entry_index(bars, bars[-1]["t"] + timedelta(minutes=5)))
        self.assertIsNone(st.short_returns(bars, None, None))


class RunTests(unittest.TestCase):
    def test_end_to_end_with_fake_bars(self):
        items = [{"id": i, "created_at": f"2024-04-15T15:0{i}:00Z", "headline": h, "summary": "", "symbols": ["SPY"]}
                 for i, h in enumerate(["Iran missile attack on Israel", "Israel war escalates", "Iran drone attack reported"], 1)]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "n.jsonl").write_text("\n".join(json.dumps(x) for x in items) + "\n")
            days = sorted(SESSIONS)
            fake = lambda d: bars_for(d, 100.0, -0.0001) if d.isoformat() in SESSIONS else []
            res = st.run(tmp / "n.jsonl", days, tmp / "o", fake, baseline_days=3)
            self.assertEqual((res["events_detected"], res["events_scored"]), (1, 1))
            self.assertEqual(res["by_type"]["RTH"]["m60"]["n"], 1)
            self.assertIn(res["verdict"], ("PROMISING", "REJECTED", "INCONCLUSIVE"))
            self.assertIn("S-043 results", (tmp / "o" / "s043_results.md").read_text())

    def test_verdict_rules(self):
        base = {"n": 30, "mean": 0.01, "lo": 0.005, "hi": 0.015}
        mk = lambda a, b: {"by_type": {"RTH": {"m60": a, "close": b}}}
        self.assertEqual(st.verdict(mk(base, {"n": 30, "mean": 0.0, "lo": -0.01, "hi": 0.01})), "PROMISING")
        neg = {"n": 40, "mean": -0.002, "lo": -0.004, "hi": 0.0}
        self.assertEqual(st.verdict(mk(neg, neg)), "REJECTED")
        self.assertEqual(st.verdict(mk({"n": 10, "mean": 0.01, "lo": 0.005, "hi": 0.02}, {"n": 10, "mean": 0.01, "lo": 0.005, "hi": 0.02})), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
