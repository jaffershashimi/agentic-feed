import csv
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

from consumer.paper_ledger import COLUMNS, analyze, load, main

# Synthetic paper fills only: invented symbols, prices and ids.


def rows(*events):
    out = []
    for i, e in enumerate(events):
        base = dict(event_id=f"e{i}", arm="A", trade_id="t1", kind="entry", occurred_at="2026-10-26T14:00:00Z", symbol="DEMO",
                    instrument="option", side="buy", qty="1", price="1.00", multiplier="100", decision_id=f"d{i}")
        out.append(dict(base, **e))
    return out


def write(tmp, data):
    path = pathlib.Path(tmp) / "fills.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
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
    def test_win_and_loss_statistics(self):
        loss = [dict(trade_id="t2", occurred_at="2026-10-27T14:00:00Z"),
                dict(trade_id="t2", kind="stop_set", side="sell", price="0.85", occurred_at="2026-10-27T14:00:30Z"),
                dict(trade_id="t2", kind="exit", side="sell", price="0.80", occurred_at="2026-10-27T16:00:00Z")]
        out, errors = run(rows(*WIN, *loss))
        a = out["arms"]["A"]
        self.assertEqual((errors, out["violations"]), ([], []))
        self.assertEqual((a["closed_trades"], a["win_rate"], a["avg_win"], a["avg_loss"], a["net"]), (2, 0.5, 30.0, -20.0, 10.0))
        self.assertEqual((a["expectancy"], a["max_drawdown"], a["returns_pct"]), (5.0, -20.0, [30.0, -20.0]))

    def test_open_trades_are_not_counted(self):
        out, _ = run(rows(*WIN[:2]))
        a = out["arms"]["A"]
        self.assertEqual((a["closed_trades"], a["open_trades"], a["win_rate"], a["net"]), (0, ["t1"], None, 0))

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

    def test_equity_uses_its_own_stop_limit_and_arms_are_separate(self):
        eq = [dict(arm="B", instrument="equity", multiplier="1", price="100"),
              dict(arm="B", instrument="equity", multiplier="1", kind="stop_set", side="sell", price="81", occurred_at="2026-10-26T14:02:00Z")]
        out, _ = run(rows(*WIN, *eq))
        self.assertEqual(out["violations"], [])
        self.assertEqual(set(out["arms"]), {"A", "B"})

    def test_bad_rows_are_reported_and_skipped(self):
        out, errors = run(rows(*WIN, dict(event_id="e0"), dict(price="nan"), dict(occurred_at="2026-10-26T14:00:00"),
                               dict(kind="exit", side="buy"), dict(kind="entry", side="sell"), dict(qty="")))
        self.assertEqual(len(errors), 6)
        self.assertEqual(out["arms"]["A"]["closed_trades"], 1)

    def test_oversell_and_cli_exit_code(self):
        out, _ = run(rows(*WIN, dict(kind="exit", side="sell", price="1.3", occurred_at="2026-10-26T15:30:00Z")))
        self.assertTrue(any("sold more than was bought" in v for v in out["violations"]))
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([str(write(tmp, rows(*WIN)))])
            self.assertEqual(code, 0)
            self.assertIn("proves nothing", json.loads(buf.getvalue())["sample_note"])


if __name__ == "__main__":
    unittest.main()
