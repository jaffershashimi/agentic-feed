import json
import tempfile
import unittest
from pathlib import Path

from feed_pipeline import history as hi
from scripts import privacy_check

# Synthetic inputs shaped like the published files; values are invented.
RESEARCH = {"schemaVersion": "research-run/1", "runKey": "abc123def4567890", "codeCommit": "f0c14efcc6b8", "availableAt": "2026-01-09",
            "uniqueObservationCount": 100, "scenarioCount": 5000, "policy": {"stop": 0.15, "target": 0.3},
            "metrics": {"call_above_sma200": {"holdout": {"n": 40, "win_rate": 0.3, "mean_net": -0.05, "mean_ci95": [-0.09, -0.01],
                                                          "ci_excludes_zero": True, "payoff_ratio": 1.6, "p05": -0.35, "extra": 1}},
                        "cash": {"holdout": {"n": 0, "mean_net": 0.0}}},
            "tailStress": {"label": "TAIL_STRESS_SYNTHETIC", "iv_multiplier": 1.6, "overnight_gap": -0.07,
                           "results": {"call_every_session": {"mean_net": -0.6, "p05": -0.8}}}}
RISK = {"schema": "risk-sim/1", "rule_sets": {"size_score_8": {"rules": {"size": 0.1}, "results": [
    {"win_prob_assumed": 0.3, "breakeven_win_prob": 0.43, "expected_return_per_trade_pct": -0.65, "p_brake_within_horizon": 1.0, "paths": 9}]}}}
VOL = {"schema": "vol-core/1", "session": "2026-01-09", "rv20_pct": {"close_to_close": 10.0}, "vrp_vol_pts": 8.0, "iv_pctile_252": 70.0}
FEED = {"schema": "agentic-feed/1", "session": "2026-01-09", "vol": {"vix": 18.0, "vix_over_rv20": 1.8, "term_3m_over_1m": 1.05},
        "regime": {"label": "neutral"}, "trend": {"above_sma200": True}}


class SummarizeTest(unittest.TestCase):
    def test_fields_and_wrong_schemas_become_null(self):
        rec = hi.summarize(RESEARCH, RISK, VOL, FEED)
        self.assertEqual(rec["session"], "2026-01-09")
        h = rec["research"]["holdout"]["call_above_sma200"]
        self.assertEqual((h["n"], h["mean_ci95"], h["worst"]), (40, [-0.09, -0.01], None))
        self.assertNotIn("extra", h)
        self.assertNotIn("paths", rec["riskSim"]["size_score_8"]["results"][0])
        bad = hi.summarize(dict(RESEARCH, schemaVersion="research-run/2"), {"schema": "x"}, None, "not a dict")
        for k in ("research", "riskSim", "volCore", "feed"):
            self.assertNotIn(k, bad)
        self.assertIsNone(bad["session"])

    def test_key_tracks_every_input(self):
        base = hi.summarize(RESEARCH, RISK, VOL, FEED)["key"]
        self.assertEqual(base, hi.summarize(json.loads(json.dumps(RESEARCH)), RISK, VOL, FEED)["key"])
        self.assertNotEqual(base, hi.summarize(dict(RESEARCH, runKey="other"), RISK, VOL, FEED)["key"])
        self.assertNotEqual(base, hi.summarize(RESEARCH, RISK, dict(VOL, vrp_vol_pts=9.0), FEED)["key"])
        self.assertNotEqual(base, hi.summarize(RESEARCH, RISK, VOL, dict(FEED, session="2026-01-12"))["key"])


class StoreTest(unittest.TestCase):
    def test_append_is_idempotent_and_skips_damaged_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / hi.HISTORY
            rec = hi.summarize(RESEARCH, RISK, VOL, FEED)
            self.assertTrue(hi.append(path, rec))
            self.assertFalse(hi.append(path, rec))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("{not json\n" + json.dumps({"schema": "other"}) + "\n")
            self.assertTrue(hi.append(path, hi.summarize(dict(RESEARCH, runKey="next"), RISK, VOL, FEED)))
            self.assertEqual(len(hi.read_history(path)), 2)
            self.assertEqual(len(path.read_text().splitlines()), 4, "damaged lines stay; nothing is rewritten")

    def test_run_end_to_end_writes_log_and_passes_privacy_scan(self):
        with tempfile.TemporaryDirectory() as feed_dir, tempfile.TemporaryDirectory() as out_dir:
            for name, obj in (("research.json", RESEARCH), ("risk-sim.json", RISK), ("vol-core.json", VOL), ("desk-feed.json", FEED)):
                (Path(feed_dir) / name).write_text(json.dumps(obj))
            self.assertTrue(hi.run(Path(feed_dir), Path(out_dir)))
            self.assertFalse(hi.run(Path(feed_dir), Path(out_dir)), "an unchanged run appends nothing")
            log = (Path(out_dir) / hi.LOG).read_text()
            for text in ("Runs recorded: 1", "| call_above_sma200 | 40 | 30.0% | -5.0% | -9.0% to -1.0% |", "not historical option quotes",
                         "| size_score_8 | 30% | 43.0% | -0.65% | 100% |", "Tail stress (TAIL_STRESS_SYNTHETIC", "VRP 8.00"):
                self.assertIn(text, log)
            self.assertEqual(privacy_check.scan(Path(out_dir)), [])

    def test_missing_inputs_still_write_a_log(self):
        with tempfile.TemporaryDirectory() as feed_dir, tempfile.TemporaryDirectory() as out_dir:
            hi.run(Path(feed_dir), Path(out_dir))
            self.assertIn("Runs recorded: 1", (Path(out_dir) / hi.LOG).read_text())
            self.assertIn("No runs recorded yet.", hi.render_log([]))


if __name__ == "__main__":
    unittest.main()
