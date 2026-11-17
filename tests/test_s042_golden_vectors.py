import json
import unittest
from pathlib import Path

from scripts import make_s042_golden as g

FIXTURE = Path(__file__).parent / "fixtures" / "s042_golden_vectors.json"


class GoldenVectorTests(unittest.TestCase):
    def test_the_published_file_is_exactly_what_the_engine_produces(self):
        dates, data = g.build()
        universe = {2025: set(g.STOCKS), 2026: set(g.STOCKS)}
        out = g.answers(dates, data, universe)
        pub = json.loads(FIXTURE.read_text())
        self.assertEqual(pub["expected"], json.loads(json.dumps(out)))
        self.assertEqual(pub["eval_dates"], dates[-g.EVAL_LAST:])

    def test_the_vectors_cover_stress_on_and_off_and_ineligible_names(self):
        pub = json.loads(FIXTURE.read_text())
        stress = {r["stress"] for r in pub["expected"].values()}
        self.assertEqual(stress, {True, False})
        self.assertTrue(any(r["signals"]["V2"] and not r["signals"]["V3"] for r in pub["expected"].values()))
        self.assertTrue(any(r["signals"]["V3"] for r in pub["expected"].values()))
        for r in pub["expected"].values():
            self.assertNotIn("LOWV", r["eligible"])      # too little dollar volume
            self.assertNotIn("PENNY", r["eligible"])     # price under 5 dollars

    def test_the_file_holds_no_real_market_data_or_names(self):
        pub = json.loads(FIXTURE.read_text())
        self.assertEqual(set(pub["bars"]), {"SPY"} | set(g.STOCKS))
        self.assertIn("Synthetic", pub["about"])


if __name__ == "__main__":
    unittest.main()
