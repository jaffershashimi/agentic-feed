import csv
import json
import tempfile
import unittest
from pathlib import Path

from consumer import vix_opex_evidence as ve

HEADER = "date,vix,d1,w1,w2,m1,m3,m6,y1,worst3m,vix_chg20,first_in_run"
# three small episodes: two events, one event, three events
ROWS = [
    "2000-01-21,31.0,1.0,2.0,3.0,4.0,5.0,6.0,7.0,-2.0,-1.0,1",
    "2000-02-18,33.0,-1.0,2.0,3.0,5.0,6.0,7.0,8.0,-6.0,-2.0,0",
    "2001-05-18,41.0,0.5,-1.0,2.0,3.0,4.0,5.0,6.0,-11.0,-3.0,1",
    "2002-08-16,35.0,-0.5,1.0,2.0,3.0,4.0,5.0,6.0,-1.0,-4.0,1",
    "2002-09-20,36.0,1.5,2.0,3.0,4.0,5.0,6.0,7.0,-3.0,-5.0,0",
    "2002-10-18,37.0,2.0,3.0,4.0,5.0,6.0,7.0,8.0,-12.0,-6.0,0",
]


def write(tmp, rows=ROWS):
    p = Path(tmp) / "e.csv"
    p.write_text(HEADER + "\n" + "\n".join(rows) + "\n")
    return p


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_episodes_start_at_first_in_run(self):
        rows = ve.load(write(self.tmp))
        self.assertEqual([r["episode"] for r in rows], [0, 0, 1, 2, 2, 2])
        self.assertEqual([len(e) for e in ve.episodes(rows)], [2, 1, 3])

    def test_first_event_must_start_an_episode(self):
        bad = ["2000-01-21,31.0,1,2,3,4,5,6,7,-2,-1,0"]
        with self.assertRaises(ValueError):
            ve.load(write(self.tmp, bad))

    def test_percentile_interpolates(self):
        self.assertEqual(ve.pctl([1, 2, 3, 4], 0.0), 1)
        self.assertEqual(ve.pctl([1, 2, 3, 4], 1.0), 4)
        self.assertAlmostEqual(ve.pctl([1, 2, 3, 4], 0.5), 2.5)

    def test_monte_carlo_tail_matches_exact_binomial(self):
        import random
        exact = ve.exact_tail(12, 10, 0.64)
        mc = ve.mc_tail(12, 10, 0.64, random.Random(1), 20000)
        self.assertAlmostEqual(mc, exact, delta=0.01)
        self.assertAlmostEqual(ve.exact_tail(5, 0, 0.3), 1.0)

    def test_run_is_deterministic_and_intervals_bracket_the_statistic(self):
        rows = ve.load(write(self.tmp))
        a, b = ve.run(rows, sims=500, seed=5), ve.run(rows, sims=500, seed=5)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        m1 = a["T1_event_bootstrap"]["m1"]
        self.assertLessEqual(m1["mean_ci"][0], m1["mean"] + 1e-9)
        self.assertGreaterEqual(m1["mean_ci"][1], m1["mean"] - 1e-9)
        self.assertEqual(a["T4_downside"]["-10"]["events"], "2 of 6")
        self.assertEqual(a["T4_downside"]["-5"]["events"], "3 of 6")
        self.assertEqual(a["T3_share_higher"]["first_in_run"]["m1"]["n"], 3)

    def test_verdict_labels(self):
        def res(lo1, p1, lo6, p6):
            return {"T2_episode_bootstrap": {"m1": {"median_ci": [lo1, 9]}, "m6": {"median_ci": [lo6, 9]}, "d1": {"median_ci": [-1, 1]}, "w1": {"median_ci": [-1, 1]}},
                    "T3_share_higher": {"first_in_run": {"m1": {"p_mc": p1}, "m6": {"p_mc": p6}}, "all_events": {"d1": {"share_pct": 50}, "w1": {"share_pct": 53}}}}
        self.assertEqual(ve.verdict(res(1, 0.01, 2, 0.02))["label"], "SUPPORTED")
        self.assertEqual(ve.verdict(res(1, 0.01, -1, 0.02))["label"], "PARTLY_SUPPORTED")
        self.assertEqual(ve.verdict(res(1, 0.20, 2, 0.20))["label"], "NOT_SUPPORTED")
        self.assertTrue(ve.verdict(res(1, 0.01, 2, 0.02))["near_term_coin_flip"]["d1"])

    def test_transcription_check_flags_an_edited_cell(self):
        # build a set whose averages equal the published ones is impractical; instead prove a large edit is flagged
        rows = ve.load(write(self.tmp))
        tc = ve.transcription_check(rows)
        self.assertIn("m1", tc["off_tolerance"])  # tiny sample is far from the published averages
        self.assertEqual(tc["episodes"], 3)

    def test_main_writes_json_and_markdown_without_error(self):
        out = Path(self.tmp) / "out"
        rc = ve.main([str(write(self.tmp)), str(out)])
        self.assertEqual(rc, 0)
        j = json.loads((out / "s057_results.json").read_text())
        self.assertEqual(j["sims"], 10000)
        self.assertIn("Verdict by the frozen rules", (out / "s057_results.md").read_text())


if __name__ == "__main__":
    unittest.main()
