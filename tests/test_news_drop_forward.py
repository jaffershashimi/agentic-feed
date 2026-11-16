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


class FakeBroker:
    def __init__(self, positions=(), open_orders=(), closed=(), is_open=False):
        self.positions, self.open_orders, self.closed, self.is_open, self.calls = list(positions), list(open_orders), list(closed), is_open, []

    def __call__(self, method, path, body=None, host="paper"):
        self.calls.append((method, path, body))
        if path == "/v2/positions":
            return self.positions
        if path.startswith("/v2/orders?status=open"):
            return self.open_orders
        if path.startswith("/v2/orders?status=closed"):
            return self.closed
        if path == "/v2/clock":
            return {"is_open": self.is_open}
        if path.startswith("/v2/assets/"):
            sym = path.rsplit("/", 1)[1]
            return {"name": "ProShares UltraPro QQQ" if sym == "LEV" else f"{sym} Corp Common Stock"}
        if method == "POST" and path == "/v2/orders":
            o = {"id": f"id{len(self.calls)}", "symbol": body["symbol"], "side": body["side"], "client_order_id": body["client_order_id"]}
            if body["side"] == "buy":
                self.open_orders.append(o)
            return o
        if method == "DELETE":
            return {}
        raise AssertionError(path)

    def posts(self):
        return [c[2] for c in self.calls if c[0] == "POST"]


def drop_market(names):
    flat = [100.0 + (i % 3) * 0.1 for i in range(59)]
    m = {"SPY": series(flat + [flat[-1]])}
    for n in names:
        m[n] = series(flat + [flat[-1] * 0.88], vol=6e6 if n == "BIG" else 5e6)
    return m


class RunTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)

    def go(self, broker, names=("AAA", "BIG"), news=None, submit=True):
        return ndf.run(broker, list(names), NOW, self.state, submit, lambda c, s, a, b: {k: v for k, v in drop_market(names).items() if k in set(s)}, news or (lambda *a: {}))

    def test_dry_run_sends_nothing_and_only_no_news_drops_are_entries(self):
        news = lambda *a: {"AAA": [{"headline": "AAA cuts guidance", "summary": "", "symbols": ["AAA"], "created_at": "2026-11-13T15:00:00Z"}]}
        b = FakeBroker()
        out = self.go(b, news=news, submit=False)
        self.assertEqual((out["drops"], out["no_news"], out["news_logged_only"]), (2, 1, 1))
        self.assertEqual([e["symbol"] for e in out["entries"]], ["BIG"])
        self.assertEqual(b.posts(), [])

    def test_submit_after_close_queues_one_oto_entry_with_a_12_percent_stop(self):
        b = FakeBroker()
        out = self.go(b)
        posts = b.posts()
        self.assertEqual(sorted(p["symbol"] for p in posts), ["AAA", "BIG"])
        p = posts[0]
        self.assertEqual((p["order_class"], p["time_in_force"], p["type"], p["side"]), ("oto", "day", "market", "buy"))
        self.assertTrue(p["client_order_id"].startswith("s044n-"))
        self.assertLessEqual(int(p["qty"]) * 88.0, 5000.0)  # notional cap
        self.assertEqual(self.go(b)["entries"], [])  # same day again: pending orders are seen

    def test_market_open_halt_and_risk_limits(self):
        b = FakeBroker(is_open=True)
        self.assertIn("market is open", self.go(b)["note"])
        self.assertEqual(b.posts(), [])
        (self.state / "HALT").write_text("x")
        with self.assertRaises(ap.GuardError):
            self.go(FakeBroker())
        (self.state / "HALT").unlink()
        pending = [{"symbol": f"P{i}", "side": "buy", "client_order_id": f"s044n-P{i}-261112", "id": f"o{i}"} for i in range(6)]
        out = self.go(FakeBroker(open_orders=pending))
        self.assertEqual(out["entries"], [])
        self.assertTrue(all("limit" in s["why"] or "open" in s["why"] for s in out["skipped"]))

    def test_leveraged_funds_are_not_entries(self):
        b = FakeBroker()
        out = self.go(b, names=("AAA", "LEV"))
        self.assertEqual([p["symbol"] for p in b.posts()], ["AAA"])
        self.assertIn({"sym": "LEV", "why": "NOT_AN_OPERATING_COMPANY"}, out["skipped"])

    def test_exit_on_the_tenth_session_cancels_the_stop_and_sells(self):
        d = series([100.0] * 60)
        entry = d["date"][-10]  # entry day is session 1, so today is session 10
        b = FakeBroker(positions=[{"symbol": "HELD", "qty": "20", "avg_entry_price": "100"}],
                       closed=[{"client_order_id": "s044n-HELD-261030", "filled_qty": "20", "side": "buy", "symbol": "HELD", "filled_at": entry + "T14:30:01Z"}],
                       open_orders=[{"symbol": "HELD", "side": "sell", "id": "stop-1", "client_order_id": "x"}])
        m = drop_market(["AAA"])
        m["HELD"] = d
        out = ndf.run(b, ["AAA", "HELD"], NOW, self.state, True, lambda c, s, a, e: {k: v for k, v in m.items() if k in set(s)}, lambda *a: {})
        self.assertEqual(out["exits"][0]["reason"], "TIME_10_SESSIONS")
        self.assertIn(("DELETE", "/v2/orders/stop-1", None), b.calls)
        self.assertEqual([p["symbol"] for p in b.posts() if p["side"] == "sell"], ["HELD"])


if __name__ == "__main__":
    unittest.main()
