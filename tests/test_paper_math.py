import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from consumer import paper_math as pm

# Synthetic numbers only. The $3.35 ask mirrors the stale Oct 23 SPY 760C snapshot used as a math illustration.
D = date(2026, 10, 26)
T0 = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


class FeeTest(unittest.TestCase):
    def test_option_fees_by_component(self):
        buy = pm.option_fees(1, "buy", 3.35, D)
        sell = pm.option_fees(1, "sell", 4.36, D)
        self.assertEqual((buy["regulatory"], buy["sec"], buy["total"]), (Decimal("0.04"), Decimal("0"), Decimal("0.04")))
        # SEC: 436 * 20.60 / 1e6 = 0.00898 -> rounded up to 0.01
        self.assertEqual((sell["regulatory"], sell["sec"], sell["taf"], sell["total"]), (Decimal("0.04"), Decimal("0.01"), Decimal("0"), Decimal("0.05")))

    def test_sec_rounding_and_unknown_schedule(self):
        self.assertEqual(pm.sec_fee(100000, D), Decimal("2.06"))
        self.assertEqual(pm.sec_fee(100001, D), Decimal("2.07"))
        self.assertIsNone(pm.option_fees(1, "buy", 1.0, date(2027, 1, 4)))  # schedule after Dec 31 is not known
        self.assertIsNone(pm.long_option(1, 1.0, 1.3, date(2027, 1, 4), date(2027, 1, 4)))


class CashFlowTest(unittest.TestCase):
    def test_call_and_put_use_ask_then_bid_once(self):
        call = pm.long_option(1, 3.35, 4.36, D, D)
        self.assertAlmostEqual(call["debit"], 335.04)
        self.assertAlmostEqual(call["proceeds"], 435.95)
        self.assertAlmostEqual(call["pnl"], 100.91)
        put = pm.long_option(2, 2.74, 2.10, D, D)
        self.assertAlmostEqual(put["debit"], 548.08)
        self.assertAlmostEqual(put["pnl"], 419.91 - 548.08)
        # A round trip at an unchanged market loses exactly the quoted spread plus fees, once.
        flat = pm.long_option(1, 3.35, 3.34, D, D)
        self.assertAlmostEqual(flat["pnl"], -(1.00 + 0.04 + 0.04 + 0.01))

    def test_stop_risk_versus_full_premium(self):
        stop = pm.round_tick_up(3.35 * 0.85)
        self.assertEqual((stop, pm.round_tick_up(3.35 * 1.30)), (2.85, 4.36))
        risk = pm.stop_risk(1, 3.35, stop, D, slippage=2.0)
        self.assertAlmostEqual(risk, 52.09)
        self.assertGreater(risk, 50)  # fails a $50 planned-risk cap
        self.assertAlmostEqual(pm.max_loss_long_option(1, 3.35, D), 335.04)
        under = pm.stop_risk(1, 2.00, pm.round_tick_up(2.00 * 0.85), D)
        self.assertLess(under, 50)
        self.assertGreater(pm.max_loss_long_option(1, 2.00, D), 50)  # under the cap at the stop, not at full loss
        self.assertGreater(pm.stressed_loss(1, 3.35, 2.85, 0.20, D, slippage=2.0), risk)


class SpreadTest(unittest.TestCase):
    def test_package_values_once(self):
        r = pm.debit_spread(1, 2.00, 0.80, 2.60, 0.92, D, D)
        self.assertEqual(r["status"], "MEASURED")
        self.assertAlmostEqual(r["debit"], 120.09)  # short-leg sale pays the SEC fee
        self.assertAlmostEqual(r["max_loss"], 120.09)
        self.assertAlmostEqual(r["proceeds"], 168 - 0.04 - 0.01 - 0.04)

    def test_unsynchronized_or_missing_legs(self):
        times = (T0, T0 + timedelta(seconds=30), T0, T0)
        self.assertEqual(pm.debit_spread(1, 2.0, 0.8, 2.6, 0.9, D, D, quote_times=times)["status"], "UNFILLABLE")
        self.assertEqual(pm.debit_spread(1, 2.0, None, 2.6, 0.9, D, D)["status"], "UNFILLABLE")
        self.assertEqual(pm.debit_spread(1, 2.0, 0.8, 2.6, 0.9, D, D, quote_times=(T0, None, T0, T0))["status"], "UNFILLABLE")
        self.assertEqual(pm.debit_spread(1, 2.0, 0.8, 2.6, 0.9, date(2027, 2, 1), date(2027, 2, 1))["status"], "UNMEASURED")


class BreakEvenTest(unittest.TestCase):
    def test_thresholds(self):
        self.assertAlmostEqual(pm.break_even_p(30, 15), 1 / 3)
        self.assertAlmostEqual(pm.break_even_p(40, 30), 3 / 7)
        self.assertAlmostEqual(pm.break_even_p(2, 1), 1 / 3)
        self.assertAlmostEqual(pm.break_even_p(98.91, 52.09), 52.09 / 151.0)
        self.assertAlmostEqual(pm.break_even_p(101, 50, cost=2.09), 52.09 / 151)
        self.assertAlmostEqual(pm.expected_value(1 / 3, 30, 15), 0.0)
        with self.assertRaises(ValueError):
            pm.expected_value(1.2, 30, 15)


class QuoteAndIntervalTest(unittest.TestCase):
    def test_fill_rules(self):
        ok = pm.fill_allowed(T0 - timedelta(seconds=20), T0, 3.34, 3.35, "realtime", True)
        self.assertEqual(ok, (True, "ok"))
        cases = {
            "MARKET_CLOSED": (T0, T0, 3.34, 3.35, "realtime", False),
            "missing source quote time": (None, T0, 3.34, 3.35, "realtime", True),
            "quote delay delayed_streaming_900": (T0, T0, 3.34, 3.35, "delayed_streaming_900", True),
            "quote time after fill (look-ahead)": (T0 + timedelta(seconds=1), T0, 3.34, 3.35, "realtime", True),
            "stale quote": (T0 - timedelta(seconds=61), T0, 3.34, 3.35, "realtime", True),
            "missing bid or ask": (T0, T0, None, 3.35, "realtime", True),
            "crossed quote": (T0, T0, 3.40, 3.35, "realtime", True),
        }
        for reason, args in cases.items():
            with self.subTest(reason):
                self.assertEqual(pm.fill_allowed(*args), (False, reason))

    def test_hourly_intervals_stay_unresolved(self):
        stop, target = 2.85, 4.36
        self.assertEqual(pm.classify_interval(3.30, 3.40, stop, target), "OPEN")
        self.assertEqual(pm.classify_interval(3.30, 2.70, stop, target), "UNRESOLVED")  # endpoints only, path unseen
        self.assertEqual(pm.classify_interval(3.30, None, stop, target), "UNRESOLVED")  # missing observation
        self.assertEqual(pm.classify_interval(3.30, 3.40, stop, target, low=2.80, high=4.40), "UNRESOLVED")  # both touched
        self.assertEqual(pm.classify_interval(3.30, 3.00, stop, target, low=2.80, high=3.50), "STOP")
        self.assertEqual(pm.classify_interval(3.30, 4.40, stop, target, low=3.20, high=4.40), "TARGET")


if __name__ == "__main__":
    unittest.main()
