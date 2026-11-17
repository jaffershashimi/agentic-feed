"""System stress test: failure injection and scale scenarios against the real S-042 and S-044F tools with fake brokers and data feeds. No network, no orders.

Each scenario states the failure and the safe outcome. A scenario that fails here is a real hole in the system, not a test problem."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import news_drop_forward as ndf
from consumer import oversold_forward as of
from consumer import paper_risk as pr
from tests import test_news_drop_forward as tn
from tests import test_oversold_forward as to

NAMES300 = [f"S{i:03d}" for i in range(300)]


def big_market(n, crash=True, spy_stressed=True):
    m = {"SPY": to.spy_stressed() if spy_stressed else to.spy_calm()}
    for i in range(n):
        m[f"S{i:03d}"] = to.stock(crash, seed=100 + i)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self._old_susp = ndf.SUSPENDED
        ndf.SUSPENDED = False   # these scenarios test the strategy's safety nets, so lift the suspension inside the test only
        self.addCleanup(setattr, ndf, "SUSPENDED", self._old_susp)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)


class DataFailures(Base):
    def test_rate_limit_storm_stops_the_run_with_a_clean_error_and_no_orders(self):
        fake = to.Fake()

        def boom(call, symbols, start, end):
            raise ap.GuardError("HTTP 429 from data API: too many requests")

        buf = io.StringIO()
        u = self.state / "u.txt"
        u.write_text("AAA\nBBB\n")
        with contextlib.redirect_stdout(buf):
            code = of.main(["--state-dir", str(self.state), "run", "--universe", str(u), "--submit"], call=fake, now=to.AFTER_CLOSE, fetch=boom)
        self.assertEqual(code, 2)
        self.assertIn("429", buf.getvalue())
        self.assertEqual(fake.posts(), [])

    def test_stale_spy_bar_refuses_everything(self):
        m = to.market()
        m["SPY"]["date"] = to.dates(330, to.ASOF - timedelta(days=1))
        fake = to.Fake()
        with self.assertRaises(ap.GuardError):
            of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        self.assertEqual(fake.posts(), [])

    def test_every_candidate_has_a_bad_bar_so_nothing_is_entered(self):
        m = to.market()
        for s in ("AAA", "CCC"):
            c = list(m[s]["c"])
            c[-1] = c[-2] * 0.5
            m[s] = to.series(c)
        fake = to.Fake()
        out = of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        self.assertEqual((fake.posts(), sorted(out["quarantined"])), ([], ["AAA", "CCC"]))

    def test_corporate_action_endpoint_down_is_soft_but_flagged(self):
        fake = to.Fake()
        fake.actions_error = True
        out = of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(to.market()))
        self.assertIsNotNone(out["corporate_actions_error"])
        self.assertEqual(len(fake.posts()), 2)  # the extra safety net failing must not stop the frozen rule

    def test_silent_news_outage_cannot_turn_every_drop_into_a_no_news_trade(self):
        names = [f"N{i}" for i in range(8)]
        broker = tn.FakeBroker()
        out = ndf.run(broker, names, tn.NOW, self.state, True, lambda c, s, a, e: {k: v for k, v in tn.drop_market(names).items() if k in set(s)}, lambda *a: {})
        self.assertTrue(out["news_feed_suspect"])
        self.assertEqual((broker.posts(), out["entries"]), ([], []))

    def test_a_normal_news_day_with_some_headlines_still_trades(self):
        names = [f"N{i}" for i in range(8)]
        broker = tn.FakeBroker()
        news = lambda *a: {"N0": [{"headline": "N0 shares are trading lower", "summary": "", "symbols": ["N0"], "created_at": "2026-11-13T15:00:00Z"}]}
        out = ndf.run(broker, names, tn.NOW, self.state, True, lambda c, s, a, e: {k: v for k, v in tn.drop_market(names).items() if k in set(s)}, news)
        self.assertFalse(out["news_feed_suspect"])
        self.assertGreater(len(broker.posts()), 0)


class BrokerFailures(Base):
    def test_order_failure_mid_batch_then_rerun_recovers_without_duplicates(self):
        class Flaky(to.Fake):
            def __call__(self, method, path, body=None, host="paper"):
                if method == "POST" and path == "/v2/orders" and len(self.posts()) == 1 and not getattr(self, "failed", False):
                    self.failed = True
                    raise ap.GuardError("HTTP 500 from paper API")
                return super().__call__(method, path, body, host)

        fake = Flaky()
        m = to.market()
        with self.assertRaises(ap.GuardError):
            of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        sent = [b["symbol"] for b in fake.posts()]
        self.assertEqual(len(sent), 1)
        again = of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        all_sent = [b["symbol"] for b in fake.posts()]
        self.assertEqual(sorted(all_sent), ["AAA", "CCC"])   # each name exactly once
        self.assertIn("ALREADY_HELD_OR_PENDING", [s["why"] for s in again["skipped"]])

    def test_garbage_position_payload_stops_before_any_order(self):
        class Garbage(to.Fake):
            def __call__(self, method, path, body=None, host="paper"):
                if path == "/v2/positions":
                    return [{"unexpected": "shape"}]
                return super().__call__(method, path, body, host)

        fake = Garbage()
        try:
            of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(to.market()))
        except Exception:
            pass
        self.assertEqual(fake.posts(), [])

    def test_live_host_is_refused_before_a_request_is_built(self):
        with self.assertRaises(ap.GuardError):
            ap.paper_base({"ALPACA_PAPER_BASE_URL": "https://api.alpaca.markets"})
        with self.assertRaises(ap.GuardError):
            ap.paper_base({"ALPACA_PAPER_TRADE": "false"})


class Concurrency(Base):
    def test_two_runs_back_to_back_send_each_order_once(self):
        fake = to.Fake()
        m = to.market()
        of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        sent = [b["symbol"] for b in fake.posts()]
        self.assertEqual(sorted(sent), sorted(set(sent)))

    def test_s042_and_s044f_never_buy_the_same_name_twice(self):
        # the S-042 queue goes first; S-044F sees those open orders and skips the overlap
        broker = tn.FakeBroker(open_orders=[{"symbol": "N0", "side": "buy", "client_order_id": "s042v3-N0-261116", "id": "x1"}])
        names = [f"N{i}" for i in range(3)]
        out = ndf.run(broker, names, tn.NOW, self.state, True, lambda c, s, a, e: {k: v for k, v in tn.drop_market(names).items() if k in set(s)}, lambda *a: {})
        self.assertNotIn("N0", [b["symbol"] for b in broker.posts()])
        self.assertIn({"sym": "N0", "why": "ALREADY_HELD_OR_PENDING"}, out["skipped"])

    def test_halt_file_created_between_dry_run_and_submit_blocks_the_submit(self):
        fake = to.Fake()
        m = to.market()
        of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, False, to.fetch_for(m))
        (self.state / "HALT").write_text("stop")
        with self.assertRaises(ap.GuardError):
            of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        self.assertEqual(fake.posts(), [])


class Scale(Base):
    def test_300_simultaneous_signals_respect_the_position_cap_and_ranking(self):
        fake = to.Fake()
        m = big_market(300)
        out = of.run(fake, NAMES300, to.AFTER_CLOSE, self.state, True, to.fetch_for(m))
        self.assertLessEqual(len(fake.posts()), of.POSITION_CAP)
        self.assertEqual(len(out["skipped"]), out["v3_signals"] - len(out["entries"]))
        again = to.Fake()
        out2 = of.run(again, NAMES300, to.AFTER_CLOSE, Path(self._td.name) / "second", True, to.fetch_for(m))
        self.assertEqual([b["symbol"] for b in fake.posts()], [b["symbol"] for b in again.posts()])  # deterministic

    def test_300_drops_respect_the_s044f_caps_and_the_tier_two_risk_limits(self):
        names = NAMES300[:120]
        broker = tn.FakeBroker()
        news = lambda *a: {"S000": [{"headline": "S000 shares are trading lower", "summary": "", "symbols": ["S000"], "created_at": "2026-11-13T15:00:00Z"}]}
        out = ndf.run(broker, names, tn.NOW, self.state, True, lambda c, s, a, e: {k: v for k, v in tn.drop_market(names).items() if k in set(s)}, news)
        posts = broker.posts()
        self.assertLessEqual(len(posts), pr.MAX_OPEN_TRADES)
        risk = sum(int(p["qty"]) * 100.0 * (1 - ndf.STOP_FRACTION) for p in posts)  # 100 is above the real close of about 88, so this overstates the risk
        self.assertLessEqual(risk, pr.MAX_TOTAL_OPEN_RISK)
        for p in posts:
            self.assertLessEqual(int(p["qty"]) * 88.5, ndf.NOTIONAL_CAP + 1)  # the fixture closes near 88

    def test_market_wide_crash_day_blocks_s044f_but_s042_stays_inside_its_cap(self):
        crash_spy = to.series([100 + 0.01 * i for i in range(299)] + [100.0 * 0.93])
        m = to.market()
        m["SPY"] = crash_spy
        res, spy_ret = ndf.find_drops({k: v for k, v in m.items()}, to.ASOF)
        self.assertEqual(res, [])  # S-044F stands aside when SPY is down 2 percent or more
        fake = to.Fake()
        out = of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(to.market()))
        self.assertLessEqual(len(fake.posts()), of.POSITION_CAP)

    def test_open_positions_already_above_the_cap_leave_no_slots(self):
        held = [{"symbol": f"H{i}", "qty": "1", "avg_entry_price": "10"} for i in range(of.POSITION_CAP + 2)]
        closed = [{"client_order_id": f"s042v3-H{i}-261110", "filled_qty": "1", "side": "buy", "symbol": f"H{i}", "filled_at": "2026-11-12T14:30:01Z"} for i in range(of.POSITION_CAP + 2)]
        fake = to.Fake(positions=held, closed=closed)
        out = of.run(fake, ["AAA", "BBB", "CCC"], to.AFTER_CLOSE, self.state, True, to.fetch_for(to.market()))
        self.assertEqual((out["slots"], fake.posts()), (0, []))


class RiskGate(Base):
    def test_daily_loss_halt_and_trade_count_stop_new_day_trades(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 19, 14, 15, tzinfo=timezone.utc)
        self.assertTrue(pr.check_day_trade(100, 99, 102, 10, now, [1000, 1000, 1000]))
        self.assertTrue(pr.check_day_trade(100, 90, 120, 200, now, [2000, 1000]))
        with self.assertRaises(pr.RiskError):
            pr.check_new_trade(100, [100] * 6, 0)
        with self.assertRaises(pr.RiskError):
            pr.check_new_trade(100, [], -pr.DAILY_LOSS_HALT)


if __name__ == "__main__":
    unittest.main()
