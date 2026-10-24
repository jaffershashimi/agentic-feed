import csv
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

from consumer.paper_ledger import COLUMNS, analyze, load, main

# Synthetic paper fills only: invented symbols, prices and ids.
SYM = "OPRA:DEMO261120C100.0"


def rows(*events):
    out = []
    for i, e in enumerate(events):
        base = dict(event_id=f"e{i}", mode="PAPER", arm="A", trade_id="t1", kind="entry", occurred_at="2026-10-26T14:00:00Z",
                    quote_time="2026-10-26T13:45:00Z", symbol=SYM, instrument="option", side="buy", qty="1", price="1.00",
                    ref_price="1.00", multiplier="100", fees="0.68", policy_hash="p1", decision_id=f"d{i}")
        if e.get("kind") == "stop_set":
            base.update(side="sell", quote_time="", ref_price="", fees="")
        if e.get("kind") == "exit":
            base.update(side="sell")
        out.append(dict(base, **e))
    return out


def write(tmp, data, columns=COLUMNS):
    path = pathlib.Path(tmp) / "fills.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)
    return path


def run(data, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        r, errors = load(write(tmp, data))
    return analyze(r, **kw), errors


def exit_(price, at="2026-10-26T15:00:00Z", **kw):
    return dict(kind="exit", price=price, ref_price=price, occurred_at=at, quote_time=at, **kw)


STOP = dict(kind="stop_set", price="0.85", occurred_at="2026-10-26T14:01:00Z")
WIN = [dict(), STOP, exit_("1.30")]


class LedgerTest(unittest.TestCase):
    def test_win_and_loss_statistics_after_fees(self):
        loss = [dict(trade_id="t2", occurred_at="2026-10-27T14:00:00Z", quote_time="2026-10-27T13:50:00Z"),
                dict(STOP, trade_id="t2", occurred_at="2026-10-27T14:00:30Z"),
                exit_("0.80", "2026-10-27T16:00:00Z", trade_id="t2")]
        out, errors = run(rows(*WIN, *loss))
        a = out["modes"]["PAPER"]["A"]
        self.assertEqual((errors, out["violations"], out["rejected"]), ([], [], []))
        self.assertEqual((a["closed_trades"], a["accepted_trades"], a["win_rate"]), (2, 2, 0.5))
        self.assertEqual((a["avg_win"], a["avg_loss"], a["net_after_fees"]), (28.64, -21.36, 7.28))
        self.assertEqual((a["expectancy"], a["max_drawdown"]), (3.64, -21.36))

    def test_issue35_stop_before_entry_is_not_protection(self):
        out, _ = run(rows(dict(occurred_at="2026-10-23T14:00:00Z", quote_time="2026-10-23T13:50:00Z"),
                          dict(STOP, occurred_at="2026-10-22T14:00:00Z"), exit_("1.30", "2026-10-23T15:00:00Z")))
        text = "\n".join(out["violations"])
        self.assertIn("no protective stop recorded within 5 minutes after entry", text)
        self.assertIn("stop recorded before the entry does not count", text)

    def test_issue35_inconsistent_instrument_rejects_the_trade(self):
        out, _ = run(rows(dict(), STOP, exit_("1.30", symbol="OPRA:OTHER261120C100.0", multiplier="1")))
        self.assertEqual(out["rejected"][0]["trade"], "PAPER/A/t1")
        self.assertIn("inconsistent symbol", out["rejected"][0]["reasons"][0])
        a = out["modes"]["PAPER"]["A"]
        self.assertEqual((a["accepted_trades"], a["rejected_trades"], a["net_after_fees"]), (0, ["t1"], 0))

    def test_unknown_fees_leave_the_trade_unmeasured(self):
        out, _ = run(rows(dict(fees=""), STOP, exit_("1.30")))
        a = out["modes"]["PAPER"]["A"]
        self.assertEqual((a["closed_trades"], a["accepted_trades"], a["unmeasured_fees_unknown"]), (1, 0, ["t1"]))
        out, _ = run(rows(dict(fees="0"), STOP, exit_("1.30", fees="0")))
        self.assertEqual(out["modes"]["PAPER"]["A"]["net_after_fees"], 30.0, "an explicit 0 fee is a known fee")

    def test_fills_better_than_the_cited_quote_are_rejected(self):
        out, _ = run(rows(dict(price="0.95", ref_price="1.00"), STOP, dict(exit_("1.30"), ref_price="1.20")))
        reasons = " ".join(out["rejected"][0]["reasons"])
        self.assertIn("filled below the ask", reasons)
        self.assertIn("filled above the bid", reasons)

    def test_drawdown_follows_closing_order(self):
        # Entry order is loss, loss, win (drawdown -62.72). Closing order is loss, win, loss (drawdown -31.36).
        def trade(tid, day_open, day_close, price):
            return [dict(trade_id=tid, occurred_at=f"2026-10-{day_open}T14:00:00Z", quote_time=f"2026-10-{day_open}T13:59:00Z"),
                    dict(STOP, trade_id=tid, occurred_at=f"2026-10-{day_open}T14:01:00Z"),
                    exit_(price, f"2026-10-{day_close}T15:00:00Z", trade_id=tid)]
        out, _ = run(rows(*trade("t1", 26, 30, "0.70"), *trade("t2", 27, 28, "0.70"), *trade("t3", 28, 29, "1.50")), max_open=3)
        a = out["modes"]["PAPER"]["A"]
        self.assertEqual(a["returns_pct"], [-31.36, 48.64, -31.36], "accepted trades are listed in closing order")
        self.assertEqual(a["max_drawdown"], -31.36)

    def test_rule_breaches_including_widening_and_stale_quotes(self):
        out, _ = run(rows(
            dict(quote_time="2026-10-26T13:00:00Z"),  # quote an hour old
            dict(STOP, price="0.80"),  # 20% stop: too loose
            dict(STOP, price="0.75", occurred_at="2026-10-26T14:30:00Z"),  # widened later
            dict(trade_id="t2", occurred_at="2026-10-26T14:30:00Z", quote_time="2026-10-26T14:29:00Z"),  # second position, no stop
            exit_("0.70"),
            exit_("1.10", "2026-10-26T15:10:00Z", trade_id="t2"),
            dict(trade_id="t3", occurred_at="2026-10-26T16:00:00Z", quote_time="2026-10-26T15:59:00Z"),
            dict(STOP, trade_id="t3", price="0.90", occurred_at="2026-10-26T16:00:10Z")))
        text = "\n".join(out["violations"])
        for needle in ("t1: e0 used a quote 60 minutes old", "t1: stop 0.8 is looser than 15%", "t1: stop widened from 0.8 to 0.75",
                       "t2: no protective stop", "t2: 2 positions open at once", "3 entries on 2026-10-26",
                       "entries after the first loss on 2026-10-26: t3"):
            self.assertIn(needle, text)
        self.assertEqual(out["rejected"], [], "rule breaches do not reject a well-formed trade")

    def test_modes_and_arms_are_separate(self):
        eq = [dict(arm="B", instrument="equity", multiplier="1", price="100", ref_price="100", symbol="AMEX:DEMO"),
              dict(STOP, arm="B", instrument="equity", multiplier="1", price="81", symbol="AMEX:DEMO", occurred_at="2026-10-26T14:02:00Z")]
        replay = [dict(r, mode="REPLAY", event_id="r" + r["event_id"]) for r in rows(*WIN)]
        out, _ = run(rows(*WIN, *eq) + replay)
        self.assertEqual(out["violations"], [])
        self.assertEqual((set(out["modes"]), set(out["modes"]["PAPER"])), ({"PAPER", "REPLAY"}, {"A", "B"}))

    def test_bad_rows_and_lifecycles(self):
        out, errors = run(rows(*WIN, dict(event_id="e0"), dict(price="nan"), dict(occurred_at="2026-10-26T14:00:00"),
                               dict(kind="exit", side="buy"), dict(mode="LIVE"), dict(qty=""), dict(quote_time=""),
                               dict(quote_time="2026-10-26T15:00:00Z"), dict(fees="-1")))
        self.assertEqual(len(errors), 9)
        self.assertEqual(out["modes"]["PAPER"]["A"]["accepted_trades"], 1)
        out, _ = run(rows(dict(trade_id="t9", kind="exit", side="sell"), *WIN, exit_("1.3", "2026-10-26T15:30:00Z")))
        reasons = {r["trade"]: " ".join(r["reasons"]) for r in out["rejected"]}
        self.assertIn("events without an entry", reasons["PAPER/A/t9"])
        self.assertIn("sold more than was bought", reasons["PAPER/A/t1"])

    def test_header_and_settings_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, errors = load(write(tmp, rows(*WIN), columns=[c for c in COLUMNS if c != "fees"]))
            self.assertEqual((r, errors), ([], ["header: missing columns fees"]))
        for bad in (dict(max_open=0), dict(max_entries=-1), dict(option_stop=float("nan")), dict(equity_stop=1.5),
                    dict(option_stop=0), dict(max_quote_age_minutes=float("inf")), dict(max_open=True)):
            with self.assertRaises(ValueError):
                analyze([], **bad)
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(main([str(write(tmp, rows(*WIN))), "--option-stop", "nan"]), 2)
                self.assertEqual(main([str(write(tmp, rows(*WIN)))]), 0)

    def test_cli_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                main([str(write(tmp, rows(*WIN)))])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["modes"]["PAPER"]["A"]["accepted_trades"], 1)
        self.assertIn("never mixed", out["sample_note"])


if __name__ == "__main__":
    unittest.main()
