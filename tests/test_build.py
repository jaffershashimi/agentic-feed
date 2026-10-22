import copy
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from feed_pipeline import build
from feed_pipeline.sources import FetchResult
from tests.helpers import synthetic_results

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)  # Thursday before the open: session = Oct 21
GOLDEN = Path(__file__).parent / "fixtures" / "golden-feed.json"


class BuildTest(unittest.TestCase):
    def test_feed_is_valid_small_and_healthy(self):
        feed, health = build.build_feed(synthetic_results(date(2026, 10, 21)), NOW)
        self.assertEqual(build.validate(feed), [])
        self.assertLessEqual(len(build.encode(feed).encode()), build.MAX_FEED_BYTES)
        self.assertEqual(feed["session"], "2026-10-21")
        self.assertTrue(feed["data_health"]["core_ok"])
        self.assertEqual(feed["data_health"]["failed"], [])
        self.assertTrue(health["core_ok"])

    def test_golden_file(self):
        feed, _ = build.build_feed(synthetic_results(date(2026, 10, 21)), NOW)
        feed["generated_at"] = "fixed"
        if not GOLDEN.exists():  # first run writes the golden file; commit it deliberately
            GOLDEN.write_text(json.dumps(feed, indent=1, sort_keys=True) + "\n")
        self.assertEqual(feed, json.loads(GOLDEN.read_text()))

    def test_future_observations_do_not_leak(self):
        results = synthetic_results(date(2026, 10, 21))
        leaked = copy.deepcopy(results)
        for result in leaked.values():
            result.series.append((date(2026, 10, 22), 9999.0))
        base, _ = build.build_feed(results, NOW)
        with_future, _ = build.build_feed(leaked, NOW)
        for section in ("vol", "trend", "macro", "regime"):
            self.assertEqual(base[section], with_future[section], section)

    def test_core_failure_is_visible_and_not_fresh(self):
        results = synthetic_results(date(2026, 10, 21))
        results["VIX"] = FetchResult("cboe", "VIX", "synthetic://VIX", error="URLError: down")
        feed, health = build.build_feed(results, NOW)
        self.assertFalse(feed["data_health"]["core_ok"])
        self.assertIn("VIX", feed["data_health"]["failed"])
        self.assertIsNone(feed["vol"]["vix"])
        self.assertEqual(feed["regime"]["label"], "unavailable")
        self.assertFalse(build.is_fresh(feed, NOW))
        self.assertFalse(next(p for p in health["providers"] if p["name"] == "VIX")["ok"])

    def test_stale_series_are_flagged(self):
        results = synthetic_results(date(2026, 10, 15))  # several sessions old
        feed, _ = build.build_feed(results, NOW)
        self.assertIn("VIX", feed["data_health"]["stale"])
        self.assertFalse(feed["data_health"]["core_ok"])

    def test_freshness_follows_trading_sessions_not_24_hours(self):
        friday_close = datetime(2026, 10, 23, 22, 0, tzinfo=timezone.utc)
        feed, _ = build.build_feed(synthetic_results(date(2026, 10, 23)), friday_close)
        monday_prep = datetime(2026, 10, 26, 12, 52, tzinfo=timezone.utc)  # 5:52 PT, ~63h later
        self.assertTrue(build.is_fresh(feed, monday_prep))
        monday_after_close = datetime(2026, 10, 26, 20, 30, tzinfo=timezone.utc)
        self.assertFalse(build.is_fresh(feed, monday_after_close))

    def test_size_budget_is_enforced(self):
        feed, _ = build.build_feed(synthetic_results(date(2026, 10, 21)), NOW)
        feed["events"] = [{"date": "2026-10-22", "time_pt": None, "type": "x" * 100}] * 80
        self.assertTrue(any("budget" in p for p in build.validate(feed)))


if __name__ == "__main__":
    unittest.main()
