import unittest
from datetime import date, timedelta

from consumer import oversold_forward as of
from consumer import oversold_panel as op
from engine import backtest
from engine.portfolio import Portfolio
from engine.simbroker import SimBroker
from engine.strategies.oversold_v3 import OversoldV3
from engine.strategy import Context
from engine.types import Intent, Position
from tests import test_oversold_forward as to


def intent(sym, rank, close=100.0, notional=1000.0):
    return Intent("t", sym, notional, 0.9, rank, close)


class PortfolioTests(unittest.TestCase):
    def test_takes_the_best_ranked_that_fit_and_reports_the_rest(self):
        orders, skipped = Portfolio(2).select([intent("C", (3,)), intent("A", (1,)), intent("B", (2,))], [])
        self.assertEqual([o[0].symbol for o in orders], ["A", "B"])
        self.assertEqual(skipped, [("C", "NO_FREE_SLOT")])

    def test_held_names_are_skipped_and_exits_free_slots(self):
        held = [Position("t", "H1", 5, "2026-01-02", 10.0, 9.0), Position("t", "H2", 5, "2026-01-02", 10.0, 9.0)]
        orders, skipped = Portfolio(2).select([intent("H1", (1,)), intent("N", (2,))], held)
        self.assertEqual((orders, skipped), ([], [("H1", "ALREADY_HELD"), ("N", "NO_FREE_SLOT")]))
        orders, skipped = Portfolio(2).select([intent("N", (2,))], held, leaving={"H2"})
        self.assertEqual([o[0].symbol for o in orders], ["N"])

    def test_one_share_too_expensive_and_determinism(self):
        orders, skipped = Portfolio(3).select([intent("BIG", (1,), close=5000.0)], [])
        self.assertEqual((orders, skipped), ([], [("BIG", "ONE_SHARE_TOO_EXPENSIVE")]))
        a = Portfolio(2).select([intent("X", (1, "X")), intent("Y", (1, "Y"))], [])
        b = Portfolio(2).select([intent("Y", (1, "Y")), intent("X", (1, "X"))], [])
        self.assertEqual([o[0].symbol for o in a[0]], [o[0].symbol for o in b[0]])  # the symbol in the rank key makes ties deterministic


class SimBrokerTests(unittest.TestCase):
    def test_buy_fills_with_slippage_and_stop_is_relative_to_the_signal_close(self):
        b = SimBroker(slip=0.001)
        b.buy(Intent("t", "AAA", 1000.0, 0.9, (1,), 100.0), 9, "2026-01-05", 101.0)
        p = b.positions[0]
        self.assertAlmostEqual(p.entry_price, 101.101)
        self.assertEqual(p.stop_price, 90.0)

    def test_stop_fills_at_the_stop_or_at_a_lower_open(self):
        b = SimBroker(slip=0.0)
        b.buy(Intent("t", "AAA", 1000.0, 0.9, (1,), 100.0), 9, "2026-01-05", 100.0)
        b.check_stops("2026-01-05", lambda s: (99.0, 100.0, 89.0, 95.0))
        self.assertEqual((b.trades[0].how, b.trades[0].exit_price), ("STOP", 90.0))
        b.buy(Intent("t", "BBB", 1000.0, 0.9, (1,), 100.0), 9, "2026-01-06", 100.0)
        b.check_stops("2026-01-06", lambda s: (85.0, 86.0, 80.0, 82.0))
        self.assertEqual(b.trades[1].exit_price, 85.0)  # the day opened below the stop

    def test_sell_at_open_pays_slippage(self):
        b = SimBroker(slip=0.001)
        b.buy(Intent("t", "AAA", 1000.0, 0.9, (1,), 100.0), 10, "2026-01-05", 100.0)
        b.sell_at_open(b.positions[0], "2026-01-07", 110.0, "SIGNAL")
        self.assertAlmostEqual(b.trades[0].exit_price, 110.0 * 0.999)
        self.assertGreater(b.trades[0].pnl, 0)


class ParityWithTheLiveTool(unittest.TestCase):
    def test_engine_signals_equal_the_forward_tool_candidates_in_the_same_order(self):
        m = to.market()
        universe_list = ["AAA", "BBB", "CCC"]
        asof = to.ASOF
        live = of.compute_candidates(m, universe_list, asof)
        uni = {asof.year: set(universe_list)}
        stress, inds, idx = backtest.prepare_market(m, uni)
        ctx = Context(m, idx, inds, uni, asof.isoformat(), stress[asof.isoformat()])
        ordered = [i.symbol for i in sorted(OversoldV3().signals(ctx), key=lambda x: x.rank)]
        self.assertEqual(ordered, [c["sym"] for c in live["candidates"]])
        self.assertGreater(len(ordered), 0)

    def test_no_signals_without_stress(self):
        m = to.market(stressed=False)
        uni = {to.ASOF.year: {"AAA", "BBB", "CCC"}}
        stress, inds, idx = backtest.prepare_market(m, uni)
        ctx = Context(m, idx, inds, uni, to.ASOF.isoformat(), stress[to.ASOF.isoformat()])
        self.assertEqual(OversoldV3().signals(ctx), [])


class BacktestMechanics(unittest.TestCase):
    def build(self, future):
        m = to.market()
        # extend every series by the `future` bars for the crashing name AAA, flat for the others, SPY stays stressed
        base = m["AAA"]
        last = base["date"][-1]
        d0 = date.fromisoformat(last)
        for k, (o, h, l, c) in enumerate(future, start=1):
            dt = (d0 + timedelta(days=k)).isoformat()
            for s, d in m.items():
                if s == "AAA":
                    d["date"].append(dt); d["o"].append(o); d["h"].append(h); d["l"].append(l); d["c"].append(c); d["v"].append(1e6)
                else:
                    px = d["c"][-1]
                    d["date"].append(dt); d["o"].append(px); d["h"].append(px * 1.01); d["l"].append(px * 0.99); d["c"].append(px); d["v"].append(1e6)
        return m

    def test_signal_close_then_next_open_entry_then_signal_exit_at_the_following_open(self):
        crash_close = to.market()["AAA"]["c"][-1]
        future = [(crash_close * 1.00, crash_close * 1.02, crash_close * 0.99, crash_close * 1.01),   # entry day: opens at the crash close
                  (crash_close * 1.05, crash_close * 1.30, crash_close * 1.04, crash_close * 1.25),   # close far above SMA5 -> exit queued
                  (crash_close * 1.26, crash_close * 1.27, crash_close * 1.25, crash_close * 1.26)]   # exit fills at this open
        m = self.build(future)
        uni = {to.ASOF.year: {"AAA", "BBB", "CCC"}}
        res = backtest.run(OversoldV3(), Portfolio(10), m, uni, start=to.ASOF.isoformat(), slip=0.0)
        tr = [t for t in res["trades"] if t.symbol == "AAA"]
        self.assertEqual(len(tr), 1)
        t = tr[0]
        d0 = to.ASOF
        self.assertEqual((t.entry_date, t.exit_date, t.how), ((d0 + timedelta(days=1)).isoformat(), (d0 + timedelta(days=3)).isoformat(), "SIGNAL"))
        self.assertAlmostEqual(t.entry_price, crash_close * 1.00)
        self.assertAlmostEqual(t.exit_price, crash_close * 1.26)

    def test_a_stop_day_exits_at_the_stop_and_summary_counts_it(self):
        crash_close = to.market()["AAA"]["c"][-1]
        future = [(crash_close, crash_close * 1.01, crash_close * 0.80, crash_close * 0.82)]  # falls through the 90 percent stop on the entry day
        m = self.build(future)
        uni = {to.ASOF.year: {"AAA", "BBB", "CCC"}}
        res = backtest.run(OversoldV3(), Portfolio(10), m, uni, start=to.ASOF.isoformat(), slip=0.0)
        t = [x for x in res["trades"] if x.symbol == "AAA"][0]
        self.assertEqual(t.how, "STOP")
        self.assertAlmostEqual(t.exit_price, round(crash_close * 0.9, 2))
        s = backtest.summarize(res)
        self.assertGreaterEqual(s["stop_share"], 0.5)

    def test_summary_windows_and_year_totals(self):
        s = backtest.summarize({"trades": [], "daily_pnl": {}})
        self.assertEqual(s["trades"], 0)


if __name__ == "__main__":
    unittest.main()
