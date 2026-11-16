import csv
import gzip
import json
import random
import tempfile
import unittest
from pathlib import Path

from consumer import news_drop_study as nds
from consumer import news_events as ne


class ClassifyTests(unittest.TestCase):
    def n(self, headline, sym="AAA", t="2024-03-01T15:00:00Z", symbols=None):
        return {"headline": headline, "summary": "", "symbols": symbols or [sym], "created_at": t}

    def test_news_analyst_no_news(self):
        self.assertEqual(nds.classify("AAA", [self.n("AAA cuts full-year guidance")]), ("NEWS", "GUIDANCE"))
        self.assertEqual(nds.classify("AAA", [self.n("AAA shares are trading lower")]), ("NO_NEWS", None))
        self.assertEqual(nds.classify("AAA", [self.n("AAA price target lowered to $20")]), ("ANALYST", None))
        self.assertEqual(nds.classify("AAA", [self.n("BBB cuts guidance", sym="BBB")]), ("NO_NEWS", None))
        # the first informative headline by time decides the subgroup
        items = [self.n("SEC probe into AAA", t="2024-03-01T16:00:00Z"), self.n("AAA cuts guidance", t="2024-03-01T14:00:00Z")]
        self.assertEqual(nds.classify("AAA", items), ("NEWS", "GUIDANCE"))

    def test_windows(self):
        self.assertEqual(nds.window_of("2019-05-01"), "Development")
        self.assertEqual(nds.window_of("2023-05-01"), "Judge")
        self.assertEqual(nds.window_of("2026-05-01"), "Final")
        self.assertIsNone(nds.window_of("2016-05-01"))


class ReturnTests(unittest.TestCase):
    def test_event_returns_are_market_adjusted_and_entry_rules_differ(self):
        days = [f"2024-01-{i:02d}" for i in range(1, 25)]
        stock = {"date": days, "o": [100.0] * 24, "c": [100.0] * 24}
        spy = {"date": days, "o": [400.0] * 24, "c": [400.0] * 24}
        stock["c"][5] = 90.0          # event day, drop
        stock["o"][6] = 91.0          # next open
        for k in range(6, 17):
            stock["c"][k] = 99.0
        for k in range(6, 17):
            spy["c"][k] = 404.0       # SPY +1%
        er = nds.event_returns({"AAA": stock}, {d: i for i, d in enumerate(days)}, spy, {"sym": "AAA", "date": days[5]})
        self.assertAlmostEqual(er["C0_10"], (99 / 90 - 1) - (404 / 400 - 1))
        self.assertAlmostEqual(er["O1_10"], (99 / 91 - 1) - (404 / 400 - 1))

    def test_too_close_to_the_end_is_skipped(self):
        days = [f"2024-01-{i:02d}" for i in range(1, 12)]
        d = {"date": days, "o": [1.0] * 11, "c": [1.0] * 11}
        self.assertIsNone(nds.event_returns({"A": d}, {x: i for i, x in enumerate(days)}, d, {"sym": "A", "date": days[5]}))


class EndToEnd(unittest.TestCase):
    def test_runs_and_is_deterministic(self):
        rnd = random.Random(2)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "panel" / "daily").mkdir(parents=True)
            rows, dates = [], []
            for i in range(1700):
                y, doy = 2017 + i // 250, i % 250
                dates.append(f"{y}-{min(1 + doy // 21, 12):02d}-{1 + doy % 21:02d}")
            for sym in ("SPY", "AAA"):
                px = 100.0
                for i in range(1700):
                    px *= 1 + rnd.gauss(0.0003, 0.01)
                    rows.append([sym, dates[i], px, px * 1.01, px * 0.99, px, 5e6])
            with gzip.open(tmp / "panel" / "daily" / "part-0000.csv.gz", "wt", newline="") as fh:
                w = csv.writer(fh); w.writerow(["symbol", "date", "o", "h", "l", "c", "v"]); w.writerows(rows)
            (tmp / "panel" / "universe_by_year.json").write_text(json.dumps({str(y): ["AAA", "SPY"] for y in range(2017, 2027)}))
            evs = [{"sym": "AAA", "date": dates[i], "prev": dates[i - 1], "ret": -0.1} for i in (600, 900, 1200, 1500)]
            (tmp / "ev.json").write_text(json.dumps(evs))
            news = [{"id": 1, "created_at": "x", "headline": "AAA cuts guidance", "summary": "", "symbols": ["AAA"], "day": dates[600]}]
            (tmp / "news.jsonl").write_text("\n".join(json.dumps(n) for n in news) + "\n")
            a = nds.run(tmp / "panel", tmp / "ev.json", tmp / "news.jsonl", tmp / "o1")
            b = nds.run(tmp / "panel", tmp / "ev.json", tmp / "news.jsonl", tmp / "o2")
            self.assertEqual(a, b)
            self.assertEqual(a["events_scored"], 4)
            self.assertIn("news_drift_short", a["verdict"])
            self.assertIn("S-044 results", (tmp / "o1" / "s044_results.md").read_text())


class BootstrapTests(unittest.TestCase):
    def test_cluster_bootstrap_counts_a_date_once(self):
        v = ne.cluster_bootstrap([1.0] * 50 + [-1.0] * 2, ["d1"] * 50 + ["d2"] * 2, 2000)
        self.assertEqual((v["n"], v["clusters"]), (52, 2))
        self.assertGreater(v["hi"] - v["lo"], 1.0)  # two clusters: the interval is wide whatever the event count
        self.assertIsNone(ne.cluster_bootstrap([], []))


if __name__ == "__main__":
    unittest.main()
