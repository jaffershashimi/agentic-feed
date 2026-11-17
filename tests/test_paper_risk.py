import unittest

from consumer import paper_risk as pr


class SizingTests(unittest.TestCase):
    def test_shares_respect_the_loss_and_notional_limits(self):
        # 100 dollar stock with a 10 percent stop risks 10 per share: loss limit gives 250 shares, notional limit gives 100
        self.assertEqual(pr.stock_shares(100.0, 0.90), 100)
        # a 500 dollar stock: notional limit 20, loss limit 2500/50 = 50
        self.assertEqual(pr.stock_shares(500.0, 0.90), 20)
        # a wide 40 percent stop on a 20 dollar stock: loss per share 8 -> 312, notional 500 -> 312
        self.assertEqual(pr.stock_shares(20.0, 0.60), 312)

    def test_bad_inputs_and_unaffordable_share(self):
        for args in ((0, 0.9), (10, 1.0), (10, 0.0)):
            with self.assertRaises(pr.RiskError):
                pr.stock_shares(*args)
        with self.assertRaises(pr.RiskError):
            pr.stock_shares(20000.0, 0.9)

    def test_option_contracts(self):
        self.assertEqual(pr.option_contracts(500.0), 5)
        self.assertEqual(pr.option_contracts(2500.0), 1)
        with self.assertRaises(pr.RiskError):
            pr.option_contracts(2600.0)
        with self.assertRaises(pr.RiskError):
            pr.option_contracts(0)


class GateTests(unittest.TestCase):
    def test_gate_checks_each_limit(self):
        self.assertTrue(pr.check_new_trade(2000, [2000, 2000], 0))
        with self.assertRaises(pr.RiskError):
            pr.check_new_trade(2600, [], 0)  # per trade
        with self.assertRaises(pr.RiskError):
            pr.check_new_trade(2000, [2500, 2500, 1000], 0)  # total open risk 8000
        with self.assertRaises(pr.RiskError):
            pr.check_new_trade(100, [100] * 6, 0)  # open count
        with self.assertRaises(pr.RiskError):
            pr.check_new_trade(100, [], -3000)  # daily halt

    def test_limits_stay_inside_a_sane_fraction_of_a_100k_account(self):
        self.assertLessEqual(pr.MAX_TOTAL_OPEN_RISK, 0.10 * 100_000)
        self.assertLessEqual(pr.DAILY_LOSS_HALT, 0.05 * 100_000)




class DayTradeGateTests(unittest.TestCase):
    from datetime import datetime as _dt, timezone as _tz
    OK = _dt(2026, 10, 19, 14, 15, tzinfo=_tz.utc)  # 10:15 New York, a Monday

    def test_the_pack_reel_trade_passes_and_one_more_share_loss_blocks(self):
        self.assertEqual(pr.check_day_trade(364.18, 358.32, 370.04, 8, self.OK, []), [])
        # a 2,500 loss limit is generous for the reel; tighten to the pack's 50 to prove the arithmetic path
        old = pr.DAY_MAX_LOSS_PER_TRADE
        pr.DAY_MAX_LOSS_PER_TRADE = 50.0
        self.addCleanup(setattr, pr, "DAY_MAX_LOSS_PER_TRADE", old)
        self.assertEqual(pr.check_day_trade(364.18, 358.32, 370.04, 8, self.OK, []), [])
        self.assertTrue(any(r.startswith("loss") for r in pr.check_day_trade(364.18, 358.32, 370.04, 9, self.OK, [])))

    def test_fresh_price_raises_the_risk_base(self):
        r = pr.check_day_trade(364.18, 358.32, 370.04, 8, self.OK, [], fresh_price=366.48)
        self.assertTrue(any(x.startswith("reward:risk") for x in r))

    def test_window_trades_daily_loss_stop_and_short(self):
        early = self._dt(2026, 10, 19, 13, 40, tzinfo=self._tz.utc)   # 09:40 ET
        self.assertTrue(any(x.startswith("window") for x in pr.check_day_trade(100, 99, 102, 10, early, [])))
        weekend = self._dt(2026, 10, 17, 15, 0, tzinfo=self._tz.utc)
        self.assertTrue(any(x.startswith("window") for x in pr.check_day_trade(100, 99, 102, 10, weekend, [])))
        self.assertTrue(any(x.startswith("trades") for x in pr.check_day_trade(100, 99, 102, 10, self.OK, [10, 10, 10])))
        self.assertTrue(any(x.startswith("daily loss") for x in pr.check_day_trade(100, 90, 120, 200, self.OK, [2500, 500])))
        self.assertTrue(any(x.startswith("stop") for x in pr.check_day_trade(100, None, 102, 10, self.OK, [])))
        self.assertTrue(any(x.startswith("stop") for x in pr.check_day_trade(100, 101, 102, 10, self.OK, [])))
        self.assertTrue(any(x.startswith("short") for x in pr.check_day_trade(100, 99, 102, 10, self.OK, [], side="sell")))

    def test_exits_are_never_blocked_by_day_limits(self):
        late = self._dt(2026, 10, 19, 19, 55, tzinfo=self._tz.utc)    # 15:55 ET, after the entry window
        self.assertEqual(pr.check_day_trade(100, 99, 102, 10, late, [10] * 5, side="sell", is_exit=True), [])


if __name__ == "__main__":
    unittest.main()
