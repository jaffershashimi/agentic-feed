import json
import math
import random
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from feed_pipeline import paperlab as pl
from feed_pipeline import research
from tests.test_research import synthetic_rows, write_csvs

# Synthetic fixtures only: invented index and VIX paths. Mechanics tests, never historical evidence.
NOW = datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)
ROWS = synthetic_rows(n=9300, seed=3, start=date(1990, 1, 2))


class PaperLabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = pl.run(ROWS, NOW)

    def test_scenario_bound_and_data_classes(self):
        syn = self.out["synthetic"]
        self.assertLessEqual(syn["tradeDraws"], pl.MAX_SCENARIOS)
        self.assertEqual(syn["tradeDraws"], sum(r["trade_draws"] for r in syn["results"].values()))
        self.assertEqual((self.out["historical"]["dataClass"], syn["dataClass"], self.out["paperFills"]["dataClass"]),
                         ("HISTORICAL_UNDERLYING_MODELED_OPTIONS", "SYNTHETIC_BLOCK_BOOTSTRAP_OF_HOLDOUT", "PAPER"))
        self.assertEqual(set(self.out["paperFills"]), {"dataClass", "status"}, "no paper fill data is ever published")
        self.assertFalse(self.out["liveExecution"])
        self.assertEqual(self.out["policyHash"], research.sha(research.POLICY))

    def test_the_bound_is_enforced(self):
        original = pl.MAX_SCENARIOS
        try:
            pl.MAX_SCENARIOS = 1000  # paths_each becomes 0, so nothing is drawn and the bound holds
            self.assertEqual(pl.run(ROWS, NOW)["synthetic"]["tradeDraws"], 0)
        finally:
            pl.MAX_SCENARIOS = original

    def test_costs_lower_returns_on_the_same_entries(self):
        h = self.out["historical"]["results"]
        for rule in pl.RULES:
            means = [h[f"{rule}/{name}"]["train"]["mean_net"] for name in ("liquid", "typical", "wide", "stressed")]
            self.assertEqual(means, sorted(means, reverse=True), f"{rule}: wider spreads and slippage must cost more")
            counts = {h[f"{rule}/{name}"]["holdout"]["n"] for name in pl.SCENARIOS}
            self.assertEqual(len(counts), 1, "every scenario re-prices the same entries")

    def test_missing_checks_skip_exit_tests(self):
        rng = random.Random(1)
        always_missing = dict(pl.SCENARIOS["liquid"], missing_check_prob=1.0)
        hold = research.POLICY["hold_sessions"]
        ends = {pl.outcome(ROWS, i, "call", always_missing, rng)["end"] - i for i in range(200, 400)}
        self.assertEqual(ends, {hold}, "with every interim check missed, every exit happens on the final day")
        self.assertIsNone(pl.outcome(ROWS, len(ROWS) - 3, "call", pl.SCENARIOS["liquid"], rng), "unresolved at the end of history")

    def test_delay_sigma_and_contract_sizing(self):
        self.assertAlmostEqual(pl.delay_sigma(16.0), 0.16 * math.sqrt(15 / (6.5 * 60 * 252)))
        c = self.out["contractsAffordable"]
        for k, f in pl.SIZES.items():
            self.assertEqual(c[k], math.floor(pl.ACCOUNT * f / c["atm_call_premium_usd"]))

    def test_synthetic_paths_are_deterministic_and_need_history(self):
        nets = [0.3, -0.15] * 20
        a = pl.synthetic_paths(nets, 0.1, 10, random.Random(5))
        self.assertEqual(a, pl.synthetic_paths(nets, 0.1, 10, random.Random(5)))
        self.assertEqual(a["trade_draws"], 10 * pl.PATHS_TRADES)
        self.assertEqual(pl.synthetic_paths(nets[:6], 0.1, 10, random.Random(5))["paths"], 0)

    def test_missing_data_report_and_refusal(self):
        gappy = ROWS[:500] + ROWS[510:]
        self.assertEqual(pl.missing_data(gappy)["gaps_over_4_days"], 1)
        with self.assertRaises(ValueError):
            pl.run(ROWS[:200], NOW)

    def test_cli_end_to_end_and_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            write_csvs(d, ROWS[-3000:], closes_only=True)
            out = Path(d) / "paper-lab.json"
            self.assertEqual(pl.main(["--out", str(out), "--csv-dir", d]), 0)
            self.assertEqual(json.loads(out.read_text())["schemaVersion"], "paper-lab/1")
            before = out.read_text()
            Path(d, "VIX_History.csv").write_text("")
            self.assertEqual(pl.main(["--out", str(out), "--csv-dir", d]), 1)
            self.assertEqual(out.read_text(), before, "a failed run leaves the previous artifact in place")


if __name__ == "__main__":
    unittest.main()
