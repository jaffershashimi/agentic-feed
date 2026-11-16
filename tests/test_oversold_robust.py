import csv
import gzip
import json
import random
import tempfile
import unittest
from pathlib import Path

from consumer import oversold_panel as op
from consumer import oversold_robust as orb


class RobustTests(unittest.TestCase):
    def test_grid_has_36_configs_and_base_is_in_it(self):
        self.assertEqual(len(orb.Z_GRID) * len(orb.STOP_GRID) * len(orb.HOLD_GRID), 36)
        self.assertIn(orb.BASE[0], orb.Z_GRID)
        # candidates must be gathered at the LOOSEST z (closest to zero), then tightened per config
        self.assertEqual(orb.CANDIDATE_Z, -1.5)
        self.assertTrue(all(z <= orb.CANDIDATE_Z for z in orb.Z_GRID))

    def test_window_stats_buckets_by_entry_date(self):
        tr = [{"entry_date": "2018-03-01", "entry": 100.0, "exit": 101.0}, {"entry_date": "2023-03-01", "entry": 100.0, "exit": 99.0}]
        w = orb.window_stats(tr, cost=0.0)
        self.assertEqual((w["Development"]["n"], w["Judge"]["n"], w["Final"]["n"]), (1, 1, 0))
        self.assertAlmostEqual(w["Development"]["mean_pct"], 1.0)

    def test_end_to_end_on_a_tiny_random_panel_is_deterministic_and_restores_globals(self):
        rnd = random.Random(3)
        with tempfile.TemporaryDirectory() as tmp:
            panel = Path(tmp) / "panel"
            (panel / "daily").mkdir(parents=True)
            rows = []
            for sym in ("SPY", "AAA", "BBB", "CCC"):
                px = 100.0
                for i in range(1600):
                    px *= 1 + rnd.gauss(0.0004, 0.02)
                    y, doy = 2017 + (i // 250), i % 250
                    rows.append([sym, f"{y}-{min(1 + doy // 21, 12):02d}-{1 + doy % 21:02d}", px * 0.999, px * 1.01, px * 0.99, px, 5e6])
            with gzip.open(panel / "daily" / "part-0000.csv.gz", "wt", newline="") as fh:
                w = csv.writer(fh); w.writerow(["symbol", "date", "o", "h", "l", "c", "v"]); w.writerows(rows)
            (panel / "universe_by_year.json").write_text(json.dumps({str(y): ["AAA", "BBB", "CCC", "SPY"] for y in range(2017, 2027)}))
            before = (op.STOP, op.MAX_DAYS)
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            self.assertEqual(orb.main([str(panel), str(a)]), 0)
            self.assertEqual(orb.main([str(panel), str(b)]), 0)
            self.assertEqual((op.STOP, op.MAX_DAYS), before)
            ra = json.loads((a / "s042_robust.json").read_text())
            self.assertEqual(ra, json.loads((b / "s042_robust.json").read_text()))
            self.assertEqual(ra["grid_summary"]["configs"], 36)
            self.assertIn("S-042 robustness", (a / "s042_robust.md").read_text())


if __name__ == "__main__":
    unittest.main()
