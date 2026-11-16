import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from consumer import overnight_monitor as om

T0 = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)


class SpreadTests(unittest.TestCase):
    def test_spread_bps_and_bad_quotes(self):
        self.assertAlmostEqual(om.spread_bps(99.9, 100.1), 20.0, places=2)
        for b, a in ((0, 100), (100, 0), (None, 100), (101, 100), (-1, 5)):
            self.assertIsNone(om.spread_bps(b, a))


class SummaryTests(unittest.TestCase):
    def test_tiers_percentiles_empty_share_and_errors(self):
        tiers = {"big": ["AAA", "BBB"], "small": ["CCC"]}
        lines = [
            {"polled_at": "x", "quotes": [{"sym": "AAA", "bp": 99.95, "ap": 100.05, "bs": 5, "as": 3}, {"sym": "BBB", "bp": 0, "ap": 0}]},  # BBB unusable, CCC missing
            {"polled_at": "y", "error": "HTTP 500"},
            {"polled_at": "z", "quotes": [{"sym": "AAA", "bp": 99.9, "ap": 100.1, "bs": 10, "as": 10}, {"sym": "CCC", "bp": 9.0, "ap": 9.5, "bs": 1, "as": 1}, {"sym": "BBB", "bp": 50, "ap": 50.05, "bs": 2, "as": 2}]},
        ]
        s = om.summarize(lines, tiers)
        self.assertEqual((s["polls"], s["errors"]), (2, 1))
        big = s["tiers"]["big"]
        self.assertEqual((big["quotes"], big["usable"]), (4, 3))
        self.assertAlmostEqual(big["no_usable_quote_share"], 0.25)
        self.assertAlmostEqual(big["median_bps"], 10.0, places=1)
        self.assertEqual(s["tiers"]["small"]["quotes"], 2)
        self.assertAlmostEqual(s["tiers"]["small"]["no_usable_quote_share"], 0.5)


class LoopTests(unittest.TestCase):
    def test_loop_records_errors_respects_min_interval_and_stops_at_until(self):
        clock = {"t": T0}
        calls, sleeps = [], []

        def call(method, path, body=None, host="paper"):
            self.assertEqual((method, host), ("GET", "data"))
            self.assertIn("feed=overnight", path)
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return {"quotes": {"AAA": {"bp": 1.0, "ap": 1.01, "bs": 1, "as": 1, "t": "x"}}}

        def sleep(sec):
            sleeps.append(sec)
            clock["t"] += timedelta(seconds=sec)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.jsonl"
            n = om.poll_loop(call, ["AAA"], out, T0 + timedelta(minutes=3), 5, now=lambda: clock["t"], sleep=sleep)
            self.assertEqual((n, sleeps), (3, [60, 60, 60]))  # interval is floored at 60 seconds
            lines = om.read_lines(out)
            self.assertEqual(len(lines), 3)
            self.assertIn("error", lines[1])
            self.assertEqual(lines[0]["quotes"][0]["sym"], "AAA")

    def test_start_waits_before_the_first_poll(self):
        clock = {"t": T0 - timedelta(minutes=5)}
        calls = []

        def call(method, path, body=None, host="paper"):
            calls.append(clock["t"])
            return {"quotes": {}}

        def sleep(sec):
            clock["t"] += timedelta(seconds=sec)

        with tempfile.TemporaryDirectory() as tmp:
            om.poll_loop(call, ["AAA"], Path(tmp) / "p.jsonl", T0 + timedelta(seconds=30), 60, now=lambda: clock["t"], sleep=sleep, start=T0)
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0], T0)

    def test_cli_summary_and_poll_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            basket = Path(tmp) / "b.json"
            basket.write_text(json.dumps({"tiers": {"t": ["AAA"]}}))
            out = Path(tmp) / "p.jsonl"
            out.write_text(json.dumps({"polled_at": "x", "quotes": [{"sym": "AAA", "bp": 1.0, "ap": 1.01, "bs": 1, "as": 1}]}) + "\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(om.main(["summary", "--basket", str(basket), "--out", str(out)]), 0)
            self.assertEqual(json.loads(buf.getvalue())["tiers"]["t"]["usable"], 1)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = om.main(["poll", "--basket", str(basket), "--out", str(out), "--until", "2026-11-17T09:00:00"], call=lambda *a, **k: {})
            self.assertEqual(code, 2)
            self.assertIn("timezone", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
