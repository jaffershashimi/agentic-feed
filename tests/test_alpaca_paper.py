import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from consumer import alpaca_paper as ap

PAPER = "https://paper-api.alpaca.markets"
GOOD_ENV = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s", "ALPACA_PAPER_BASE_URL": PAPER, "ALPACA_PAPER_TRADE": "true"}


class FakeAlpaca:
    """Routes calls to canned answers and records every call. Never touches the network."""

    def __init__(self, is_open=True, held=(), open_orders=(), asset=None, price="100.00"):
        self.calls = []
        self.is_open, self.held, self.open_orders = is_open, list(held), list(open_orders)
        self.asset = asset or {"class": "us_equity", "tradable": True, "status": "active"}
        self.price = price

    def __call__(self, method, path, body=None, host="paper"):
        self.calls.append((method, path, body, host))
        if path == "/v2/clock":
            return {"is_open": self.is_open, "next_open": "2026-11-16T09:30:00-05:00"}
        if path.startswith("/v2/assets/"):
            return self.asset
        if path == "/v2/positions":
            return self.held
        if path.startswith("/v2/orders?status=open"):
            return self.open_orders
        if path.startswith("/v2/stocks/") and host == "data":
            return {"trade": {"p": self.price}}
        if method == "POST" and path == "/v2/orders":
            return {"id": "ord-1", "status": "accepted"}
        if method == "DELETE" and path == "/v2/orders":
            return [{"id": "x"}]
        if method == "DELETE" and path.startswith("/v2/positions"):
            return [{"symbol": "SPY"}] if path == "/v2/positions?cancel_orders=true" else {"id": "close-1", "status": "accepted"}
        if path == "/v2/account":
            return {"status": "ACTIVE", "currency": "USD", "trading_blocked": False, "account_blocked": False, "shorting_enabled": True}
        raise AssertionError(f"unexpected call {method} {path}")

    def sent(self):
        return [c for c in self.calls if c[0] in ("POST", "DELETE")]


class GuardTests(unittest.TestCase):
    def test_only_the_paper_host_is_accepted(self):
        self.assertEqual(ap.paper_base(GOOD_ENV), PAPER)
        self.assertEqual(ap.paper_base({**GOOD_ENV, "ALPACA_PAPER_BASE_URL": PAPER + "/v2"}), PAPER)
        for bad in ("https://api.alpaca.markets", "http://paper-api.alpaca.markets", "https://paper-api.alpaca.markets.evil.com",
                    "https://paper-api.alpaca.markets@evil.com", "https://evil.com/paper-api.alpaca.markets",
                    "https://paper-api.alpaca.markets:8443"):
            with self.assertRaises(ap.GuardError, msg=bad):
                ap.paper_base({**GOOD_ENV, "ALPACA_PAPER_BASE_URL": bad})
        with self.assertRaises(ap.GuardError):
            ap.paper_base({**GOOD_ENV, "ALPACA_PAPER_TRADE": "false"})

    def test_client_refuses_missing_or_placeholder_keys_and_a_live_data_url_swap(self):
        for env in ({}, {**GOOD_ENV, "ALPACA_API_KEY": "PASTE_YOUR_KEY_ID_HERE"}, {**GOOD_ENV, "ALPACA_DATA_BASE_URL": "https://evil.com"}):
            with self.assertRaises(ap.GuardError):
                ap.make_client(env)
        self.assertTrue(callable(ap.make_client(GOOD_ENV)))

    def test_entry_sizing_stop_and_limits(self):
        o = ap.build_entry("spy", 739.15)
        self.assertEqual((o["symbol"], o["qty"], o["order_class"], o["side"], o["time_in_force"]), ("SPY", "1", "oto", "buy", "day"))
        self.assertEqual(o["stop_loss"]["stop_price"], "591.32")  # 20 percent below, 2 decimals
        self.assertEqual(ap.build_entry("F", 12.5)["qty"], "80")  # floor(1000 / 12.5)
        for bad in (("SPY", 1500.0), ("SPY", 0), ("SPY", float("nan")), ("BRK.B", 100), ("SPY240119C00500000", 5), ("", 5)):
            with self.assertRaises(ap.GuardError, msg=str(bad)):
                ap.build_entry(*bad)
        with self.assertRaises(ap.GuardError):
            ap.build_entry("SPY", 100, max_usd=5000)

    def test_market_closed_asset_and_position_limits(self):
        for fake, why in ((FakeAlpaca(is_open=False), "closed"),
                          (FakeAlpaca(asset={"class": "crypto", "tradable": True, "status": "active"}), "asset"),
                          (FakeAlpaca(held=[{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}]), "positions"),
                          (FakeAlpaca(held=[{"symbol": "SPY"}]), "same symbol"),
                          (FakeAlpaca(held=[{"symbol": "A"}, {"symbol": "B"}], open_orders=[{"symbol": "C", "side": "buy"}]), "orders"),
                          (FakeAlpaca(open_orders=[{"symbol": "SPY", "side": "buy"}]), "pending same symbol")):
            with self.assertRaises(ap.GuardError, msg=why):
                ap.guard_entry(fake, "SPY")
        ap.guard_entry(FakeAlpaca(held=[{"symbol": "A"}], open_orders=[{"symbol": "STOP", "side": "sell", "parent_order_id": "p"}]), "SPY")

    def test_stop_child_orders_do_not_count_as_entries(self):
        legs = [{"symbol": "A", "side": "sell", "parent_order_id": "p1"}, {"symbol": "B", "side": "sell", "parent_order_id": "p2"}]
        ap.guard_entry(FakeAlpaca(held=[{"symbol": "A"}, {"symbol": "B"}], open_orders=legs), "SPY")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)

    def run_cli(self, fake, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ap.main(["--state-dir", str(self.state), *argv], call=fake)
        return code, json.loads(buf.getvalue())

    def test_buy_is_a_dry_run_unless_submit(self):
        fake = FakeAlpaca()
        code, out = self.run_cli(fake, "buy", "SPY", "--ref-price", "739.15")
        self.assertEqual((code, out["submit"]), (0, False))
        self.assertEqual(fake.sent(), [])
        self.assertFalse((self.state / "orders.jsonl").exists())

    def test_submit_sends_one_oto_order_and_logs_it_without_keys(self):
        fake = FakeAlpaca()
        code, out = self.run_cli(fake, "buy", "SPY", "--ref-price", "739.15", "--submit")
        self.assertEqual((code, out["order_id"]), (0, "ord-1"))
        sent = fake.sent()
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0][0], sent[0][1], sent[0][2]["order_class"], sent[0][2]["stop_loss"]["stop_price"]), ("POST", "/v2/orders", "oto", "591.32"))
        log = (self.state / "orders.jsonl").read_text()
        self.assertIn("ord-1", log)
        self.assertNotIn("APCA", log)

    def test_default_reference_price_comes_from_the_data_host_read_only(self):
        fake = FakeAlpaca(price="50.00")
        code, out = self.run_cli(fake, "buy", "F")
        self.assertEqual((code, out["request"]["qty"]), (0, "20"))
        self.assertTrue(any(c[3] == "data" and c[0] == "GET" for c in fake.calls))

    def test_guard_failure_exits_2_and_sends_nothing(self):
        fake = FakeAlpaca(is_open=False)
        code, out = self.run_cli(fake, "buy", "SPY", "--ref-price", "739.15", "--submit")
        self.assertEqual(code, 2)
        self.assertIn("market is closed", out["error"])
        self.assertEqual(fake.sent(), [])

    def test_halt_blocks_orders_until_resume(self):
        fake = FakeAlpaca()
        self.run_cli(fake, "halt", "--reason", "test")
        self.assertTrue((self.state / "HALT").exists())
        code, out = self.run_cli(fake, "buy", "SPY", "--ref-price", "739.15", "--submit")
        self.assertEqual(code, 2)
        self.assertIn("HALT", out["error"])
        code, out = self.run_cli(fake, "close", "SPY", "--submit")
        self.assertEqual(code, 2)
        self.run_cli(fake, "resume")
        self.assertFalse((self.state / "HALT").exists())
        self.assertEqual(self.run_cli(fake, "buy", "SPY", "--ref-price", "739.15", "--submit")[0], 0)

    def test_halt_flatten_cancels_and_closes(self):
        fake = FakeAlpaca()
        code, out = self.run_cli(fake, "halt", "--flatten")
        self.assertEqual(code, 0)
        self.assertEqual([c[1] for c in fake.sent()], ["/v2/orders", "/v2/positions?cancel_orders=true"])

    def test_close_is_a_dry_run_by_default_and_rejects_odd_symbols(self):
        fake = FakeAlpaca()
        self.assertFalse(self.run_cli(fake, "close", "SPY")[1]["submit"])
        self.assertEqual(fake.sent(), [])
        self.assertEqual(self.run_cli(fake, "close", "SPY/../x", "--submit")[0], 2)
        self.assertEqual(self.run_cli(fake, "close", "SPY", "--submit")[1]["order_id"], "close-1")

    def test_preflight_reports_room_and_status_never_balances(self):
        fake = FakeAlpaca(held=[{"symbol": "A"}])
        code, out = self.run_cli(fake, "preflight")
        self.assertEqual((code, out["open_positions"], out["room_for_entries"]), (0, 1, 2))
        self.assertEqual(set(out["account"]), {"status", "currency", "trading_blocked", "account_blocked", "shorting_enabled"})

    def test_fills_lists_filled_orders_and_legs(self):
        class F(FakeAlpaca):
            def __call__(self, method, path, body=None, host="paper"):
                if path.startswith("/v2/orders?status=closed"):
                    return [{"id": "1", "symbol": "SPY", "side": "buy", "filled_qty": "1", "filled_avg_price": "739.2", "filled_at": "t",
                             "legs": [{"id": "2", "symbol": "SPY", "side": "sell", "filled_at": None}]}]
                return super().__call__(method, path, body, host)
        _, out = self.run_cli(F(), "fills")
        self.assertEqual([f["id"] for f in out["fills"]], ["1"])

    def test_cli_without_credentials_is_a_clean_error(self):
        buf = io.StringIO()
        with mock.patch.dict("os.environ", {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""}), contextlib.redirect_stdout(buf):
            code = ap.main(["--state-dir", str(self.state), "status"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
