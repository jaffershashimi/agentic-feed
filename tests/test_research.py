import json
import math
import os
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from feed_pipeline import research as rs

# Synthetic fixtures only: invented index and VIX paths. Mechanics tests, never historical evidence.


def sessions(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_rows(n=2900, seed=1, start=date(2012, 1, 2)):
    rng, s, v, rows = random.Random(seed), 1500.0, 18.0, []
    for d in sessions(start, n):
        v = max(9.0, min(60.0, v * math.exp(rng.gauss(0, 0.06))))
        s *= math.exp(rng.gauss(0.0002, v / 100 / math.sqrt(252)))
        rows.append((d, s, v))
    return rows


def write_csvs(folder, rows, spx_extra="", vix_extra=""):
    spx = "DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(f"{d:%m/%d/%Y},{s},{s*1.01},{s*0.99},{s}\n" for d, s, _ in rows) + spx_extra
    vix = "DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(f"{d:%m/%d/%Y},{v},{v},{v},{v}\n" for d, _, v in rows) + vix_extra
    Path(folder, "SPX_History.csv").write_text(spx)
    Path(folder, "VIX_History.csv").write_text(vix)


class OutcomeTest(unittest.TestCase):
    def path(self, moves, vix=20.0):
        rows, s = [], 100.0
        for i, m in enumerate([0.0] + moves):
            s *= 1 + m
            rows.append((date(2022, 1, 3) + timedelta(days=i), s, vix))
        return rows

    def test_gap_through_stop_fills_at_the_close_not_the_level(self):
        o = rs.trade_outcome(self.path([-0.08] + [0.0] * 12), 0, "call")
        self.assertEqual(o["exit"], "stop")
        self.assertLess(o["net"], -0.15 - 0.2, "an 8% gap costs far more than the 15% stop")

    def test_target_and_time_exits_and_costs(self):
        up = rs.trade_outcome(self.path([0.03] + [0.0] * 12), 0, "call")
        self.assertEqual(up["exit"], "target")
        flat = rs.trade_outcome(self.path([0.0005] * 13, vix=10.0), 0, "call")
        self.assertIn(flat["exit"], ("stop", "time"))
        self.assertLess(flat["net"], 0, "theta, spread and fees lose money in a flat market")
        self.assertIsNone(rs.trade_outcome(self.path([0.0] * 5), 0, "call"), "history too short to resolve")

    def test_put_mirrors_call(self):
        self.assertEqual(rs.trade_outcome(self.path([-0.03] + [0.0] * 12), 0, "put")["exit"], "target")


class RunTest(unittest.TestCase):
    def test_manifest_contract_splits_and_purging(self):
        rows = synthetic_rows()
        m, reused = rs.build(rows, {"SPX": "a", "VIX": "b"}, None, rs.datetime(2026, 10, 24, tzinfo=rs.timezone.utc))
        self.assertFalse(reused)
        for field in ("datasetHash", "codeHash", "policyHash", "strategyHash", "costModelHash", "seed", "dataClass", "availableAt",
                      "splits", "uniqueObservationCount", "scenarioCount", "attemptedModelCount", "metrics", "limitations", "artifactHash"):
            self.assertIn(field, m)
        self.assertEqual(m["dataClass"], "HISTORICAL_UNDERLYING_MODELED_OPTIONS")
        self.assertFalse(m["liveExecution"])
        self.assertLess(m["uniqueObservationCount"], m["scenarioCount"], "resamples are counted separately from observations")
        self.assertGreater(m["scenarioCount"], 900_000)
        self.assertGreater(m["coverage"]["call_every_session"]["purged_cross_split"], 0)
        self.assertEqual(m["metrics"]["cash"]["holdout"]["mean_net"], 0.0)
        h = m["metrics"]["call_every_session"]["holdout"]
        self.assertEqual(sum(h["exits"].values()), h["n"])
        self.assertTrue(h["mean_ci95"][0] <= h["mean_net"] <= h["mean_ci95"][1])
        self.assertEqual(m["tailStress"]["label"], "TAIL_STRESS_SYNTHETIC")

    def test_cache_reuse_and_invalidation(self):
        rows = synthetic_rows(n=1800, start=date(2018, 1, 2))
        now = rs.datetime(2026, 10, 24, tzinfo=rs.timezone.utc)
        a, _ = rs.build(rows, {}, None, now)
        b, reused = rs.build(rows, {}, a, now)
        self.assertTrue(reused)
        self.assertIs(b, a, "unchanged inputs reuse the result instead of manufacturing a new run")
        c, reused2 = rs.build(rows + [(rows[-1][0] + timedelta(days=3), rows[-1][1] * 1.01, 19.0)], {}, a, now)
        self.assertFalse(reused2)
        self.assertNotEqual(c["runKey"], a["runKey"])
        self.assertEqual(c["attemptedModelCount"], a["attemptedModelCount"], "same strategies: no new attempted models")

    def test_bad_data_is_refused_or_skipped(self):
        with self.assertRaises(ValueError):
            rs.build(synthetic_rows(n=100), {}, None, rs.datetime(2026, 10, 24, tzinfo=rs.timezone.utc))
        rows = synthetic_rows(n=50)
        dup = rs.align([(d, s, s, s, s) for d, s, _ in rows] + [(rows[0][0], 1.0, 1.0, 1.0, 1.0)], [(d, v) for d, _, v in rows] + [(rows[1][0], -5.0)])
        self.assertEqual(len(dup), 50, "duplicate dates keep the first row; non-positive VIX is dropped")


class MainTest(unittest.TestCase):
    def test_end_to_end_write_reuse_lock_and_fail_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_csvs(tmp, synthetic_rows(n=1500, start=date(2019, 1, 2)), spx_extra="bad,row,,,\n", vix_extra="01/01/2030,nan,nan,nan,nan\n")
            out = Path(tmp, "research.json")
            self.assertEqual(rs.main(["--out", str(out), "--csv-dir", tmp]), 0)
            first = out.read_text()
            self.assertEqual(rs.main(["--out", str(out), "--csv-dir", tmp]), 0)
            self.assertEqual(out.read_text(), first, "second identical run reuses the artifact")
            out.with_suffix(".lock").write_text("held")
            self.assertEqual(rs.main(["--out", str(out), "--csv-dir", tmp]), 0)  # skipped, not duplicated
            out.with_suffix(".lock").unlink()
            Path(tmp, "VIX_History.csv").write_text("DATE,CLOSE\n")  # corrupt input
            self.assertEqual(rs.main(["--out", str(out), "--csv-dir", tmp]), 1)
            self.assertEqual(out.read_text(), first, "a failed run leaves the previous artifact untouched")
            self.assertFalse(out.with_suffix(".lock").exists())
            self.assertEqual(json.loads(first)["schemaVersion"], "research-run/1")


if __name__ == "__main__":
    unittest.main()
