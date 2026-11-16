import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from consumer import news_events as ne
from consumer import news_watch as nw

T0 = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)


class FakeAlpaca:
    def __init__(self, headlines, mids):
        self.headlines, self.mids, self.calls = headlines, list(mids), []

    def __call__(self, method, path, body=None, host="paper"):
        self.calls.append(path)
        assert host == "data" and method == "GET"
        if path.startswith("/v1beta1/news"):
            return {"news": self.headlines, "next_page_token": None}
        if path.startswith("/v2/stocks/quotes/latest"):
            m = self.mids.pop(0) if len(self.mids) > 1 else self.mids[0]
            return {"quotes": {"SPY": {"bp": m - 0.01, "ap": m + 0.01}}}
        raise AssertionError(path)


def hl(i, text, t):
    return {"id": i, "created_at": t, "headline": text, "summary": "", "symbols": ["SPY"], "source": "benzinga", "author": "x"}


class WatchTests(unittest.TestCase):
    def test_event_logged_once_with_reactions_and_summary(self):
        items = [hl(1, "Iran missile attack on Israel", "2026-11-17T14:50:00Z"), hl(2, "Israel war escalates", "2026-11-17T14:55:00Z"),
                 hl(3, "Iran drone attack reported", "2026-11-17T14:58:00Z")]
        fake = FakeAlpaca(items, [600.0, 598.0, 597.0, 596.0, 594.0])
        with tempfile.TemporaryDirectory() as tmp:
            clock = {"t": T0}

            def sleep(sec):
                clock["t"] += timedelta(seconds=sec)

            n = nw.run(fake, tmp, T0 + timedelta(minutes=70), 60, now_fn=lambda: clock["t"], sleep=sleep)
            self.assertGreater(n, 60)
            ev = nw.read(Path(tmp) / "events.jsonl")
            self.assertEqual(len(ev), 1)  # same event is not logged on every cycle
            self.assertEqual(ev[0]["t0"][:16], "2026-11-17T14:58")
            self.assertEqual(len(nw.read(Path(tmp) / "reactions.jsonl")), 4)
            s = nw.summarize(tmp)
            self.assertEqual(s["events"], 1)
            self.assertIsNotNone(s["rows"][0]["m60_pct"])
            self.assertLess(s["rows"][0]["m60_pct"], 0)

    def test_errors_are_logged_and_never_stop_the_loop(self):
        ne.RETRY_BASE = 0
        self.addCleanup(setattr, ne, "RETRY_BASE", 1.0)

        def boom(*a, **k):
            raise RuntimeError("HTTP 500")

        with tempfile.TemporaryDirectory() as tmp:
            clock = {"t": T0}
            n = nw.run(boom, tmp, T0 + timedelta(minutes=3), 60, now_fn=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + timedelta(seconds=s)))
            self.assertEqual(n, 3)
            self.assertEqual(len(nw.read(Path(tmp) / "errors.jsonl")), 3)

    def test_cli_rejects_naive_until(self):
        self.assertEqual(nw.main(["run", "--dir", "x", "--until", "2026-11-17T21:00:00"], call=lambda *a, **k: {}), 2)


if __name__ == "__main__":
    unittest.main()
