import contextlib
import io
import json
import random
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import oversold_forward as of

ASOF = date(2026, 11, 13)  # a Friday session
AFTER_CLOSE = datetime(2026, 11, 13, 22, 30, tzinfo=timezone.utc)  # 5:30 PM ET


def dates(n, end=ASOF):
    return [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def series(closes, vol=1e6, dts=None):
    n = len(closes)
    return {"date": dts or dates(n), "o": list(closes), "h": [c * 1.01 for c in closes], "l": [c * 0.99 for c in closes], "c": list(closes), "v": [vol] * n}


def spy_stressed(n=330):
    calm = [100 + 0.01 * i for i in range(n - 30)]
    return series(calm + [calm[-1] * (1.05 if i % 2 else 0.95) for i in range(30)])


def spy_calm(n=330):
    return series([100 + 0.01 * i for i in range(n)])


def stock(crash, n=330, seed=1):
    rnd = random.Random(seed)
    px, cs = 100.0, []
    for _ in range(n - 1):
        px *= 1 + rnd.gauss(0.0005, 0.01)
        cs.append(px)
    cs.append(cs[-1] * (0.70 if crash else 1.0))
    return series(cs)


def market(stressed=True):
    return {"SPY": spy_stressed() if stressed else spy_calm(), "AAA": stock(True, seed=1), "BBB": stock(False, seed=2), "CCC": stock(True, seed=3)}


class Fake:
    def __init__(self, positions=(), open_orders=(), closed=(), is_open=False):
        self.positions, self.open_orders, self.closed, self.is_open = list(positions), list(open_orders), list(closed), is_open
        self.calls = []
        self.splits, self.actions_error = [], False
        self.quote = {"bp": 100.0, "ap": 100.1}
        self.refuse_sells = False   # the broker refuses every sell order (as it did on 2026-11-19)

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
        if path.startswith("/v2/stocks/quotes/latest"):
            return {"quotes": {s: self.quote for s in path.split("symbols=")[1].split(",")}}
        if path.startswith("/v1/corporate-actions"):
            if self.actions_error:
                raise ap.GuardError("HTTP 500")
            return {"corporate_actions": {"forward_splits": [{"symbol": s, "ex_date": "2026-11-17"} for s in self.splits]}}
        if method == "GET" and path.startswith("/v2/orders/"):
            return {"id": path.rsplit("/", 1)[1], "status": "canceled"}
        if method == "POST" and path == "/v2/orders":
            if self.refuse_sells and body["side"] == "sell" and body.get("type") != "stop":
                raise ap.GuardError('HTTP 403 from paper API: {"message":"insufficient qty available for order for ' + body["symbol"] + '"}')
            order = {"id": f"id-{len(self.calls)}", "status": "accepted", "symbol": body["symbol"], "side": body["side"], "client_order_id": body["client_order_id"]}
            if body["side"] == "buy":
                self.open_orders.append(order)
            return order
        if method == "DELETE":
            gone = path.rsplit("/", 1)[1]
            self.open_orders = [o for o in self.open_orders if o.get("id") != gone]
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def posts(self):
        return [c[2] for c in self.calls if c[0] == "POST"]


def fetch_for(data):
    return lambda call, symbols, start, end: {s: d for s, d in data.items() if s in set(symbols)}


class SignalTests(unittest.TestCase):
    def test_oversold_names_signal_and_ranking_is_deterministic(self):
        sig = of.compute_candidates(market(), ["AAA", "BBB", "CCC"], ASOF)
        self.assertTrue(sig["stress"])
        syms = [c["sym"] for c in sig["candidates"]]
        self.assertEqual(set(syms), {"AAA", "CCC"})
        self.assertEqual(syms, sorted(syms, key=lambda s: next((c["rsi2"], c["z"], c["sym"]) for c in sig["candidates"] if c["sym"] == s)))
        self.assertLessEqual(sig["candidates"][0]["z"], -2.0)

    def test_no_v3_signal_without_market_stress_but_v2_still_counts(self):
        sig = of.compute_candidates(market(stressed=False), ["AAA", "BBB", "CCC"], ASOF)
        self.assertFalse(sig["stress"])
        self.assertEqual((sig["v2_signals"], len(sig["candidates"])), (2, 0))

    def test_stale_short_and_ineligible_names_are_reported_or_skipped(self):
        m = market()
        m["STALE"] = stock(True, seed=4)
        m["STALE"]["date"] = dates(330, ASOF - timedelta(days=3))
        m["SHORT"] = stock(True, n=100, seed=5)
        m["CHEAP"] = series([4.0] * 329 + [2.5])
        sig = of.compute_candidates(m, ["AAA", "STALE", "SHORT", "CHEAP"], ASOF)
        self.assertEqual(sig["stale"], ["STALE"])
        self.assertEqual(sig["short_history"], ["SHORT"])
        self.assertNotIn("CHEAP", [c["sym"] for c in sig["candidates"]])

    def test_missing_spy_bar_is_refused(self):
        m = market()
        m["SPY"]["date"] = dates(330, ASOF - timedelta(days=1))
        with self.assertRaises(ap.GuardError):
            of.compute_candidates(m, ["AAA"], ASOF)

    def test_entry_body_size_stop_and_id(self):
        b = of.entry_body({"sym": "AAA", "close": 72.5}, ASOF)
        self.assertEqual((b["qty"], b["order_class"], b["type"], b["time_in_force"], b["side"]), ("41", "oto", "market", "day", "buy"))   # 3,000 USD entry: floor(3000 / 72.5)
        self.assertEqual((b["stop_loss"]["stop_price"], b["client_order_id"]), ("65.25", "s042v3-AAA-261113"))
        with self.assertRaises(ap.GuardError):
            of.entry_body({"sym": "BIG", "close": 4000.0}, ASOF)


class TrimTests(unittest.TestCase):
    def test_a_partial_bar_after_the_asof_session_is_dropped(self):
        d = {"SPY": series([100, 101, 102]), "X": series([5, 6, 7])}
        d["SPY"]["date"][-1] = (ASOF + timedelta(days=3)).isoformat()
        out = of.trim(d, ASOF)
        self.assertEqual((len(out["SPY"]["c"]), len(out["SPY"]["v"]), len(out["X"]["c"])), (2, 2, 3))


class SessionTests(unittest.TestCase):
    def test_last_complete_session(self):
        self.assertEqual(of.last_complete_session(AFTER_CLOSE), ASOF)
        self.assertEqual(of.last_complete_session(datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)), ASOF)  # Saturday
        self.assertEqual(of.last_complete_session(datetime(2026, 11, 13, 21, 5, tzinfo=timezone.utc)), date(2026, 11, 12))  # 5 min after the close
        self.assertEqual(of.last_complete_session(datetime(2026, 11, 13, 21, 20, tzinfo=timezone.utc)), ASOF)


class ExitTests(unittest.TestCase):
    def data(self, closes):
        return {"AAA": series(closes)}

    def test_exit_when_close_above_sma5(self):
        d = self.data([100] * 325 + [90, 90, 90, 90, 95])
        out = of.plan_exits([{"sym": "AAA", "qty": 5, "entry_date": d["AAA"]["date"][-3]}], d, ASOF)
        self.assertEqual((out[0]["action"], out[0]["reason"]), ("EXIT", "CLOSE_ABOVE_SMA5"))

    def test_time_exit_on_the_fifth_session_and_hold_before_it(self):
        flat = [100] * 325 + [90, 89, 88, 87, 86]
        d = self.data(flat)
        entry5 = d["AAA"]["date"][-5]
        self.assertEqual(of.plan_exits([{"sym": "AAA", "qty": 5, "entry_date": entry5}], d, ASOF)[0]["reason"], "TIME_5_SESSIONS")
        entry3 = d["AAA"]["date"][-3]
        r = of.plan_exits([{"sym": "AAA", "qty": 5, "entry_date": entry3}], d, ASOF)[0]
        self.assertEqual((r["action"], r["hold_days"]), ("HOLD", 3))

    def test_missing_or_stale_data_never_exits(self):
        d = self.data([100] * 330)
        out = of.plan_exits([{"sym": "ZZZ", "qty": 1, "entry_date": "2026-11-10"}, {"sym": "AAA", "qty": 1, "entry_date": "2020-01-01"}], d, ASOF)
        self.assertEqual([o["action"] for o in out], ["HOLD", "HOLD"])
        self.assertEqual([o["reason"] for o in out], ["NO_FRESH_DATA", "ENTRY_DATE_NOT_IN_BARS"])


class ReportTests(unittest.TestCase):
    def test_report_pairs_stop_legs_and_rule_exits_and_ignores_other_orders(self):
        closed = [
            {"id": "b1", "client_order_id": "s042v3-AAA-261105", "side": "buy", "symbol": "AAA", "filled_qty": "10", "filled_avg_price": "100", "filled_at": "2026-11-06T14:30:00Z",
             "legs": [{"side": "sell", "filled_qty": "10", "filled_avg_price": "90", "type": "stop"}]},
            {"id": "b2", "client_order_id": "s042v3-BBB-261105", "side": "buy", "symbol": "BBB", "filled_qty": "5", "filled_avg_price": "200", "filled_at": "2026-11-06T14:30:00Z",
             "legs": [{"side": "sell", "filled_qty": "0", "type": "stop"}]},
            {"id": "x1", "client_order_id": "s042v3x-BBB-261110", "side": "sell", "symbol": "BBB", "filled_qty": "5", "filled_avg_price": "210", "filled_at": "2026-11-10T14:30:00Z"},
            {"id": "z", "client_order_id": "something-else", "side": "buy", "symbol": "ZZZ", "filled_qty": "1", "filled_avg_price": "5", "filled_at": "2026-11-06T14:30:00Z"},
            {"id": "b3", "client_order_id": "s042v3-CCC-261112", "side": "buy", "symbol": "CCC", "filled_qty": "3", "filled_avg_price": "50", "filled_at": "2026-11-13T14:30:00Z", "legs": []},
        ]
        fake = Fake(positions=[{"symbol": "CCC", "qty": "3"}], closed=closed)
        fake.closed_orders_path = None
        r = of.report(fake)
        self.assertEqual((r["closed_trades"], r["stops"], r["total_pnl"], r["open_positions"]), (2, 1, -50.0, 1))
        self.assertAlmostEqual(r["mean_ret_pct"], (-10.0 + 5.0) / 2)

    def test_report_with_no_trades_is_empty_not_an_error(self):
        r = of.report(Fake())
        self.assertEqual((r["closed_trades"], r["mean_ret_pct"], r["open_positions"]), (0, None, 0))


def losing_closed(n, qty, entry, exit_px, start=0):
    """n closed S-042 trades: a filled entry whose stop leg filled at exit_px."""
    return [{"id": f"h{start + i}", "client_order_id": f"s042v3-L{start + i:03d}-261105", "side": "buy", "symbol": f"L{start + i:03d}", "filled_qty": str(qty), "filled_avg_price": str(entry),
             "filled_at": "2026-11-06T14:30:00Z", "legs": [{"side": "sell", "filled_qty": str(qty), "filled_avg_price": str(exit_px), "type": "stop"}]} for i in range(n)]


class HaltTests(unittest.TestCase):
    def test_not_halted_on_a_small_loss_or_few_trades(self):
        self.assertFalse(of.halt_status(Fake(closed=losing_closed(9, 10, 100, 90)))["halted"])          # -900 USD, mean -10 percent, only 9 trades
        self.assertFalse(of.halt_status(Fake(closed=losing_closed(29, 10, 100, 99)))["halted"])         # 29 trades: the mean rule needs 30

    def test_halts_below_the_usd_line(self):
        st = of.halt_status(Fake(closed=losing_closed(2, 1000, 100, 90)))                              # -20,000 USD
        self.assertEqual((st["halted"], st["reason"], st["closed"]), (True, "HALT_USD", 2))

    def test_halts_on_a_negative_mean_at_30_closed_trades(self):
        st = of.halt_status(Fake(closed=losing_closed(30, 10, 100, 99)))                               # -300 USD total, mean -1 percent
        self.assertEqual((st["halted"], st["reason"], st["closed"]), (True, "HALT_MEAN", 30))

    def test_an_unreadable_order_never_halts_but_blocks_entries(self):
        broken = Fake(closed=[{"client_order_id": "s042v3-BAD-261105", "side": "buy", "symbol": "BAD", "filled_qty": "5"}])    # no fill price
        st = of.halt_status(broken)
        self.assertEqual((st["halted"], st["reason"]), (False, "CHECK_ERROR"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)

    def go(self, fake, submit=True, data=None):
        return of.run(fake, ["AAA", "BBB", "CCC"], AFTER_CLOSE, self.state, submit, fetch_for(data or market()))

    def test_dry_run_sends_nothing(self):
        fake = Fake()
        out = self.go(fake, submit=False)
        self.assertEqual((out["v3_signals"], len(out["entries"]), fake.posts()), (2, 2, []))

    def test_submit_while_the_market_is_open_sends_nothing(self):
        fake = Fake(is_open=True)
        out = self.go(fake)
        self.assertEqual(fake.posts(), [])
        self.assertIn("market is open", out["note"])

    def test_submit_after_close_queues_entries_once(self):
        fake = Fake()
        out = self.go(fake)
        posts = fake.posts()
        self.assertEqual(sorted(b["symbol"] for b in posts), ["AAA", "CCC"])
        self.assertTrue(all(b["time_in_force"] == "day" and b["type"] == "market" and b["order_class"] == "oto" for b in posts))
        again = self.go(fake)  # same day: the pending tagged orders are seen, nothing new goes out
        self.assertEqual(len(fake.posts()), 2)
        self.assertEqual({s["why"] for s in again["skipped"]}, {"ALREADY_HELD_OR_PENDING"})
        self.assertIn("s042-entry", (self.state / "orders.jsonl").read_text())

    def test_position_cap_limits_new_entries(self):
        pending = [{"symbol": f"P{i}", "side": "buy", "client_order_id": f"s042v3-P{i}-261112", "id": f"o{i}"} for i in range(of.POSITION_CAP - 1)]
        fake = Fake(open_orders=pending)
        out = self.go(fake)
        self.assertEqual((out["slots"], len(out["entries"])), (1, 1))
        self.assertEqual([s["why"] for s in out["skipped"]], ["NO_FREE_SLOT"])

    def test_exit_cancels_the_stop_and_queues_a_market_sell(self):
        d = market()
        d["HELD"] = series([100] * 325 + [90, 90, 90, 90, 95])
        entry = d["HELD"]["date"][-3]
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}],
                    closed=[{"client_order_id": "s042v3-HELD-261111", "filled_qty": "7", "filled_avg_price": "100", "side": "buy", "symbol": "HELD", "filled_at": entry + "T14:30:01Z"}],
                    open_orders=[{"symbol": "HELD", "side": "sell", "id": "stop-1", "client_order_id": "x"}])
        out = of.run(fake, ["AAA", "BBB", "CCC", "HELD"], AFTER_CLOSE, self.state, True, fetch_for(d))
        self.assertIn(("DELETE", "/v2/orders/stop-1", None), fake.calls)
        sells = [b for b in fake.posts() if b["side"] == "sell"]
        self.assertEqual((len(sells), sells[0]["symbol"], sells[0]["qty"], sells[0]["time_in_force"]), (1, "HELD", "7", "day"))
        self.assertEqual(out["exits"][0]["reason"], "CLOSE_ABOVE_SMA5")

    def held_exit_fixture(self):
        d = market()
        d["HELD"] = series([100] * 325 + [90, 90, 90, 90, 95])
        entry = d["HELD"]["date"][-3]
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}],
                    closed=[{"client_order_id": "s042v3-HELD-261111", "filled_qty": "7", "filled_avg_price": "100", "side": "buy", "symbol": "HELD", "filled_at": entry + "T14:30:01Z",
                             "legs": [{"side": "sell", "stop_price": "90.00"}]}],
                    open_orders=[{"symbol": "HELD", "side": "sell", "type": "stop", "status": "new", "qty": "7", "id": "stop-1", "client_order_id": "x"}])
        return d, fake

    def test_a_refused_exit_puts_the_stop_back_and_does_not_stop_the_run(self):
        """2026-11-19: the exit cancelled the stop, the broker refused the sell, and the position was left without a stop."""
        d, fake = self.held_exit_fixture()
        fake.refuse_sells = True
        out = of.run(fake, ["AAA", "BBB", "CCC", "HELD"], AFTER_CLOSE, self.state, True, fetch_for(d))
        refused = [s for s in out["skipped"] if s["why"] == "ORDER_REFUSED"]
        self.assertEqual(len(refused), 1)
        self.assertNotIn("HELD", refused[0]["detail"])      # symbols never go into a log
        self.assertIn("insufficient qty", refused[0]["detail"])
        stops = [b for b in fake.posts() if b.get("type") == "stop"]
        self.assertEqual((len(stops), stops[0]["symbol"], stops[0]["qty"], stops[0]["time_in_force"]), (1, "HELD", "7", "gtc"))
        self.assertEqual(sorted(b["symbol"] for b in fake.posts() if b["side"] == "buy"), ["AAA", "CCC"])   # entries still went out

    def test_an_exit_with_no_usable_quote_leaves_the_stop_untouched(self):
        d, fake = self.held_exit_fixture()
        fake.quote = {"bp": 90.0, "ap": 110.0}   # spread far too wide
        fake.is_open = True
        monday_10am = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
        out = of.run(fake, ["AAA", "BBB", "CCC", "HELD"], monday_10am, self.state, True, fetch_for(d), mode="morning")
        self.assertIn({"sym": "HELD", "why": "EXIT_QUOTE_TOO_WIDE"}, out["skipped"])
        self.assertFalse([c for c in fake.calls if c[0] == "DELETE"])

    def test_cancel_and_wait_polls_until_the_broker_confirms(self):
        seen, states = [], iter(["pending_cancel", "pending_cancel", "canceled"])

        def call(method, path, body=None, host="paper"):
            seen.append((method, path))
            return {"status": next(states)} if method == "GET" else {}
        of.cancel_and_wait(call, ["a"], sleep=lambda s: None)
        self.assertEqual([m for m, _ in seen], ["DELETE", "GET", "GET", "GET"])

    def test_halt_exits_only_this_strategy_and_sends_no_entry(self):
        """Owner decision 2026-11-21: the S-042 halt closes S-042 positions and blocks S-042 entries, nothing else."""
        d = market()
        d["HELD"] = series([100] * 325 + [90, 90, 90, 90, 80])     # still below its SMA5: no rule exit on its own
        entry = d["HELD"]["date"][-3]
        held_entry = {"client_order_id": "s042v3-HELD-261111", "filled_qty": "7", "filled_avg_price": "100", "side": "buy", "symbol": "HELD", "filled_at": entry + "T14:30:01Z",
                      "legs": [{"side": "sell", "stop_price": "90.00"}]}
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[held_entry] + losing_closed(2, 1000, 100, 90),
                    open_orders=[{"symbol": "HELD", "side": "sell", "type": "stop", "status": "new", "qty": "7", "id": "stop-1", "client_order_id": "x"}])
        fake.is_open = True
        out = of.run(fake, ["AAA", "BBB", "CCC", "HELD"], datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc), self.state, True, fetch_for(d), mode="morning")
        self.assertTrue(out["halted"])
        self.assertEqual([e["reason"] for e in out["exits"] if e["sym"] == "HELD"], ["S042_HALT"])
        self.assertEqual([b["symbol"] for b in fake.posts() if b["side"] == "buy"], [])                 # no entry
        self.assertEqual([(b["symbol"], b["side"]) for b in fake.posts() if b["side"] == "sell"], [("HELD", "sell")])
        self.assertTrue(all(s["why"] == "ENTRY_BLOCKED_BY_HALT" for s in out["skipped"] if s["sym"] in ("AAA", "CCC")))

    def test_corporate_action_soon_skips_the_name_and_endpoint_errors_are_soft(self):
        fake = Fake()
        fake.splits = ["AAA"]
        out = self.go(fake)
        self.assertEqual([b["symbol"] for b in fake.posts()], ["CCC"])
        self.assertIn({"sym": "AAA", "why": "CORPORATE_ACTION_SOON"}, out["skipped"])
        broken = Fake()
        broken.actions_error = True
        out = self.go(broken)
        self.assertEqual(sorted(b["symbol"] for b in broken.posts()), ["AAA", "CCC"])
        self.assertIn("500", out["corporate_actions_error"])

    def test_a_bad_series_is_quarantined_before_it_can_signal(self):
        m = market()
        c = list(m["AAA"]["c"])
        c[-1] = c[-2] * 0.5  # looks like an unadjusted 2-for-1 split on the signal day
        m["AAA"] = series(c)
        sig = of.compute_candidates(m, ["AAA", "BBB", "CCC"], ASOF)
        self.assertIn("SPLIT_SUSPECT", sig["quarantined"].get("AAA", []))
        self.assertNotIn("AAA", [x["sym"] for x in sig["candidates"]])

    def test_candidates_carry_a_cost_estimate_and_the_report_shows_entry_gap(self):
        sig = of.compute_candidates(market(), ["AAA", "CCC"], ASOF)
        self.assertTrue(all(c["est_cost_bps"] > 0 for c in sig["candidates"]))
        closed = [{"id": "e1", "client_order_id": "s042v3-AAA-261106", "side": "buy", "symbol": "AAA", "filled_qty": "10", "filled_avg_price": "90.10",
                   "filled_at": "2026-11-09T14:30:00Z", "legs": [{"side": "sell", "filled_qty": "10", "filled_avg_price": "95", "stop_price": "81.00"}]}]
        r = of.report(Fake(closed=closed))
        self.assertAlmostEqual(r["trades"][0]["entry_gap_bps"], 11.1, places=1)
        self.assertEqual(r["entry_gap_vs_signal_close_bps"]["n"], 1)

    def test_halt_blocks_everything(self):
        (self.state / "HALT").write_text("x")
        fake = Fake()
        with self.assertRaises(ap.GuardError):
            self.go(fake)
        self.assertEqual(fake.calls, [])

    def test_cli_error_paths_and_universe_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = Path(tmp) / "u.txt"
            u.write_text("aaa\n# comment\nBBB  # trailing\n\nCCC\n")
            self.assertEqual(of.load_universe(u), ["AAA", "BBB", "CCC"])
            (Path(tmp) / "e.txt").write_text("# nothing\n")
            with self.assertRaises(ap.GuardError):
                of.load_universe(Path(tmp) / "e.txt")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = of.main(["--state-dir", tmp, "run", "--universe", str(u)], call=Fake(), now=AFTER_CLOSE, fetch=fetch_for(market()))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buf.getvalue())["v3_signals"], 2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = of.main(["--state-dir", tmp, "signals", "--universe", str(u)], call=Fake(), now=AFTER_CLOSE, fetch=fetch_for(market()))
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()


class MorningModeTests(unittest.TestCase):
    TUE_10AM = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)  # 10:00 ET

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())

    def go(self, fake, now=None, data=None):
        # signals come from the last complete session; the fake market ends on ASOF (Friday) so use a Monday 10:00 ET
        now = now or datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
        return of.run(fake, ["AAA", "BBB", "CCC"], now, self.state, True, fetch_for(market()), mode="morning")

    def test_entries_are_marketable_limits_with_a_stop_and_go_once(self):
        fake = Fake(is_open=True)
        out = self.go(fake)
        posts = fake.posts()
        self.assertEqual(sorted(b["symbol"] for b in posts), ["AAA", "CCC"])
        for b in posts:
            self.assertEqual((b["type"], b["time_in_force"], b["order_class"]), ("limit", "gtc", "oto"))   # gtc, so the attached stop outlives the session
            self.assertAlmostEqual(float(b["limit_price"]), round(100.1 * 1.003, 2), places=2)
            self.assertIn("stop_loss", b)
        self.go(fake)
        self.assertEqual(len(fake.posts()), 2)  # idempotent: the second run sees the pending tagged orders

    def test_outside_the_window_or_market_closed_sends_nothing(self):
        for fake, now in ((Fake(is_open=False), None), (Fake(is_open=True), datetime(2026, 11, 16, 18, 30, tzinfo=timezone.utc))):
            out = self.go(fake, now)
            self.assertEqual(fake.posts(), [])
            self.assertIn("morning mode", out["note"])

    def test_wide_or_missing_quote_skips_the_name(self):
        fake = Fake(is_open=True)
        fake.quote = {"bp": 90.0, "ap": 100.0}  # about 10 percent wide
        out = self.go(fake)
        self.assertEqual(fake.posts(), [])
        self.assertEqual({s["why"] for s in out["skipped"]}, {"ENTRY_QUOTE_TOO_WIDE"})

    def test_morning_exit_sells_stay_day_orders(self):
        body, why = of.morning_limit({"symbol": "X", "qty": "5", "side": "sell", "type": "market", "time_in_force": "day"}, (100.0, 100.2), "sell")
        self.assertEqual((body["time_in_force"], why), ("day", None))

    def test_morning_limit_math(self):
        body, why = of.morning_limit({"symbol": "X"}, (50.0, 50.1), "sell")
        self.assertEqual((body["type"], body["limit_price"], why), ("limit", f"{round(50.0 * 0.997, 2):.2f}", None))
        self.assertEqual(of.morning_limit({}, None, "buy"), (None, "NO_QUOTE"))


HELD_ENTRY = {"id": "e1", "client_order_id": "s042v3-HELD-261110", "side": "buy", "symbol": "HELD", "filled_qty": "7", "filled_avg_price": "100", "filled_at": "2026-11-10T15:00:00Z",
              "legs": [{"side": "sell", "type": "stop", "stop_price": "90.00", "status": "expired", "filled_qty": "0"}]}
NOW_MORNING = datetime(2026, 11, 18, 15, 5, tzinfo=timezone.utc)


class StopGuardTests(unittest.TestCase):
    def state(self, fake):
        return of.broker_state(fake)

    def test_a_position_with_no_live_stop_gets_a_gtc_stop_at_the_recorded_price(self):
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY])
        out = of.ensure_stops(fake, self.state(fake), NOW_MORNING, True)
        self.assertEqual((out["missing"], len(out["placed"]), out["skipped"]), (1, 1, []))
        b = fake.posts()[0]
        self.assertEqual((b["symbol"], b["qty"], b["side"], b["type"], b["time_in_force"], b["stop_price"], b["client_order_id"]), ("HELD", "7", "sell", "stop", "gtc", "90.00", "s042v3s-HELD-261118"))

    def test_covered_positions_get_nothing_and_a_partial_cover_gets_the_difference(self):
        live = {"symbol": "HELD", "side": "sell", "type": "stop", "qty": "7", "status": "new", "legs": []}
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY], open_orders=[live])
        self.assertEqual(of.ensure_stops(fake, self.state(fake), NOW_MORNING, True)["missing"], 0)
        self.assertEqual(fake.posts(), [])
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY], open_orders=[{**live, "qty": "3"}])
        of.ensure_stops(fake, self.state(fake), NOW_MORNING, True)
        self.assertEqual(fake.posts()[0]["qty"], "4")

    def test_a_stop_leg_nested_in_a_pending_parent_counts_as_cover(self):
        parent = {"symbol": "HELD", "side": "buy", "type": "limit", "status": "filled", "legs": [{"symbol": "HELD", "side": "sell", "type": "stop", "qty": "7", "status": "held"}]}
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY], open_orders=[parent])
        self.assertEqual(of.ensure_stops(fake, self.state(fake), NOW_MORNING, True)["missing"], 0)

    def test_dry_run_reports_the_gap_and_sends_nothing(self):
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY])
        out = of.ensure_stops(fake, self.state(fake), NOW_MORNING, False)
        self.assertEqual((out["missing"], out["placed"], fake.posts()), (1, [], []))

    def test_excluded_symbols_and_missing_references_are_handled(self):
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY])
        self.assertEqual(of.ensure_stops(fake, self.state(fake), NOW_MORNING, True, exclude={"HELD"})["missing"], 0)
        no_leg = {**HELD_ENTRY, "legs": []}
        fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[no_leg])
        out = of.ensure_stops(fake, self.state(fake), NOW_MORNING, True)
        self.assertEqual((out["placed"], out["skipped"], fake.posts()), ([], [{"sym": "HELD", "why": "NO_STOP_REFERENCE"}], []))

    def test_a_broker_rejection_is_reported_not_raised(self):
        class Reject(Fake):
            def __call__(self, method, path, body=None, host="paper"):
                if method == "POST":
                    raise ap.GuardError("HTTP 422 stop price above market")
                return super().__call__(method, path, body, host)
        fake = Reject(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY])
        out = of.ensure_stops(fake, self.state(fake), NOW_MORNING, True)
        self.assertEqual((out["placed"], out["skipped"][0]["sym"]), ([], "HELD"))

    def test_run_replaces_the_stop_even_when_entries_are_blocked_and_skips_a_symbol_that_is_leaving(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Fake(positions=[{"symbol": "HELD", "qty": "7"}], closed=[HELD_ENTRY], is_open=True)
            fm = datetime(2026, 11, 16, 15, 5, tzinfo=timezone.utc)
            out = of.run(fake, ["AAA", "BBB", "CCC"], fm, Path(td), True, fetch_for(market()), "morning", entries_allowed=False)
            stops = [b for b in fake.posts() if b.get("type") == "stop"]
            self.assertEqual((len(stops), len(out["stops"]["placed"])), (1, 1))
            self.assertFalse([b for b in fake.posts() if b["side"] == "buy"])

    def test_a_stop_guard_fill_pairs_as_a_stop_exit_in_the_closed_trades(self):
        stopfill = {"id": "g1", "client_order_id": "s042v3s-HELD-261118", "side": "sell", "symbol": "HELD", "filled_qty": "7", "filled_avg_price": "89.50", "filled_at": "2026-11-18T16:00:00Z"}
        fake = Fake(closed=[HELD_ENTRY, stopfill])
        trades, _ = of.closed_trades(fake)
        self.assertEqual((len(trades), trades[0]["how"], trades[0]["pnl"]), (1, "STOP", -73.5))
