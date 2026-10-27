import csv
import io
import json
import math
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

from consumer import paper_ledger
from consumer.replay_r2 import (LEDGER_FIELDS, PT, bs_call, implied_vol, main, occ, option_stop, replay, years_to)
from tests.test_replay_r2 import DAY, after, market

# Synthetic contracts only: invented strikes and Black-Scholes prices, not market data.
EXPIRIES = (date(2026, 11, 27), date(2026, 12, 4))  # the first is under 21 days from DAY, the second is the pick
DECISION = datetime.combine(DAY, time(6, 45), PT).astimezone(timezone.utc)


def option_bars(level, vol=0.25, special=None, skip=(), days=(DAY,)):
    """Flat 5m call bars priced at the decision time; the 06:45 entry bar's high is 2% above; special maps 'HH:MM'
    to price multipliers {'h', 'l', 'c'}; skip lists 'HH:MM' bars that are missing."""
    out = {}
    for expiry in EXPIRIES:
        for strike in range(85, 116):
            p = bs_call(level, strike, years_to(DECISION, expiry), vol)[0]
            bars = []
            for day in days:
                for i in range(78):
                    start = datetime.combine(day, time(6, 30), PT) + timedelta(minutes=5 * i)
                    hhmm = start.strftime("%H:%M")
                    if day == DAY and hhmm in skip:
                        continue
                    bar = {"start": start.astimezone(timezone.utc), "o": p, "h": p, "l": p, "c": p, "v": 500.0}
                    if day == DAY and hhmm == "06:45":
                        bar["h"] = p * 1.02
                    for k, m in ((special or {}).get(hhmm, {}) if day == DAY else {}).items():
                        bar[k] = p * m
                    bars.append(bar)
            out[(expiry, float(strike))] = bars
    return {"DEMO": out}


def rows_of(ledger, arm, kind):
    return [r for r in ledger if r["arm"] == arm and r["kind"] == kind]


def check(ledger):
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "fills.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS)
            w.writeheader()
            w.writerows(ledger)
        loaded, errors = paper_ledger.load(path)
    report = paper_ledger.analyze(loaded, max_open=3, max_entries=5, option_stop=0.15, equity_stop=0.20,
                                  first_loss_stop=False)
    return errors, report["violations"], report["rejected"]


class OptionMathTests(unittest.TestCase):
    def test_black_scholes_round_trip_and_helpers(self):
        price, delta = bs_call(100, 100, 0.1, 0.3)
        self.assertAlmostEqual(implied_vol(price, 100, 100, 0.1), 0.3, places=5)
        self.assertTrue(0.5 < delta < 0.6)
        self.assertIsNone(implied_vol(1.0, 100, 90, 0.1))  # below intrinsic value: no volatility fits
        self.assertEqual(occ("NVDA", date(2026, 11, 13), 220.0), "NVDA261113C00220000")
        self.assertEqual(occ("XLF", date(2026, 11, 13), 56.5), "XLF261113C00056500")
        self.assertEqual(option_stop(4.95), 4.21)
        self.assertEqual(option_stop(0.69), 0.59)


class OptionArmTests(unittest.TestCase):
    def test_arm_a_buys_r2_contract_and_arm_b_takes_stock_when_iv_is_rich(self):
        bars, daily, level = market()
        ledger, parts, decisions, _keys = replay(bars, daily, [DAY], option_bars=option_bars(level))
        (entry,) = rows_of(ledger, "REPLAY-A-R2-DEMO", "entry")
        d = decisions[0]
        self.assertEqual((d["arm_a"], d["arm_b"]), ("OPTION", "STOCK"))  # rv20 of the smooth rally is tiny: IV/RV >> 1.5
        self.assertEqual(d["option"]["expiry"], EXPIRIES[1])
        self.assertTrue(0.30 <= d["option"]["delta"] <= 0.60)
        eligible = [c for c in d["option"]["candidates"] if c.get("eligible")]
        self.assertEqual(min(abs(c["delta"] - 0.45) for c in eligible), abs(d["option"]["delta"] - 0.45))
        self.assertTrue(0 < len(d["option"]["candidates"]) <= 10)  # at most the 10 strikes nearest delta 0.45 in the band
        self.assertEqual(entry["instrument"], "option")
        self.assertEqual(entry["trade_id"], "R2A-DEMO-20261110-0645")
        self.assertIn(occ("DEMO", EXPIRIES[1], d["option"]["strike"]), entry["source"])
        qty = int(entry["qty"])
        self.assertEqual(qty, int(750 // (float(entry["price"]) * 100 + 0.68)))  # score 9: size $750
        self.assertEqual(entry["fees"], f"{0.68 * qty:.2f}")
        self.assertEqual(float(rows_of(ledger, "REPLAY-A-R2-DEMO", "stop_set")[0]["price"]), option_stop(float(entry["price"])))
        self.assertEqual(rows_of(ledger, "REPLAY-B-R2-DEMO", "entry")[0]["instrument"], "equity")
        self.assertEqual(d["exit_a"], "UNRESOLVED")
        self.assertEqual(len(parts), 10)  # five criteria for each arm's trade
        self.assertEqual(check(ledger), ([], [], []))

    def test_arm_b_takes_the_option_when_iv_is_not_rich(self):
        bars, daily, level = market()
        with mock.patch("consumer.replay_r2.rv20", return_value=0.22):
            ledger, _p, decisions, _k = replay(bars, daily, [DAY], option_bars=option_bars(level))
        self.assertEqual((decisions[0]["arm_a"], decisions[0]["arm_b"]), ("OPTION", "OPTION"))
        b_entry = rows_of(ledger, "REPLAY-B-R2-DEMO", "entry")[0]
        self.assertTrue(b_entry["source"].endswith("ARMB_OPTION_PATH"))
        self.assertEqual(b_entry["price"], rows_of(ledger, "REPLAY-A-R2-DEMO", "entry")[0]["price"])

    def test_stop_target_and_mirrored_underlying_exit(self):
        bars, daily, level = market()
        ledger, _p, decisions, _k = replay(bars, daily, [DAY],
                                           option_bars=option_bars(level, special={"09:00": {"l": 0.5}}))
        (x,) = rows_of(ledger, "REPLAY-A-R2-DEMO", "exit")
        self.assertTrue(x["source"].endswith("SELL_BAR_LOW;EXIT_STOP_15PCT"))
        self.assertEqual(datetime.fromisoformat(x["occurred_at"]).astimezone(PT).strftime("%H:%M"), "09:00")
        self.assertIn("realistic_a", decisions[0])
        ledger, _p, _d, _k = replay(bars, daily, [DAY],
                                    option_bars=option_bars(level, special={"09:00": {"c": 1.5, "h": 1.5}}))
        (x,) = rows_of(ledger, "REPLAY-A-R2-DEMO", "exit")
        self.assertTrue(x["source"].endswith("EXIT_TARGET_30PCT_NEXT_BAR"))
        self.assertEqual(datetime.fromisoformat(x["occurred_at"]).astimezone(PT).strftime("%H:%M"), "09:05")
        earnings = {("DEMO", after(DAY, 1)[0], "am")}  # the underlying exits at the 12:55 bar
        ledger, _p, _d, _k = replay(bars, daily, [DAY], earnings=earnings, option_bars=option_bars(level))
        (x,) = rows_of(ledger, "REPLAY-A-R2-DEMO", "exit")
        self.assertTrue(x["source"].endswith("EXIT_MIRROR_EXIT_EARNINGS_BEFORE_CLOSE"))
        self.assertEqual(datetime.fromisoformat(x["occurred_at"]).astimezone(PT).strftime("%H:%M"), "12:55")
        self.assertEqual(check(ledger), ([], [], []))

    def test_missing_data_and_no_fit(self):
        bars, daily, level = market()
        _l, _p, decisions, _k = replay(bars, daily, [DAY], option_bars=option_bars(level, skip=("06:40",)))
        self.assertEqual((decisions[0]["arm_a"], decisions[0]["arm_b"]), ("MISSING_DATA", "STOCK"))
        ledger, _p, decisions, _k = replay(bars, daily, [DAY], option_bars=option_bars(level, vol=1.5))
        self.assertEqual(decisions[0]["arm_a"], "NO_FIT")  # every candidate costs more than $750
        self.assertFalse(rows_of(ledger, "REPLAY-A-R2-DEMO", "entry"))
        _l, _p, decisions, _k = replay(bars, daily, [DAY], option_bars={})
        self.assertEqual(decisions[0]["arm_a"], "MISSING_DATA")

    def test_cli_with_option_bars(self):
        bars, daily, level = market()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with open(root / "bars.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume"])
                for b in bars["DEMO"]:
                    w.writerow(["DEMO", b["start"].isoformat(), b["o"], b["h"], b["l"], b["c"], b["v"]])
            with open(root / "daily.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume"])
                for d in daily["DEMO"]:
                    w.writerow(["DEMO", f"{d['day']}T00:00:00Z", d["c"], d["h"], d["c"], d["c"], d["v"]])
            with open(root / "options.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["underlying", "expiration", "strike", "begins_at", "open", "high", "low", "close", "volume"])
                for (expiry, strike), obs in option_bars(level)["DEMO"].items():
                    for b in obs:
                        w.writerow(["DEMO", expiry, strike, b["start"].isoformat(), b["o"], b["h"], b["l"], b["c"], b["v"]])
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--bars", str(root / "bars.csv"), "--daily", str(root / "daily.csv"), "--days", "2026-11-10",
                             "--option-bars", str(root / "options.csv"), "--out", str(root / "out")])
            self.assertEqual(code, 0)
            summary = json.loads(buf.getvalue())
            self.assertEqual((summary["entries"], summary["arm_a"], summary["arm_b"]), (1, {"OPTION": 1}, {"STOCK": 1}))
            decisions = json.loads((root / "out" / "decisions.json").read_text())
            self.assertEqual(decisions[0]["option"]["expiry"], "2026-12-04")


if __name__ == "__main__":
    unittest.main()
