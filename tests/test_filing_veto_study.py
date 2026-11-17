import tempfile
import unittest
from datetime import datetime, timezone

from consumer import edgar_events as ee
from consumer import filing_veto_study as fs
from tests.test_edgar_events import FakeOpen, TICKERS, sub

CAL = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16"]


def client(table):
    return ee.Client(tempfile.mkdtemp(), opener=FakeOpen(table), sleep=lambda s: None)


class RowTests(unittest.TestCase):
    def test_flags_use_the_exact_window_and_unmapped_trades_are_dropped(self):
        # entry 2026-11-13: signal day 11-12, window 2026-11-11 16:00 ET (21:00Z) to 2026-11-13 09:50 ET (14:50Z)
        c = client({"company_tickers.json": TICKERS, "CIK0000320193.json": sub(["8-K", "8-K", "10-Q"], ["2.02", "7.01", ""],
                                                                              ["2026-11-12T12:00:00.000Z", "2026-11-12T13:00:00.000Z", "2026-11-12T13:30:00.000Z"])})
        trades = [{"symbol": "AAPL", "entry_date": "2026-11-13", "ret": 0.01}, {"symbol": "NOPE", "entry_date": "2026-11-13", "ret": 0.02}]
        rows, n, mapped = fs.build_rows(trades, c, CAL)
        self.assertEqual((n, mapped, len(rows)), (2, 1, 1))
        self.assertEqual((rows[0]["v1"], rows[0]["v2"]), (True, True))

    def test_a_filing_before_the_window_or_after_the_order_time_does_not_flag(self):
        c = client({"company_tickers.json": TICKERS, "CIK0000320193.json": sub(["8-K", "8-K"], ["2.02", "2.02"], ["2026-11-11T20:59:00.000Z", "2026-11-13T14:51:00.000Z"])})
        rows, _, _ = fs.build_rows([{"symbol": "AAPL", "entry_date": "2026-11-13", "ret": 0.0}], c, CAL)
        self.assertEqual((rows[0]["v1"], rows[0]["v2"]), (False, False))


class VerdictTests(unittest.TestCase):
    def rows(self, flagged_ret, plain_ret, n_flagged, n_plain, year="2023"):
        out = []
        for i in range(n_flagged):
            out.append({"entry_date": f"{year}-03-{1 + i % 27:02d}", "week": f"{year}-W{i % 40}", "ret_pp": flagged_ret, "v1": True, "v2": True})
        for i in range(n_plain):
            out.append({"entry_date": f"{year}-04-{1 + i % 27:02d}", "week": f"{year}-W{(i % 40) + 5}", "ret_pp": plain_ret, "v1": False, "v2": False})
        return out

    def test_unmappable_coverage_blocks_instead_of_rejecting(self):
        res = fs.evaluate(self.rows(1.0, 2.0, 100, 300), n_trades=1000, mapped=500)
        self.assertEqual({r["verdict"] for r in res.values()}, {"BLOCKED"})

    def test_too_few_flagged_trades_blocks(self):
        res = fs.evaluate(self.rows(1.0, 2.0, 10, 300), n_trades=320, mapped=310)
        self.assertEqual({r["verdict"] for r in res.values()}, {"BLOCKED"})

    def test_good_coverage_keeps_the_pre_registered_verdict_and_render_has_no_symbols(self):
        res = fs.evaluate(self.rows(2.0, 1.0, 100, 300), n_trades=400, mapped=395)    # filings do better: D negative, so REJECTED
        self.assertEqual({r["verdict"] for r in res.values()}, {"REJECTED"})
        text = fs.render(res, 400, 395)
        self.assertIn("S-053", text)
        self.assertNotIn("AAPL", text)


if __name__ == "__main__":
    unittest.main()
