import csv
import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from consumer import strategy_lab as lab


def _days(n, start=date(2022, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _walk(n, seed, drift=0.0004, vol=0.012, start=100.0):
    rng, c, bars = random.Random(seed), start, []
    for d in _days(n):
        o = c * (1 + rng.gauss(0, vol / 3))
        c = o * (1 + drift + rng.gauss(0, vol))
        h, l = max(o, c) * (1 + abs(rng.gauss(0, vol / 2))), min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        bars.append({"day": d, "o": o, "h": h, "l": l, "c": c, "v": 5_000_000})
    return bars


def _daily(n=700):
    syms = ["SPY", "QQQ", "TLT", "GLD", "DBC", "AAA", "BBB", "CCC", "DDD"]
    return {s: _walk(n, k, drift=0.0006 if s in ("SPY", "QQQ", "AAA") else 0.0001) for k, s in enumerate(syms)}


class IndicatorTests(unittest.TestCase):
    def test_rsi_bounds_and_extremes(self):
        up = lab.rsi_series([float(i) for i in range(1, 30)])
        self.assertEqual(up[-1], 100.0)
        down = lab.rsi_series([float(30 - i) for i in range(29)])
        self.assertLess(down[-1], 1e-6)
        self.assertIsNone(up[1])

    def test_bs_call_known_value(self):
        # S=K=100, T=1, sigma=0.2, r=0.04 -> about 9.93
        self.assertAlmostEqual(lab.bs_call(100, 100, 1.0, 0.2), 9.925, places=2)
        self.assertEqual(lab.bs_call(110, 100, 0.0, 0.2), 10.0)

    def test_curve_stats_drawdown(self):
        d = _days(4)
        s = lab.curve_stats(list(zip(d, [100, 120, 90, 110])))
        self.assertAlmostEqual(s["max_drawdown"], 0.25)
        self.assertAlmostEqual(s["total_return"], 0.10)


class LabTests(unittest.TestCase):
    def setUp(self):
        self.daily = _daily()

    def test_buy_and_hold_tracks_benchmark(self):
        s, _ = lab.run_all(self.daily)
        bh = s["results"]["BH"]["all"]["curve"]
        spy = self.daily["SPY"]
        lo = 200
        expect = spy[-1]["c"] / spy[lo + 1]["o"] - 1
        self.assertAlmostEqual(bh["total_return"], expect, delta=0.01)
        self.assertEqual(s["results"]["BH"]["judge_verdict"]["verdict"], "BENCHMARK")

    def test_deterministic_and_cash_conserved(self):
        a, ta = lab.run_all(self.daily)
        b, tb = lab.run_all({k: [dict(x) for x in v] for k, v in self.daily.items()})
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(ta, tb)
        for name in lab.STRATEGIES:
            self.assertEqual(a["results"][name]["all"]["curve"]["start_equity"], 1000.0)

    def test_pb1_trades_obey_rules(self):
        _, trades = lab.run_all(self.daily)
        pb = [t for t in trades if t["strategy"] == "PB1" and t["exit_day"]]
        self.assertTrue(pb)
        for t in pb:
            self.assertIn(t["reason"], ("EXIT_CLOSE_ABOVE_SMA5", "EXIT_TIME_10D", "EXIT_STOP_8PCT", "EXIT_STOP_GAP"))
            self.assertGreaterEqual(t["exit_day"], t["entry_day"])
            if t["reason"] == "EXIT_STOP_8PCT":
                self.assertAlmostEqual(t["ret"], 0.92 * (1 - lab.COST) - 1, places=4)
        open_at = {}
        for t in sorted(pb, key=lambda t: t["entry_day"]):
            live = [x for x in pb if x["entry_day"] <= t["entry_day"] < x["exit_day"]]
            self.assertLessEqual(len(live), 3)

    def test_market_gate_blocks_new_entries(self):
        d = self.daily
        d["MKT"] = [dict(b, c=1000 - i, o=1000 - i, h=1001 - i, l=999 - i) for i, b in enumerate(d["SPY"])]
        s, trades = lab.run_all(d, market="MKT")
        for name in ("PB1", "PB1I", "OPT1", "MR1"):
            self.assertEqual([t for t in trades if t["strategy"] == name], [], name)
        with self.assertRaises(ValueError):
            lab.run_all(self.daily, market="NOPE")

    def test_tf1_only_holds_names_above_trend(self):
        d = self.daily
        d["TLT"] = [dict(b, c=200 - i * 0.1, o=200 - i * 0.1, h=200.5 - i * 0.1, l=199.5 - i * 0.1) for i, b in enumerate(d["TLT"])]
        _, trades = lab.run_all(d)
        self.assertFalse([t for t in trades if t["strategy"] == "TF1" and t["symbol"] == "TLT"])

    def test_verdict_rules(self):
        bench = {"curve": {"sharpe": 1.0}, "trades": {}}
        good = {"trades": {"trades": 120, "profit_factor": 1.5, "mean_ret_ci95": [0.001, 0.01]},
                "curve": {"max_drawdown": 0.1, "sharpe": 1.4}}
        self.assertEqual(lab.verdict("PB1", good, bench)["verdict"], "PASS")
        weekly = dict(good, trades=dict(good["trades"], trades=20))
        self.assertEqual(lab.verdict("TF1", weekly, bench)["verdict"], "PASS_EXCEPT_SAMPLE")
        bad = dict(good, curve={"max_drawdown": 0.4, "sharpe": 0.5})
        self.assertEqual(lab.verdict("PB1", bad, bench)["verdict"], "FAIL")

    def test_cli_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "daily.csv"
            with open(p, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume"])
                for s, bars in self.daily.items():
                    for b in bars:
                        w.writerow([s, f"{b['day']}T00:00:00Z", b["o"], b["h"], b["l"], b["c"], b["v"]])
            self.assertEqual(lab.main(["--daily", str(p), "--out", tmp, "--market", "SPY"]), 0)
            s = json.loads((Path(tmp) / "summary_lab.json").read_text())
            self.assertEqual(s["results"]["OPT1"]["data_class"], "SYNTHETIC")
            self.assertEqual(set(s["results"]), set(lab.STRATEGIES))
            self.assertTrue((Path(tmp) / "trades_lab.csv").exists())


if __name__ == "__main__":
    unittest.main()
