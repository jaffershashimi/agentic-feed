import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone

from consumer import edgar_events as ee

TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL"}, "1": {"cik_str": 1067983, "ticker": "BRK-B"}}


def sub(forms, items, accepted, older=None):
    return {"filings": {"recent": {"accessionNumber": [f"0001-26-{i:06d}" for i in range(len(forms))], "form": forms, "items": items, "acceptanceDateTime": accepted},
                        "files": [{"name": older[0]}] if older else []}}


class FakeOpen:
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, req, timeout=0):
        self.calls.append(req.full_url)
        for key, body in self.table.items():
            if req.full_url.endswith(key):
                class R:
                    def read(_s, b=gzip.compress(json.dumps(body).encode())):
                        return b
                return R()
        raise AssertionError(req.full_url)


def client(table, **kw):
    return ee.Client(tempfile.mkdtemp(), opener=FakeOpen(table), sleep=lambda s: None, **kw)


def T(h, m, d=15):
    return datetime(2026, 11, d, h, m, tzinfo=timezone.utc)


class FlagTests(unittest.TestCase):
    def f(self, form, items, when=T(12, 0)):
        return {"accession": "x", "form": form, "items": items, "accepted": when}

    def test_noise_only_items_do_not_count_and_event_items_do(self):
        self.assertEqual(ee.flags([self.f("8-K", ["7.01", "9.01"])]), (False, False))
        self.assertEqual(ee.flags([self.f("8-K", ["8.01"])]), (True, False))
        self.assertEqual(ee.flags([self.f("8-K", ["2.02", "9.01"])]), (True, True))
        self.assertEqual(ee.flags([self.f("8-K", ["5.02"])]), (True, True))

    def test_other_forms_and_amendments_are_ignored(self):
        self.assertEqual(ee.flags([self.f("4", []), self.f("8-K/A", ["2.02"]), self.f("424B5", [])]), (False, False))

    def test_window_is_inclusive_and_uses_the_exact_acceptance_time(self):
        rows = [self.f("8-K", ["2.02"], T(20, 0, 14)), self.f("8-K", ["2.02"], T(13, 50)), self.f("8-K", ["2.02"], T(13, 51))]
        got = ee.in_window(rows, T(20, 0, 14), T(13, 50))
        self.assertEqual(len(got), 2)


class ClientTests(unittest.TestCase):
    def test_ticker_map_and_share_class_lookup(self):
        c = client({"company_tickers.json": TICKERS})
        tk = c.ticker_map()
        self.assertEqual(ee.lookup(tk, "AAPL"), 320193)
        self.assertEqual(ee.lookup(tk, "BRK.B"), 1067983)
        self.assertIsNone(ee.lookup(tk, "ZZZZ"))

    def test_filings_merge_recent_and_older_pages_and_cache(self):
        recent = sub(["8-K", "10-Q"], ["2.02,9.01", ""], ["2026-11-14T21:49:16.000Z", "2026-11-01T12:00:00.000Z"], older=("CIK0000320193-submissions-001.json",))
        older = {"form": ["8-K"], "items": ["5.02"], "accessionNumber": ["0001-20-000001"], "acceptanceDateTime": ["2020-03-02T13:30:00.000Z"]}
        fo = FakeOpen({"CIK0000320193.json": recent, "CIK0000320193-submissions-001.json": older})
        c = ee.Client(tempfile.mkdtemp(), opener=fo, sleep=lambda s: None)
        rows = c.filings(320193)
        self.assertEqual([(r["form"], r["items"]) for r in rows], [("8-K", ["2.02", "9.01"]), ("10-Q", []), ("8-K", ["5.02"])])
        self.assertEqual(rows[0]["accepted"], datetime(2026, 11, 14, 21, 49, 16, tzinfo=timezone.utc))
        c.filings(320193)
        self.assertEqual(len(fo.calls), 2)      # the second read came from the cache

    def test_user_agent_is_generic_by_default_and_configurable(self):
        self.assertEqual(client({}).ua, ee.DEFAULT_UA)
        self.assertNotIn("@", ee.DEFAULT_UA)
        self.assertEqual(client({}, user_agent="X y").ua, "X y")

    def test_rate_limit_waits_between_requests(self):
        waits, t = [], [0.0]
        c = ee.Client(tempfile.mkdtemp(), opener=FakeOpen({"CIK0000000001.json": sub([], [], []), "CIK0000000002.json": sub([], [], [])}),
                      sleep=lambda s: waits.append(s), clock=lambda: t[0])
        c.get(ee.SUBMISSIONS_URL.format(cik=1), "a.json")
        c.get(ee.SUBMISSIONS_URL.format(cik=2), "b.json")
        self.assertTrue(waits and 0 < max(waits) <= ee.MIN_INTERVAL)


class CoverageTests(unittest.TestCase):
    def test_counts_only_and_never_reads_a_return(self):
        c = client({"company_tickers.json": TICKERS, "CIK0000320193.json": sub(["8-K"], ["2.02"], ["2026-11-15T09:00:00.000Z"])})
        trades = [{"symbol": "AAPL", "entry_date": "2026-11-15"}, {"symbol": "QQQQ", "entry_date": "2026-11-15"}]
        res = ee.coverage(trades, c, lambda d: (T(0, 0, 14), T(13, 50)), {"W": ("2026-11-01", "2026-11-30")})
        self.assertEqual((res["trades"], res["mapped"], res["companies"]), (2, 1, 1))
        self.assertEqual(res["by_window"]["W"]["v2"], 1)


if __name__ == "__main__":
    unittest.main()
