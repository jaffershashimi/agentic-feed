import csv
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from consumer.paper_ledger import ALL_COLUMNS, analyze, load, main

# Synthetic paper fills only: invented symbols, prices and ids.


def rows(*events):
    out = []
    for i, e in enumerate(events):
        at = e.get("occurred_at", "2026-10-26T14:00:00Z")
        base = dict(event_id=f"e{i}", arm="A", trade_id="t1", kind="entry", occurred_at=at, symbol="DEMO",
                    instrument="option", side="buy", qty="1", price="1.00", multiplier="100", decision_id=f"d{i}",
                    leg="", fees="0.65", quote_time=at.replace(":00Z", ":00Z"), received_at=at, quote_delay="realtime",
                    quote_source="test")
        row = dict(base, **e)
        if row["kind"] == "stop_set":
            row.update(fees="", quote_time="", received_at="", quote_delay="")
        out.append(row)
    return out


def write(tmp, data):
    path = pathlib.Path(tmp) / "fills.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ALL_COLUMNS)
        w.writeheader()
        w.writerows(data)
    return path


def run(data, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        r, errors = load(write(tmp, data))
    return analyze(r, **kw), errors


WIN = [dict(), dict(kind="stop_set", side="sell", price="0.85", occurred_at="2026-10-26T14:01:00Z"),
       dict(kind="exit", side="sell", price="1.30", occurred_at="2026-10-26T15:00:00Z")]


class LedgerTest(unittest.TestCase):
    def test_win_and_loss_statistics_net_of_fees(self):
        loss = [dict(trade_id="t2", occurred_at="2026-10-27T14:00:00Z"),
                dict(trade_id="t2", kind="stop_set", side="sell", price="0.85", occurred_at="2026-10-27T14:00:30Z"),
                dict(trade_id="t2", kind="exit", side="sell", price="0.80", occurred_at="2026-10-27T16:00:00Z")]
        out, errors = run(rows(*WIN, *loss))
        a = out["arms"]["A"]
        self.assertEqual((errors, out["violations"], a["excluded"]), ([], [], {}))
        self.assertEqual((a["closed_trades"], a["win_rate"], a["avg_win"], a["avg_loss"]), (2, 0.5, 28.7, -21.3))
        self.assertEqual((a["net"], a["fees"], a["max_drawdown"]), (7.4, 2.6, -21.3))

    def test_drawdown_follows_closing_order(self):
        # t1 opens first but closes last with a loss; t2 opens later and closes first with a win.
        data = rows(dict(fees="0"), dict(kind="stop_set", side="sell", price="0.85", occurred_at="2026-10-26T14:00:10Z"),
                    dict(trade_id="t2", arm="A", occurred_at="2026-10-26T14:30:00Z", fees="0"),
                    dict(trade_id="t2", kind="stop_set", side="sell", price="0.85", occurred_at="2026-10-26T14:30:10Z"),
                    dict(trade_id="t2", kind="exit", side="sell", price="1.30", occurred_at="2026-10-26T15:00:00Z", fees="0"),
                    dict(kind="exit", side="sell", price="0.80", occurred_at="2026-10-26T16:00:00Z", fees="0"))
        out, _ = run(data, max_open=2)
        self.assertEqual(out["arms"]["A"]["max_drawdown"], -20.0)
        self.assertEqual(out["arms"]["A"]["returns_pct"], [30.0, -20.0])

    def test_open_trades_are_not_counted(self):
        out, _ = run(rows(*WIN[:2]))
        a = out["arms"]["A"]
        self.assertEqual((a["closed_trades"], a["open_trades"], a["win_rate"], a["net"]), (0, ["t1"], None, 0))

    def test_issue35_stop_before_entry_is_excluded(self):
        data = rows(dict(occurred_at="2026-10-23T14:00:00Z"),
                    dict(kind="stop_set", side="sell", price="0.85", occurred_at="2026-10-22T14:00:00Z"),
                    dict(kind="exit", side="sell", price="1.30", occurred_at="2026-10-23T15:00:00Z"))
        out, _ = run(data)
        a = out["arms"]["A"]
        self.assertEqual(a["closed_trades"], 0)
        self.assertIn("t1", a["excluded"])
        self.assertTrue(any("before the entry" in p for p in a["excluded"]["t1"]))
        self.assertTrue(any("no protective stop" in v for v in out["violations"]))

    def test_issue35_instrument_change_is_excluded(self):
        data = rows(dict(symbol="SPY", multiplier="100"),
                    dict(symbol="SPY", kind="stop_set", side="sell", price="0.85", occurred_at="2026-10-26T14:01:00Z"),
                    dict(symbol="QQQ", instrument="equity", multiplier="1", kind="exit", side="sell", price="1.30",
                         occurred_at="2026-10-26T15:00:00Z"))
        out, _ = run(data)
        a = out["arms"]["A"]
        self.assertEqual(a["closed_trades"], 0)
        self.assertIn("t1", a["excluded"])

    def test_wrong_multiplier_is_rejected(self):
        _, errors = run(rows(dict(multiplier="1")))
        self.assertTrue(any("multiplier" in e for e in errors))

    def test_stop_widening_is_a_violation(self):
        data = rows(*WIN[:2], dict(kind="stop_set", side="sell", price="0.90", occurred_at="2026-10-26T14:10:00Z"),
                    dict(kind="stop_set", side="sell", price="0.86", occurred_at="2026-10-26T14:20:00Z"),
                    WIN[2])
        out, _ = run(data)
        self.assertTrue(any("widened from 0.9 to 0.86" in v for v in out["violations"]))
        self.assertEqual(out["arms"]["A"]["closed_trades"], 0)

    def test_quote_evidence_is_required(self):
        cases = {"missing": dict(quote_time=""), "delayed": dict(quote_delay="delayed"),
                 "stale": dict(quote_time="2026-10-26T13:58:00Z"), "lookahead": dict(quote_time="2026-10-26T14:00:30Z")}
        for name, change in cases.items():
            with self.subTest(name):
                data = rows(dict(**change), *WIN[1:])
                out, errors = run(data)
                self.assertEqual(errors, [])
                self.assertEqual(out["arms"]["A"]["closed_trades"], 0)
                self.assertIn("t1", out["arms"]["A"]["excluded"])

    def test_missing_fees_excludes_from_measured(self):
        data = rows(dict(fees=""), *WIN[1:])
        out, _ = run(data)
        self.assertEqual(out["arms"]["A"]["closed_trades"], 0)
        self.assertTrue(any("fees missing" in p for p in out["arms"]["A"]["excluded"]["t1"]))

    def test_shorts_are_disabled(self):
        _, errors = run(rows(dict(kind="entry", side="sell")))
        self.assertTrue(any("shorts are disabled" in e for e in errors))
        _, errors = run(rows(dict(instrument="equity", multiplier="1", leg="short", side="sell")))
        self.assertTrue(any("spread legs must be options" in e for e in errors))

    def test_debit_spread_with_linked_legs(self):
        data = rows(dict(leg="long", symbol="OPRA:DEMO261120C100.0", price="2.00", fees="0.65"),
                    dict(leg="short", symbol="OPRA:DEMO261120C105.0", side="sell", price="0.80", fees="0.65"),
                    dict(leg="net", kind="stop_set", side="sell", price="0.84", occurred_at="2026-10-26T14:01:00Z"),
                    dict(leg="long", symbol="OPRA:DEMO261120C100.0", kind="exit", side="sell", price="2.60",
                         occurred_at="2026-10-26T15:00:00Z"),
                    dict(leg="short", symbol="OPRA:DEMO261120C105.0", kind="exit", side="buy", price="0.92",
                         occurred_at="2026-10-26T15:00:00Z"))
        out, errors = run(data)
        a = out["arms"]["A"]
        self.assertEqual((errors, a["excluded"]), ([], {}))
        # debit 1.20 x 100 = 120, exit value 1.68 x 100 = 168, fees 4 x 0.65 = 2.60
        self.assertEqual((a["closed_trades"], a["net"]), (1, 45.4))

    def test_unbalanced_spread_is_excluded(self):
        data = rows(dict(leg="long", symbol="OPRA:DEMO261120C100.0", price="2.00"),
                    dict(leg="net", kind="stop_set", side="sell", price="1.70", occurred_at="2026-10-26T14:01:00Z"),
                    dict(leg="long", symbol="OPRA:DEMO261120C100.0", kind="exit", side="sell", price="2.60",
                         occurred_at="2026-10-26T15:00:00Z"))
        out, _ = run(data)
        self.assertIn("t1", out["arms"]["A"]["excluded"])

    def test_rule_breaches(self):
        out, _ = run(rows(
            dict(), dict(kind="stop_set", side="sell", price="0.80", occurred_at="2026-10-26T14:01:00Z"),  # 20% stop: too loose
            dict(trade_id="t2", occurred_at="2026-10-26T14:30:00Z"),  # second position while t1 is open, no stop at all
            dict(kind="exit", side="sell", price="0.70", occurred_at="2026-10-26T15:00:00Z"),  # t1 closes at a loss
            dict(trade_id="t2", kind="exit", side="sell", price="1.10", occurred_at="2026-10-26T15:10:00Z"),
            dict(trade_id="t3", occurred_at="2026-10-26T16:00:00Z"),  # third entry, after the first loss
            dict(trade_id="t3", kind="stop_set", side="sell", price="0.90", occurred_at="2026-10-26T16:00:10Z")))
        text = "\n".join(out["violations"])
        for needle in ("t1: stop 0.8 is looser than 15%", "t2: no protective stop", "t2: 2 positions open at once",
                       "3 entries on 2026-10-26", "entries after the first loss on 2026-10-26: t3"):
            self.assertIn(needle, text)

    def test_duplicates_and_bad_rows(self):
        dup = rows(*WIN)
        dup.append(dict(dup[0], event_id="e99"))  # same content, new id
        out, errors = run(dup + rows(dict(event_id="e0"), dict(price="nan"), dict(occurred_at="2026-10-26T14:00:00"),
                                     dict(kind="exit", side="buy"), dict(qty=""), dict(fees="-1"), dict(leg="sideways")))
        self.assertEqual(len(errors), 8)
        self.assertEqual(out["arms"]["A"]["closed_trades"], 1)

    def test_oversell_and_cli(self):
        out, _ = run(rows(*WIN, dict(kind="exit", side="sell", price="1.3", occurred_at="2026-10-26T15:30:00Z")))
        self.assertTrue(any("sold more than was bought" in v for v in out["violations"]))
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([str(write(tmp, rows(*WIN)))])
            self.assertEqual(code, 0)
            self.assertIn("proves nothing", json.loads(buf.getvalue())["sample_note"])
            for bad in (["--option-stop", "nan"], ["--option-stop", "1.5"], ["--max-open", "0"], ["--max-quote-age-option", "-5"]):
                with self.subTest(bad), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main([str(write(tmp, rows(*WIN)))] + bad)


if __name__ == "__main__":
    unittest.main()
