import csv
import gzip
import io
import json
import os
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from consumer import bar_archive as ba

DAY = date(2026, 11, 10)  # a normal session; Nov 27, 2026 is an early close
NOW = datetime(2026, 11, 11, 4, 0, tzinfo=timezone.utc)


def bar(t, price="100.50", vol="1000"):
    return {"t": t, "o": price, "h": price, "l": price, "c": price, "v": vol, "n": "10", "vw": price}


class FakeApi:
    """Mimics the bars endpoint: batches of symbols, pagination, pre and post market bars."""

    def __init__(self, per_symbol, page=3):
        self.per_symbol, self.page, self.calls = per_symbol, page, []

    def __call__(self, path):
        self.calls.append(path)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(path).query))
        syms = q["symbols"].split(",")
        flat = [(s, b) for s in syms for b in self.per_symbol.get(s, [])]
        start = int(q.get("page_token") or 0)
        chunk = flat[start:start + self.page]
        bars = {}
        for s, b in chunk:
            bars.setdefault(s, []).append(b)
        nxt = str(start + self.page) if start + self.page < len(flat) else None
        return {"bars": bars, "next_page_token": nxt}


def day_bars(price="100.50"):
    # 12:00Z is before the 14:30Z open (pre-market), 14:30Z..20:55Z regular, 21:00Z is after the close
    return [bar("2026-11-10T12:00:00Z", price), bar("2026-11-10T14:30:00Z", price), bar("2026-11-10T14:35:00Z", price),
            bar("2026-11-10T20:55:00Z", price), bar("2026-11-10T21:00:00Z", price), bar("2026-11-10T22:00:00Z", price)]


class FetchTests(unittest.TestCase):
    def test_window_drops_extended_hours_sorts_and_paginates(self):
        api = FakeApi({"SPY": day_bars(), "AMD": day_bars("200.10")}, page=2)
        rows = ba.fetch_day(["spy", "AMD", " "], DAY, NOW, api)
        self.assertEqual([(r["symbol"], r["t"]) for r in rows],
                         [("AMD", "2026-11-10T14:30:00Z"), ("AMD", "2026-11-10T14:35:00Z"), ("AMD", "2026-11-10T20:55:00Z"),
                          ("SPY", "2026-11-10T14:30:00Z"), ("SPY", "2026-11-10T14:35:00Z"), ("SPY", "2026-11-10T20:55:00Z")])
        self.assertGreater(len(api.calls), 1)  # it followed next_page_token
        self.assertTrue(all("end=2026-11-10T21%3A00%3A00Z" in c and "feed=sip" in c for c in api.calls))

    def test_values_stay_as_the_exact_text_sent(self):
        rows = ba.fetch_day(["SPY"], DAY, NOW, FakeApi({"SPY": [bar("2026-11-10T14:30:00Z", "166.8159", "123456")]}))
        self.assertEqual((rows[0]["o"], rows[0]["v"]), ("166.8159", "123456"))

    def test_refuses_a_session_that_ended_less_than_16_minutes_ago(self):
        with self.assertRaises(ba.ArchiveError):
            ba.fetch_day(["SPY"], DAY, datetime(2026, 11, 10, 21, 10, tzinfo=timezone.utc), FakeApi({}))
        ba.fetch_day(["SPY"], DAY, datetime(2026, 11, 10, 21, 20, tzinfo=timezone.utc), FakeApi({"SPY": day_bars()}))

    def test_non_session_and_empty_symbols_and_naive_now(self):
        with self.assertRaises(ba.ArchiveError):
            ba.fetch_day(["SPY"], date(2026, 11, 14), NOW, FakeApi({}))  # Saturday
        with self.assertRaises(ba.ArchiveError):
            ba.fetch_day([" "], DAY, NOW, FakeApi({}))
        with self.assertRaises(ba.ArchiveError):
            ba.fetch_day(["SPY"], DAY, datetime(2026, 11, 11, 4, 0), FakeApi({}))

    def test_early_close_window_ends_at_one_pm_eastern(self):
        start, end = ba.session_window(date(2026, 11, 27))
        self.assertEqual((start.hour, start.minute, end.hour, end.minute), (14, 30, 18, 0))

    def test_duplicate_bar_is_an_error(self):
        dup = [bar("2026-11-10T14:30:00Z"), bar("2026-11-10T14:30:00Z")]
        with self.assertRaises(ba.ArchiveError):
            ba.fetch_day(["SPY"], DAY, NOW, FakeApi({"SPY": dup}))


class FileTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.rows = ba.fetch_day(["SPY", "AMD"], DAY, NOW, FakeApi({"SPY": day_bars(), "AMD": day_bars("200.10")}))

    def test_render_is_deterministic_and_readable(self):
        a, b = ba.render(self.rows), ba.render(list(self.rows))
        self.assertEqual(a, b)
        self.assertEqual(gzip.decompress(a)[:20], b"symbol,t,o,h,l,c,v,n")

    def test_write_day_manifest_and_append_only(self):
        entry = ba.write_day(self.root, DAY, self.rows, NOW)
        self.assertEqual((entry["date"], entry["symbols"], entry["rows"]), ("2026-11-10", 2, 6))
        self.assertTrue(ba.day_path(self.root, DAY).exists())
        self.assertEqual(ba.read_manifest(self.root)[0]["sha256"], entry["sha256"])
        with self.assertRaises(ba.ArchiveError):
            ba.write_day(self.root, DAY, self.rows, NOW)
        self.assertEqual(len(ba.read_manifest(self.root)), 1)

    def test_empty_day_writes_nothing(self):
        with self.assertRaises(ba.ArchiveError):
            ba.write_day(self.root, DAY, [], NOW)
        self.assertFalse(ba.manifest_path(self.root).exists())

    def test_check_passes_then_catches_tamper_missing_and_untracked(self):
        ba.write_day(self.root, DAY, self.rows, NOW)
        self.assertEqual(ba.check(self.root), {"days_ok": 1, "problems": []})
        path = ba.day_path(self.root, DAY)
        good = path.read_bytes()
        path.write_bytes(good + b"x")
        self.assertEqual(ba.check(self.root)["problems"][0]["problem"], "HASH_MISMATCH")
        path.write_bytes(good)
        extra = ba.day_path(self.root, date(2026, 11, 11))
        extra.write_bytes(good)
        self.assertEqual(ba.check(self.root)["problems"], [{"date": "2026-11-11", "problem": "FILE_NOT_IN_MANIFEST"}])
        extra.unlink()
        path.unlink()
        self.assertEqual(ba.check(self.root)["problems"][0]["problem"], "FILE_MISSING")

    def test_verify_reports_identical_and_a_revision(self):
        ba.write_day(self.root, DAY, self.rows, NOW)
        same = ba.verify(self.root, DAY, ba.fetch_day(["SPY", "AMD"], DAY, NOW, FakeApi({"SPY": day_bars(), "AMD": day_bars("200.10")})))
        self.assertTrue(same["identical"])
        revised = ba.fetch_day(["SPY", "AMD"], DAY, NOW, FakeApi({"SPY": day_bars(), "AMD": day_bars("200.11")}))
        rep = ba.verify(self.root, DAY, revised)
        self.assertEqual((rep["identical"], rep["changed_rows"], rep["only_in_archive"], rep["only_in_fresh"]), (False, 3, 0, 0))
        with self.assertRaises(ba.ArchiveError):
            ba.verify(self.root, date(2026, 11, 9), revised)

    def test_compare_with_replay_format_bars(self):
        other = self.root / "bars5m.csv"
        with open(other, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume", "interpolated"])
            w.writerow(["SPY", "2026-11-10T14:30:00Z", "100.50", "100.50", "100.50", "100.50", "500", "false"])  # same, half volume
            w.writerow(["SPY", "2026-11-10T14:35:00Z", "100.60", "100.60", "100.60", "100.60", "1000", "false"])  # 0.0995 percent off
            w.writerow(["ZZZ", "2026-11-10T14:30:00Z", "1", "1", "1", "1", "1", "false"])  # not in the archive
            w.writerow(["SPY", "2026-11-09T14:30:00Z", "1", "1", "1", "1", "1", "false"])  # another day: ignored
        rep = ba.compare_bars(self.rows, other, DAY)
        self.assertEqual((rep["common_bars"], rep["only_in_other"], rep["close_exact_share"]), (2, 1, 0.5))
        self.assertAlmostEqual(rep["abs_pct_diff"]["c"]["max"], 0.09950, places=4)
        self.assertEqual(rep["volume_ratio_other_over_archive"]["max"], 1.0)


class EnvAndCliTests(unittest.TestCase):
    def test_http_get_setup_refuses_bad_environment_and_never_echoes_the_secret(self):
        for env in ({}, {"ALPACA_API_KEY": "PASTE_YOUR_KEY_ID_HERE", "ALPACA_SECRET_KEY": "s"},
                    {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "SECRETVALUE", "ALPACA_DATA_BASE_URL": "https://paper-api.alpaca.markets"}):
            with self.assertRaises(ba.ArchiveError) as cm:
                ba.make_http_get(env)
            self.assertNotIn("SECRETVALUE", str(cm.exception))
        self.assertTrue(callable(ba.make_http_get({"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"})))

    def test_days_of_skips_weekends_and_holidays(self):
        class A:
            date, from_, to = None, "2026-11-23", "2026-11-30"
        self.assertEqual([d.isoformat() for d in ba.days_of(A)],
                         ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"])  # Thanksgiving Nov 26
        class B:
            date, from_, to = None, None, None
        with self.assertRaises(ba.ArchiveError):
            ba.days_of(B)

    def test_cli_check_and_compare_work_without_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = ba.fetch_day(["SPY"], DAY, NOW, FakeApi({"SPY": day_bars()}))
            ba.write_day(root, DAY, rows, NOW)
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(ba.main(["check", "--root", str(root)]), 0)
            self.assertEqual(json.loads(buf.getvalue())["days_ok"], 1)
            ba.day_path(root, DAY).write_bytes(b"broken")
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(ba.main(["check", "--root", str(root)]), 3)
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""}), redirect_stdout(buf):
                # a fetch without credentials is a clean error, exit 2, and makes no network call
                code = ba.main(["fetch", "--root", str(root), "--symbols", "SPY", "--date", "2026-11-09"])
            self.assertEqual(code, 2)
            self.assertIn("error", json.loads(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
