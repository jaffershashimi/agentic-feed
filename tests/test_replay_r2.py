import csv
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta, timezone

from consumer import paper_ledger
from consumer.replay_r2 import LEDGER_FIELDS, PT, derive, main, replay, stop_price
from feed_pipeline import calendar
from tests.helpers import sessions

# Synthetic bars only: an invented symbol and invented prices, not market data.
DAY = date(2026, 11, 10)
A = 0.0005  # intraday half range, as a fraction of the level


def after(day, count):
    out, d = [], day
    while len(out) < count:
        d += timedelta(days=1)
        if calendar.is_session(d):
            out.append(d)
    return out


def intraday(day, level, volume, special=None):
    """78 flat 5m bars at level (high/low level x (1 +- A), typical price = level); special maps 'HH:MM' to bar overrides."""
    out = []
    for i in range(78):
        start = datetime.combine(day, time(6, 30), PT) + timedelta(minutes=5 * i)
        bar = {"o": level, "h": level * (1 + A), "l": level * (1 - A), "c": level}
        bar.update((special or {}).get(start.strftime("%H:%M"), {}))
        out.append({"start": start.astimezone(timezone.utc), "day": day, "v": volume, **bar})
    return out


def market(scale=1.0, adv=3_000_000, special=None, later=0):
    """240 rising daily sessions before DAY, 15 of them with 5m bars, DAY gapping 0.3% above, `later` flat days after."""
    days = sessions(DAY - timedelta(days=1), 240)
    daily = [{"day": d, "c": (50 + 0.2 * i) * scale, "h": (50 + 0.2 * i) * scale * 1.001, "v": adv} for i, d in enumerate(days)]
    level = daily[-1]["c"] * 1.003
    bars = [b for d in daily[-15:] for b in intraday(d["day"], d["c"], adv / 78)]
    bars += intraday(DAY, level, 60_000 * adv / 3_000_000, special(level) if callable(special) else special)
    for d in after(DAY, later):
        bars += intraday(d, level, 40_000)
    return {"DEMO": bars}, {"DEMO": daily}, level


def rows_of(ledger, kind):
    return [r for r in ledger if r["kind"] == kind]


class ReplayR2Tests(unittest.TestCase):
    def test_gap_breakout_enters_once_with_score_and_parts(self):
        bars, daily, level = market()
        ledger, parts, decisions, keys = replay(bars, daily, [DAY])
        self.assertEqual(keys, ["R2|2026-11-10|DEMO"])
        (entry,) = rows_of(ledger, "entry")
        self.assertEqual(entry["trade_id"], "R2B-DEMO-20261110-0645")
        self.assertEqual(entry["decision_id"], "R2-20261110-DEMO-0645")
        self.assertEqual(entry["arm"], "REPLAY-B-R2-DEMO")
        self.assertEqual(entry["score"], "9")  # context 2, trigger 2, liquidity 1, room 2, risk 2
        self.assertAlmostEqual(float(entry["price"]), round(level * 1.0005, 4))
        self.assertAlmostEqual(float(entry["qty"]) * float(entry["price"]), 750, delta=0.01)
        self.assertTrue(entry["source"].startswith("REPLAY_R2;R2_c879c06e24a5;BREAK_1H_HIGH"))
        self.assertEqual([(p["criterion"], p["points"]) for p in parts],
                         [("context", 2), ("trigger", 2), ("liquidity", 1), ("room", 2), ("risk", 2)])
        self.assertEqual(decisions[0]["arm_a"], "MISSING_DATA")
        self.assertEqual(decisions[0]["exit"], "UNRESOLVED")
        self.assertFalse(rows_of(ledger, "exit"))

    def test_stop_exit_at_next_open_and_checker_passes(self):
        bars, daily, level = market(special=lambda x: {"09:00": {"h": x * 1.25, "l": x * 0.75}})
        ledger, *_ = replay(bars, daily, [DAY])
        (stop,), (exit_,) = rows_of(ledger, "stop_set"), rows_of(ledger, "exit")
        self.assertEqual(float(stop["price"]), stop_price(float(rows_of(ledger, "entry")[0]["price"])))
        self.assertTrue(exit_["source"].endswith("EXIT_STOP_20PCT"))
        self.assertEqual(datetime.fromisoformat(exit_["occurred_at"]).astimezone(PT).strftime("%H:%M"), "09:05")
        self.assertAlmostEqual(float(exit_["price"]), round(level * 0.9995, 4))
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "fills.csv"
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS)
                w.writeheader()
                w.writerows(ledger)
            loaded, errors = paper_ledger.load(path)
        report = paper_ledger.analyze(loaded, max_open=3, max_entries=5, first_loss_stop=False)
        self.assertEqual((errors, report["violations"], report["rejected"]), ([], [], []))

    def test_target_and_time_stop(self):
        bars, daily, _level = market(special=lambda x: {"09:00": {"h": x * 1.31, "c": x * 1.31}})
        ledger, *_ = replay(bars, daily, [DAY])
        self.assertTrue(rows_of(ledger, "exit")[0]["source"].endswith("EXIT_TARGET_30PCT"))
        bars, daily, _level = market(later=7)
        ledger, *_ = replay(bars, daily, [DAY])
        (exit_,) = rows_of(ledger, "exit")
        self.assertTrue(exit_["source"].endswith("EXIT_TIME_STOP_5D"))
        self.assertEqual(datetime.fromisoformat(exit_["occurred_at"]).astimezone(PT).date(), after(DAY, 6)[-1])

    def test_filters_block_entries(self):
        for kw, flag in ((dict(scale=0.1), "F2_PRICE"), (dict(adv=500_000), "F1_LIQUIDITY")):
            bars, daily, _level = market(**kw)
            ledger, parts, decisions, _keys = replay(bars, daily, [DAY])
            self.assertEqual((ledger, parts), ([], []))
            self.assertIn(flag, decisions[0]["failed"])
            self.assertEqual(decisions[0]["action"], "SKIP")

    def test_earnings_block_lower_risk_and_force_exit(self):
        bars, daily, _level = market()
        _l, _p, decisions, _k = replay(bars, daily, [DAY], earnings={("DEMO", DAY, "pm")})
        self.assertIn("F6_EARNINGS", decisions[0]["failed"])
        ledger, parts, _d, _k = replay(bars, daily, [DAY], earnings={("DEMO", after(DAY, 1)[0], "am")})
        (entry,), (exit_,) = rows_of(ledger, "entry"), rows_of(ledger, "exit")
        self.assertEqual(entry["score"], "7")
        self.assertAlmostEqual(float(entry["qty"]) * float(entry["price"]), 500, delta=0.01)
        self.assertEqual(next(p["points"] for p in parts if p["criterion"] == "risk"), 0)
        self.assertTrue(exit_["source"].endswith("EXIT_EARNINGS_BEFORE_CLOSE"))
        self.assertEqual(datetime.fromisoformat(exit_["occurred_at"]).astimezone(PT).strftime("%H:%M"), "12:55")

    def test_macro_window_scores_risk_one(self):
        bars, daily, _level = market()
        _l, parts, _d, _k = replay(bars, daily, [DAY], macro={after(DAY, 2)[-1].isoformat()})
        self.assertEqual(next(p["points"] for p in parts if p["criterion"] == "risk"), 1)

    def test_no_look_ahead_and_deterministic(self):
        bars, daily, _level = market(later=7)
        full = replay(bars, daily, [DAY])
        self.assertEqual(full, replay(bars, daily, [DAY]))
        cut = datetime.combine(DAY, time(6, 50), PT).astimezone(timezone.utc)
        short = replay({"DEMO": [b for b in bars["DEMO"] if b["start"] < cut]}, daily, [DAY])
        self.assertEqual(rows_of(full[0], "entry"), rows_of(short[0], "entry"))
        self.assertEqual(full[1], short[1])

    def test_done_keys_are_never_replayed(self):
        bars, daily, _level = market()
        self.assertEqual(replay(bars, daily, [DAY], done={"R2|2026-11-10|DEMO"}), ([], [], [], []))

    def test_stop_rounds_up_and_derived_windows(self):
        self.assertEqual(stop_price(100.0), 80.0)
        self.assertEqual(stop_price(10.006), 8.01)
        bars = intraday(DAY, 20.0, 1000)
        h1, h4 = derive(bars, 60), derive(bars, 240)
        self.assertEqual(len(h1), 7)
        self.assertEqual((h1[-1]["end"] - h1[-1]["start"]).total_seconds(), 1800)
        self.assertEqual([b["start"].astimezone(PT).strftime("%H:%M") for b in h4], ["06:30", "10:30"])
        self.assertEqual(sum(b["v"] for b in derive(bars, 15)), 78_000)

    def test_cli_writes_outputs(self):
        bars, daily, _level = market()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with open(root / "bars.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume", "interpolated"])
                for b in bars["DEMO"]:
                    w.writerow(["DEMO", b["start"].isoformat(), b["o"], b["h"], b["l"], b["c"], b["v"], "false"])
            with open(root / "daily.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume"])
                for d in daily["DEMO"]:
                    w.writerow(["DEMO", f"{d['day']}T00:00:00Z", d["c"], d["h"], d["c"], d["c"], d["v"]])
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--bars", str(root / "bars.csv"), "--daily", str(root / "daily.csv"), "--days", "2026-11-10",
                             "--out", str(root / "out")])
            self.assertEqual(code, 0)
            summary = json.loads(buf.getvalue())
            self.assertEqual((summary["entries"], summary["unresolved"]), (1, 1))
            self.assertEqual(summary["files"], ["decisions.json", "replay_fills.csv", "replay_sessions_new.csv", "score_parts.csv"])
            (root / "events.json").write_text(json.dumps({"earnings": [{"symbol": "DEMO", "date": "2026-11-10", "timing": "x"}]}))
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(main(["--bars", str(root / "bars.csv"), "--daily", str(root / "daily.csv"),
                                       "--days", "2026-11-10", "--events", str(root / "events.json")]), 2)


if __name__ == "__main__":
    unittest.main()
