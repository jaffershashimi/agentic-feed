import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import news_drop_forward as ndf

ASOF = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 22, 30, tzinfo=timezone.utc)


def series(closes, vol=5e6, end=ASOF):
    n = len(closes)
    ds = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    return {"date": ds, "o": list(closes), "h": closes, "l": closes, "c": list(closes), "v": [vol] * n}


def market(spy_last=0.0):
    flat = [100.0 + (i % 3) * 0.1 for i in range(59)]
    return {"SPY": series(flat + [flat[-1] * (1 + spy_last)]), "DROP": series(flat + [flat[-1] * 0.88]), "FLAT": series(flat + [flat[-1]]),
            "CHEAP": series([5.0] * 59 + [4.0]), "THIN": series(flat + [flat[-1] * 0.85], vol=1000)}


class DropTests(unittest.TestCase):
    def test_only_liquid_priced_big_drops_are_events_and_market_crash_days_are_skipped(self):
        ev, spy_ret = ndf.find_drops(market(), ASOF)
        self.assertEqual([e["sym"] for e in ev], ["DROP"])
        ev, _ = ndf.find_drops(market(spy_last=-0.03), ASOF)
        self.assertEqual(ev, [])

    def test_missing_spy_bar_is_refused(self):
        m = market()
        m["SPY"]["date"] = [(ASOF - timedelta(days=61 - i)).isoformat() for i in range(60)]
        with self.assertRaises(ap.GuardError):
            ndf.find_drops(m, ASOF)

    def test_scan_classifies_with_news_and_log_is_idempotent(self):
        fetch = lambda call, syms, start, end: market()
        news = lambda call, events, asof: {"DROP": [{"headline": "DROP cuts full-year guidance", "summary": "", "symbols": ["DROP"], "created_at": "2026-11-13T15:00:00Z"}]}
        res = ndf.scan(None, ["DROP", "FLAT"], NOW, fetch, news)
        self.assertEqual((res["events"][0]["group"], res["events"][0]["sub"]), ("NEWS", "GUIDANCE"))
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "e.jsonl"
            self.assertEqual(ndf.append_new(log, res), 1)
            self.assertEqual(ndf.append_new(log, res), 0)
            self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_outcomes_fill_in_market_adjusted_returns_once_ten_sessions_exist(self):
        n = 80
        flat = [100.0] * n
        d = series(flat, end=date(2026, 11, 30))
        s = series(flat, end=date(2026, 11, 30))
        ev_date = d["date"][60]
        d["o"][61] = 90.0
        for k in range(61, 71):
            d["c"][k] = 99.0
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "e.jsonl"
            log.write_text(json.dumps({"sym": "DROP", "date": ev_date, "ret": -0.1}) + "\n")
            out = ndf.outcomes(None, log, datetime(2027, 1, 5, tzinfo=timezone.utc), lambda call, syms, start, end: {"DROP": d, "SPY": s})
            self.assertEqual(out["updated"], 1)
            row = json.loads(log.read_text())
            self.assertAlmostEqual(row["o1_10"], 99 / 90 - 1)
            self.assertEqual(ndf.outcomes(None, log, datetime(2027, 1, 5, tzinfo=timezone.utc), lambda *a: {})["updated"], 0)

    def test_cli_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = Path(tmp) / "u.txt"
            u.write_text("DROP\nFLAT\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = ndf.main(["scan", "--universe", str(u), "--log", str(Path(tmp) / "l.jsonl")], call=object(), now=NOW,
                                fetch=lambda *a: market(), news=lambda *a: {})
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buf.getvalue())["events"][0]["group"], "NO_NEWS")


if __name__ == "__main__":
    unittest.main()
