import unittest

from engine import backtest
from engine.portfolio import Portfolio
from engine.strategies import breakout as bo
from tests import test_oversold_forward as to


class PriorExtremeTests(unittest.TestCase):
    def test_the_window_never_includes_today(self):
        closes = [1, 2, 3, 10, 4, 5]
        self.assertEqual(bo.prior_extreme(closes, 3, max), [None, None, None, 3, 10, 10])
        self.assertEqual(bo.prior_extreme(closes, 3, min), [None, None, None, 1, 2, 3])

    def test_a_value_does_not_change_when_the_future_changes(self):
        a = bo.prior_extreme([5, 6, 7, 8, 9, 10], 3, max)
        b = bo.prior_extreme([5, 6, 7, 8, 9, 1000], 3, max)
        self.assertEqual(a[:5], b[:5])


def trending_market(n=330, breakout_at=300, crash_at=None):
    """SPY drifts up. AAA drifts up, closes at a new high on day `breakout_at`, then rolls over (exit by TRAIL or stop)."""
    spy = [100 + 0.05 * i for i in range(n)]
    aaa = [50 + 0.02 * i for i in range(n)]
    for i in range(breakout_at, n):
        aaa[i] = aaa[breakout_at - 1] * (1.05 + 0.01 * (i - breakout_at)) if (crash_at is None or i < crash_at) else aaa[crash_at - 1] * 0.8
    return {"SPY": to.series(spy), "AAA": to.series(aaa, vol=5e6)}


class BreakoutBacktestTests(unittest.TestCase):
    def run_variant(self, variant, data):
        universe = {y: {"AAA"} for y in range(2024, 2028)}
        return backtest.run(bo.Breakout(variant), Portfolio(max_positions=20), data, universe, start=data["SPY"]["date"][0], slip=0.0)

    def test_a_breakout_enters_once_and_exits_when_the_trend_breaks(self):
        res = self.run_variant("B20", trending_market(crash_at=315))
        tr = res["trades"]
        self.assertTrue(tr)
        self.assertEqual({t.symbol for t in tr}, {"AAA"})
        self.assertIn(tr[0].how, ("TRAIL", "STOP"))

    def test_nothing_trades_when_there_is_no_new_high(self):
        flat = {"SPY": to.series([100 + 0.05 * i for i in range(330)]), "AAA": to.series([50 + 0.02 * i for i in range(330)], vol=5e6)}
        # a smooth uptrend makes a new 20-day closing high every day, so use a flat series instead
        flat["AAA"] = to.series([50.0] * 330, vol=5e6)
        self.assertEqual(self.run_variant("B20", flat)["trades"], [])

    def test_the_market_filter_blocks_entries_when_spy_is_below_its_200_day_average(self):
        data = trending_market()
        data["SPY"] = to.series([200 - 0.3 * i for i in range(330)])   # a falling market
        self.assertEqual(self.run_variant("B20M", data)["trades"], [])
        self.assertTrue(self.run_variant("B20", data)["trades"])        # without the filter the same stock still trades

    def test_the_time_stop_is_the_40th_session(self):
        class Pos:
            symbol, sessions_held = "AAA", 40
        data = trending_market(breakout_at=300)
        s = bo.Breakout("B20")
        from engine.strategy import Context
        idx = {k: {dt: i for i, dt in enumerate(v["date"])} for k, v in data.items()}
        ctx = Context(data, idx, {}, {}, data["AAA"]["date"][329], False)
        self.assertEqual(s.exit_reason(Pos, ctx), "TIME")


if __name__ == "__main__":
    unittest.main()
