import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import breakout_forward as bf
from engine import gate
from tests import test_oversold_forward as to

NOW = datetime(2026, 11, 16, 15, 5, tzinfo=timezone.utc)    # 10:05 ET Monday; last complete session is Fri 2026-11-13
HEALTHY = [{"check": "stops present", "status": "OK", "detail": ""}, {"check": "data fresh", "status": "OK", "detail": ""}]


def market(last_close_factor=1.10, spy_up=True):
    n = 330
    spy = [100 + 0.05 * i for i in range(n)] if spy_up else [200 - 0.3 * i for i in range(n)]
    aaa = [50 + 0.02 * i for i in range(n)]
    aaa[-1] = aaa[-2] * last_close_factor
    return {"SPY": to.series(spy), "AAA": to.series(aaa, vol=5e6)}


def entry_order(sym, qty, filled="2026-11-05T15:00:00Z", cid=None):
    return {"id": "e-" + sym, "client_order_id": cid or f"s048b55m-{sym}-261105", "side": "buy", "symbol": sym, "filled_qty": str(qty), "filled_avg_price": "55", "filled_at": filled,
            "legs": [{"side": "sell", "type": "stop", "stop_price": "49.5", "status": "new", "filled_qty": "0"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)

    def go(self, fake, data, submit=False, **kw):
        return bf.run(fake, ["AAA"], NOW, self.state, submit, to.fetch_for(data), "morning", **kw)


class EntryTests(Base):
    def test_a_new_high_in_an_uptrend_plans_one_entry_with_the_frozen_shape(self):
        out = self.go(to.Fake(is_open=True), market())
        self.assertEqual((out["signals"], len(out["entries"]), out["slots"]), (1, 1, 20))
        b = out["entries"][0]
        self.assertEqual((b["symbol"], b["side"], b["order_class"], b["client_order_id"]), ("AAA", "buy", "oto", "s048b55m-AAA-261113"))
        close = float(b["stop_loss"]["stop_price"]) / 0.9
        self.assertLessEqual(int(b["qty"]) * close, 1000 * 1.02)

    def test_no_entry_when_spy_is_below_its_200_day_average_or_when_there_is_no_new_high(self):
        self.assertEqual(self.go(to.Fake(is_open=True), market(spy_up=False))["signals"], 0)
        self.assertEqual(self.go(to.Fake(is_open=True), market(last_close_factor=0.99))["signals"], 0)

    def test_dry_run_sends_nothing_and_a_submit_sends_a_gtc_limit_with_its_stop_once(self):
        fake = to.Fake(is_open=True)
        self.go(fake, market())
        self.assertEqual(fake.posts(), [])
        self.go(fake, market(), submit=True)
        posts = fake.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0]["type"], posts[0]["time_in_force"], posts[0]["order_class"]), ("limit", "gtc", "oto"))
        self.go(fake, market(), submit=True)
        self.assertEqual(len(fake.posts()), 1)      # idempotent: the pending tagged order is seen

    def test_a_symbol_the_account_already_holds_is_not_bought_again(self):
        fake = to.Fake(positions=[{"symbol": "AAA", "qty": "3"}], is_open=True)
        out = self.go(fake, market())
        self.assertEqual((len(out["entries"]), out["skipped"][0]["why"]), (0, "ALREADY_HELD_OR_PENDING"))

    def test_outside_the_window_or_market_closed_sends_nothing(self):
        fake = to.Fake(is_open=False)
        out = self.go(fake, market(), submit=True)
        self.assertEqual((fake.posts(), "nothing sent" in out["note"]), ([], True))

    def test_entries_blocked_by_the_gate_are_skipped_by_name(self):
        fake = to.Fake(is_open=True)
        out = self.go(fake, market(), submit=True, entries_allowed=False)
        self.assertEqual([b for b in fake.posts() if b["side"] == "buy"], [])
        self.assertTrue(any(s["why"] == "ENTRY_BLOCKED_BY_GATE" for s in out["skipped"]))

    def test_the_halt_line_blocks_new_entries_but_not_exits_or_stops(self):
        held = [{"symbol": "BBB", "qty": "3", "unrealized_pl": "-2600"}]
        fake = to.Fake(positions=held, closed=[entry_order("BBB", 3)], is_open=True)
        out = self.go(fake, market(), submit=True)
        self.assertTrue(out["halted"])
        self.assertEqual([b for b in fake.posts() if b["side"] == "buy"], [])
        self.assertTrue(any(s["why"] == "ENTRY_BLOCKED_BY_HALT" for s in out["skipped"]))
        ok = to.Fake(positions=[{"symbol": "BBB", "qty": "3", "unrealized_pl": "-200"}], closed=[entry_order("BBB", 3)], is_open=True)
        self.assertFalse(self.go(ok, market(), submit=True)["halted"])

    def test_the_kill_switch_blocks_everything(self):
        ap.halt_file(self.state).write_text("x")
        with self.assertRaises(ap.GuardError):
            self.go(to.Fake(is_open=True), market())


class ExitTests(Base):
    def held(self, entry_date):
        pos = [{"symbol": "AAA", "qty": "18"}]
        return to.Fake(positions=pos, closed=[entry_order("AAA", 18, filled=entry_date + "T15:00:00Z")], is_open=True)

    def test_a_close_below_the_prior_20_session_low_exits_as_a_trail(self):
        out = self.go(self.held("2026-11-05"), market(last_close_factor=0.80))
        self.assertEqual([(e["action"], e["reason"]) for e in out["exits"]], [("EXIT", "TRAIL")])

    def test_the_40th_session_exits_on_time_and_an_uneventful_day_holds(self):
        data = market(last_close_factor=1.001)
        entry_40 = data["AAA"]["date"][-40]
        out = self.go(self.held(entry_40), data)
        self.assertEqual([(e["action"], e["reason"]) for e in out["exits"]], [("EXIT", "TIME")])
        out = self.go(self.held(data["AAA"]["date"][-10]), data)
        self.assertEqual(out["exits"][0]["action"], "HOLD")

    def test_an_exit_cancels_the_stop_and_sends_a_day_limit_sell_in_the_window(self):
        fake = self.held("2026-11-05")
        fake.open_orders = [{"id": "stop1", "symbol": "AAA", "side": "sell", "type": "stop", "qty": "18", "status": "new", "client_order_id": "x", "legs": []}]
        self.go(fake, market(last_close_factor=0.80), submit=True)
        sells = [b for b in fake.posts() if b["side"] == "sell"]
        self.assertEqual((len(sells), sells[0]["type"], sells[0]["time_in_force"], sells[0]["client_order_id"][:9]), (1, "limit", "day", "s048b55mx"))
        self.assertTrue(any(c[0] == "DELETE" for c in fake.calls))

    def test_a_position_without_a_live_stop_gets_one_on_submit(self):
        fake = self.held("2026-11-10")
        out = self.go(fake, market(last_close_factor=1.001), submit=True)
        stops = [b for b in fake.posts() if b.get("type") == "stop"]
        self.assertEqual((len(stops), stops[0]["time_in_force"], stops[0]["client_order_id"].startswith("s048b55ms-")), (1, "gtc", True))
        self.assertEqual(len(out["stops"]["placed"]), 1)


class GateTests(Base):
    def test_the_gate_checks_a_breakout_plan_against_its_own_profile(self):
        plan = self.go(to.Fake(is_open=True), market())
        self.assertEqual(gate.decide(plan, HEALTHY, now=NOW, tag=bf.TAG)["decision"], gate.PASS)
        # the S-042 profile rejects it: the order id does not carry the S-042 tag
        self.assertEqual(gate.decide(plan, HEALTHY, now=NOW)["decision"], gate.BLOCK_ENTRIES)

    def test_an_oversized_breakout_entry_is_blocked(self):
        plan = self.go(to.Fake(is_open=True), market())
        plan["entries"][0]["qty"] = "100"
        self.assertEqual(gate.decide(plan, HEALTHY, now=NOW, tag=bf.TAG)["decision"], gate.BLOCK_ENTRIES)


class ProtectTests(unittest.TestCase):
    def test_protect_command_replaces_a_missing_stop_only_with_submit(self):
        import contextlib, io, json
        fake = to.Fake(positions=[{"symbol": "AAA", "qty": "18"}], closed=[entry_order("AAA", 18)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(bf.main(["protect"], call=fake, now=NOW), 0)
        self.assertEqual((json.loads(buf.getvalue())["missing"], fake.posts()), (1, []))
        with contextlib.redirect_stdout(io.StringIO()):
            bf.main(["protect", "--submit"], call=fake, now=NOW)
        stops = [b for b in fake.posts() if b.get("type") == "stop"]
        self.assertEqual((len(stops), stops[0]["client_order_id"].startswith("s048b55ms-")), (1, True))


class ReportTests(unittest.TestCase):
    def test_report_pairs_stop_guard_fills_and_counts_only_this_strategy(self):
        closed = [entry_order("AAA", 18), {"id": "g1", "client_order_id": "s048b55ms-AAA-261110", "side": "sell", "symbol": "AAA", "filled_qty": "18", "filled_avg_price": "50", "filled_at": "2026-11-10T16:00:00Z"},
                  entry_order("ZZZ", 5, cid="s042v3-ZZZ-261105")]
        r = bf.report(to.Fake(closed=closed))
        self.assertEqual((r["closed_trades"], r["stops"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
