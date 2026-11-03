import csv
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from consumer import replay_daily as rd


def _days(n, start=date(2024, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _uptrend(n=260, vol=6_000_000):
    """Slow steady climb with a 4-day dip every 20 sessions, so breakouts recur with room above."""
    bars = []
    for i, d in enumerate(_days(n)):
        c = 50 + 0.15 * i - (1.2 if i % 20 in (16, 17, 18, 19) else 0)
        bars.append({"day": d, "o": c - 0.2, "h": c + 0.4, "l": c - 0.5, "c": c, "v": vol})
    return bars


class EvaluateTests(unittest.TestCase):
    def test_needs_221_closes(self):
        bars = _uptrend(230)
        closes = [b["c"] for b in bars]
        e20, e21 = rd.ema(closes, 20), rd.ema(closes, 21)
        self.assertIsNone(rd.evaluate(bars, 219, closes, e20, e21, None, set()))

    def test_no_trigger_without_breakout(self):
        bars = _uptrend()
        for b in bars[-6:]:
            b.update(h=200, c=100, o=100, l=99)
        bars[-1].update(c=99, h=99.5)
        closes = [b["c"] for b in bars]
        e20, e21 = rd.ema(closes, 20), rd.ema(closes, 21)
        self.assertIsNone(rd.evaluate(bars, len(bars) - 1, closes, e20, e21, None, set()))

    def test_low_volume_fails_f1_and_unknown_earnings_scores_risk_1(self):
        bars = _uptrend(vol=500_000)
        closes = [b["c"] for b in bars]
        e20, e21 = rd.ema(closes, 20), rd.ema(closes, 21)
        hits = [rd.evaluate(bars, s, closes, e20, e21, None, set()) for s in range(221, len(bars))]
        hits = [h for h in hits if h]
        self.assertTrue(hits)
        for score, parts, failed in hits:
            self.assertIn("F1_LIQUIDITY", failed)
            self.assertEqual(parts["risk"], 1)
            self.assertEqual(score, sum(parts.values()))

    def test_pm_earnings_on_signal_day_fails_f6(self):
        bars = _uptrend()
        closes = [b["c"] for b in bars]
        e20, e21 = rd.ema(closes, 20), rd.ema(closes, 21)
        s = next(s for s in range(221, len(bars)) if rd.evaluate(bars, s, closes, e20, e21, None, set()))
        _, parts, failed = rd.evaluate(bars, s, closes, e20, e21, {bars[s]["day"]: "pm"}, set())
        self.assertIn("F6_EARNINGS_DAY", failed)
        self.assertEqual(parts["risk"], 2)


class ExitTests(unittest.TestCase):
    def _bars(self, rows):
        return [{"day": d, "o": o, "h": h, "l": l, "c": c, "v": 1} for d, (o, h, l, c) in zip(_days(len(rows)), rows)]

    def test_stop_before_target_in_one_session(self):
        bars = self._bars([(100, 140, 70, 100)])
        x, price, reason = rd.run_exit(bars, 0, 100, 80, 130, [0])
        self.assertEqual((x, reason), (0, "EXIT_STOP_20PCT"))
        self.assertAlmostEqual(price, 80 * 0.9995)

    def test_gap_below_stop_exits_at_open(self):
        bars = self._bars([(100, 101, 99, 100), (60, 62, 58, 61)])
        x, price, reason = rd.run_exit(bars, 0, 100, 80, 130, [0, 0])
        self.assertEqual((x, reason), (1, "EXIT_STOP_GAP"))
        self.assertAlmostEqual(price, 60 * 0.9995)

    def test_close_under_ema20_exits_next_open_or_unresolved(self):
        bars = self._bars([(100, 101, 99, 100), (100, 101, 97, 98), (97, 98, 96, 97)])
        x, price, reason = rd.run_exit(bars, 0, 100, 80, 130, [99, 99, 99])
        self.assertEqual((x, reason), (2, "EXIT_CLOSE_UNDER_EMA20"))
        self.assertAlmostEqual(price, 97 * 0.9995)
        self.assertEqual(rd.run_exit(bars[:2], 0, 100, 80, 130, [99, 99])[2], "UNRESOLVED")

    def test_time_stop_after_five_sessions(self):
        bars = self._bars([(100, 101, 99, 100)] * 6)
        x, _, reason = rd.run_exit(bars, 0, 100, 80, 130, [0] * 6)
        self.assertEqual((x, reason), (5, "EXIT_TIME_STOP_5D"))


class BacktestTests(unittest.TestCase):
    def test_trades_are_deterministic_and_no_lookahead(self):
        bars = _uptrend(320)
        trades, _ = rd.backtest({"AAA": bars})[:2]
        self.assertTrue(trades)
        self.assertEqual(trades, rd.backtest({"AAA": [dict(b) for b in bars]})[0])
        first = trades[0]
        cut = next(i for i, b in enumerate(bars) if b["day"].isoformat() == first["entry_day"])
        changed = [dict(b) for b in bars]
        for b in changed[cut + 1:]:
            b.update(o=1, h=1, l=1, c=1)
        again = rd.backtest({"AAA": changed})[0][0]
        for k in ("signal_day", "entry_day", "score", "fill", "context", "trigger", "liquidity", "room", "risk"):
            self.assertEqual(first[k], again[k])

    def test_one_position_per_symbol_and_size_by_score(self):
        trades, _, _ = rd.backtest({"AAA": _uptrend(320)})
        for a, b in zip(trades, trades[1:]):
            self.assertGreater(b["entry_day"], a["exit_day"])
        for t in trades:
            self.assertGreaterEqual(t["score"], 6)
            self.assertEqual(t["size"], 750 if t["score"] >= 9 else 500)
            self.assertGreaterEqual(t["stop"], t["fill"] * 0.80 - 1e-9)
            self.assertLess(t["stop"], t["fill"] * 0.80 + 0.01)

    def test_stats_known_numbers(self):
        trades = [{"trade_id": str(i), "exit_day": f"2025-01-{i + 1:02d}", "exit_reason": "X", "pnl": p}
                  for i, p in enumerate([10, -5, 20, -10])]
        s = rd.stats(trades)
        self.assertEqual(s["trades"], 4)
        self.assertEqual(s["expectancy"], 3.75)
        self.assertEqual(s["profit_factor"], 2.0)
        self.assertEqual(s["max_drawdown"], 10)
        self.assertEqual(s["worst_trade"], -10)
        lo, hi = s["expectancy_ci95"]
        self.assertLessEqual(lo, 3.75)
        self.assertGreaterEqual(hi, 3.75)

    def test_component_audit_flags_constant_criteria(self):
        rows = [{"context": 2, "trigger": 1, "liquidity": 2, "room": r, "risk": 1} for r in (0, 1, 2)]
        audit = rd.component_audit(rows)
        self.assertTrue(audit["trigger"]["invariant"])
        self.assertFalse(audit["room"]["invariant"])
        self.assertEqual(audit["room"]["counts"], {"0": 1, "1": 1, "2": 1})

    def test_cli_writes_files_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily.csv"
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume"])
                for b in _uptrend(320):
                    w.writerow(["AAA", f"{b['day']}T00:00:00Z", b["o"], b["h"], b["l"], b["c"], b["v"]])
            self.assertEqual(rd.main(["--daily", str(path), "--out", tmp]), 0)
            s = json.loads((Path(tmp) / "summary_daily.json").read_text())
            self.assertEqual(s["data_class"], "REPLAY-DAILY")
            self.assertIn("EARNINGS_UNKNOWN", s["flags"])
            self.assertIn("SURVIVORSHIP_BIAS", s["flags"])
            self.assertTrue(s["components"]["entries"]["liquidity"]["invariant"])
            self.assertTrue((Path(tmp) / "trades_daily.csv").exists())


if __name__ == "__main__":
    unittest.main()
