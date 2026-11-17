import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from consumer import alpaca_options as ao
from consumer import s042_options as so
from engine import recon

TODAY = date(2026, 11, 20)                                       # a Friday, the start date
NOW = datetime(2026, 11, 20, 15, 5, tzinfo=timezone.utc)         # 10:05 ET, inside the window
EXP = date(2026, 12, 28)                                         # 38 days out


def contract(exp, strike, kind="call"):
    return {"symbol": ao.occ("AAA", exp, "C", strike), "type": kind, "expiration_date": exp.isoformat(), "strike_price": str(strike), "tradable": True}


CONTRACTS = [contract(EXP, k) for k in (20, 21, 22, 23, 24)] + [contract(date(2026, 12, 18), k) for k in (20, 22)]


class Fake:
    def __init__(self, quotes=None, positions=(), orders=(), closed=(), spot=20.5, contracts=CONTRACTS, is_open=True, equity="100000"):
        self.quotes, self.positions, self.orders, self.closed = quotes or {}, list(positions), list(orders), list(closed)
        self.spot, self.contracts, self.is_open, self.equity, self.posted = spot, contracts, is_open, equity, []

    def __call__(self, method, path, body=None, host="paper"):
        if path == "/v2/account":
            return {"equity": self.equity}
        if path == "/v2/positions":
            return self.positions
        if path.startswith("/v2/orders?status=open"):
            return self.orders
        if path.startswith("/v2/orders?status=closed"):
            return self.closed
        if path == "/v2/clock":
            return {"is_open": self.is_open}
        if "/trades/latest" in path:
            return {"trade": {"p": self.spot}}
        if path.startswith("/v2/options/contracts"):
            return {"option_contracts": self.contracts}
        if path.startswith("/v1beta1/options/snapshots"):
            syms = path.split("symbols=")[1].replace("%2C", ",").split("&")[0].split(",")
            return {"snapshots": {s: {"latestQuote": {"bp": self.quotes[s][0], "ap": self.quotes[s][1]}} for s in syms if s in self.quotes}}
        if method == "POST":
            self.posted.append(body)
            return {"id": "o-%d" % len(self.posted), "status": "accepted"}
        raise AssertionError(f"unexpected {method} {path}")


LONG, SHORT = ao.occ("AAA", EXP, "C", 21), ao.occ("AAA", EXP, "C", 22)   # spot 20.5: first strike >= 20.5 is 21; first >= 21.73 is 22
PLAN = {"entries": [{"symbol": "AAA"}]}


class PickTests(unittest.TestCase):
    def test_nearest_expiry_to_37_days_first_strike_at_or_above_spot_and_1_06(self):
        self.assertEqual(so.pick_strikes(CONTRACTS, 20.5, TODAY), (EXP, 21.0, 22.0))

    def test_no_pair_when_the_strike_grid_is_too_coarse(self):
        self.assertIsNone(so.pick_strikes([contract(EXP, 21), contract(EXP, 22)], 22.5, TODAY))
        self.assertIsNone(so.pick_strikes([contract(date(2026, 12, 4), 21)], 20.5, TODAY))   # 14 days is outside 30 to 45

    def test_sessions_held_counts_weekdays(self):
        self.assertEqual(so.sessions_held(date(2026, 11, 20), date(2026, 11, 27)), 5)
        self.assertEqual(so.sessions_held(date(2026, 11, 20), date(2026, 11, 23)), 1)


class RunTests(unittest.TestCase):
    def run_it(self, fake, plan=PLAN, submit=True, now=NOW):
        return so.run(fake, plan, now, Path(tempfile.mkdtemp()), submit)

    def test_opens_one_spread_sized_to_the_budget_with_a_tagged_multileg_order(self):
        fake = Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)})
        out = self.run_it(fake)
        self.assertEqual((out["opened"], out["skipped"]), (1, {}))
        body = fake.posted[0]
        self.assertEqual(body["order_class"], "mleg")
        self.assertEqual(body["client_order_id"], "debit-vertical-o42-AAA-261120")
        debit = float(body["limit_price"]) * 100 * int(body["qty"])
        self.assertLessEqual(debit, so.MAX_RISK_USD)
        self.assertGreater(int(body["qty"]), 1)

    def test_too_expensive_wide_quote_and_missing_listing_are_skipped_with_reasons(self):
        big = [contract(EXP, k) for k in range(195, 216)]
        l2, s2 = ao.occ("AAA", EXP, "C", 200), ao.occ("AAA", EXP, "C", 212)
        self.assertEqual(self.run_it(Fake(quotes={l2: (9.9, 10.0), s2: (4.9, 5.0)}, contracts=big, spot=200.0))["skipped"], {"TOO_EXPENSIVE": 1})
        self.assertEqual(self.run_it(Fake(quotes={LONG: (0.5, 1.5), SHORT: (0.45, 0.5)}))["skipped"], {"WIDE_OR_NO_QUOTE": 1})
        self.assertEqual(self.run_it(Fake(quotes={}, contracts=[]))["skipped"], {"NO_LISTED_SPREAD": 1})

    def test_dry_run_sends_nothing(self):
        fake = Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)})
        out = self.run_it(fake, submit=False)
        self.assertEqual((out["opened"], fake.posted), (1, []))

    def test_inactive_before_the_start_date_outside_the_window_and_when_closed(self):
        q = {LONG: (1.0, 1.1), SHORT: (0.45, 0.5)}
        early = self.run_it(Fake(quotes=q), now=datetime(2026, 11, 19, 15, 5, tzinfo=timezone.utc))
        self.assertEqual(early["opened"], 0)
        self.assertIn("starts", early["note"])
        late = self.run_it(Fake(quotes=q), now=datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc))
        self.assertIn("window", late["note"])
        with self.assertRaises(so.GuardError):
            self.run_it(Fake(quotes=q, is_open=False))

    def test_duplicate_underlying_and_open_cap(self):
        have = [{"symbol": LONG, "qty": "2", "avg_entry_price": "1.0", "unrealized_pl": "0"}, {"symbol": SHORT, "qty": "-2", "avg_entry_price": "0.4", "unrealized_pl": "0"}]
        closed = [{"client_order_id": "debit-vertical-o42-AAA-261119", "filled_qty": "2", "legs": [{"symbol": LONG, "filled_qty": "2"}, {"symbol": SHORT, "filled_qty": "2"}]}]
        out = self.run_it(Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)}, positions=have, closed=closed))
        self.assertEqual(out["skipped"], {"ALREADY_HAVE_ONE": 1})
        orders = [{"client_order_id": f"debit-vertical-o42-X{i}-261119", "symbol": None, "legs": [{"symbol": ao.occ("X", EXP, "C", 10 + i)}]} for i in range(so.MAX_OPEN)]
        out = self.run_it(Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)}, orders=orders))
        self.assertEqual(out["skipped"], {"AT_MAX_OPEN": 1})

    def test_halt_line_blocks_new_spreads(self):
        have = [{"symbol": LONG, "qty": "2", "avg_entry_price": "1.0", "unrealized_pl": "-1600"}, {"symbol": SHORT, "qty": "-2", "avg_entry_price": "0.4", "unrealized_pl": "0"}]
        closed = [{"client_order_id": "debit-vertical-o42-AAA-261119", "filled_qty": "2", "legs": [{"symbol": LONG, "filled_qty": "2"}, {"symbol": SHORT, "filled_qty": "2"}]}]
        out = self.run_it(Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)}, positions=have, closed=closed, ), plan={"entries": [{"symbol": "BBB"}]})
        self.assertTrue(out["halted"])
        self.assertEqual(out["opened"], 0)

    def test_log_counts_have_no_names(self):
        out = self.run_it(Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)}))
        self.assertNotIn("AAA", json.dumps(so.counts(out)))


class ManageTests(unittest.TestCase):
    def have(self, long_px="1.0", short_px="0.4"):
        return [{"symbol": LONG, "qty": "2", "avg_entry_price": long_px, "unrealized_pl": "0"}, {"symbol": SHORT, "qty": "-2", "avg_entry_price": short_px, "unrealized_pl": "0"}]

    def closed(self, day="261120"):
        return [{"client_order_id": f"debit-vertical-o42-AAA-{day}", "filled_qty": "2", "legs": [{"symbol": LONG, "filled_qty": "2"}, {"symbol": SHORT, "filled_qty": "2"}]}]

    def go(self, fake, now):
        return so.manage(fake, now, Path(tempfile.mkdtemp()), True)

    def test_holds_inside_the_rules(self):
        fake = Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)}, positions=self.have(), closed=self.closed("261123"))
        out = self.go(fake, datetime(2026, 11, 24, 15, 5, tzinfo=timezone.utc))
        self.assertEqual((out["closing"], fake.posted), (0, []))

    def test_time_stop_after_five_trading_days_closes_both_legs_with_unique_ids(self):
        fake = Fake(quotes={LONG: (1.0, 1.1), SHORT: (0.45, 0.5)}, positions=self.have(), closed=self.closed("261120"))
        out = self.go(fake, datetime(2026, 11, 27, 15, 5, tzinfo=timezone.utc))
        self.assertEqual(out["closing"], 1)
        self.assertEqual({b["side"] for b in fake.posted}, {"sell", "buy"})
        self.assertEqual({b["client_order_id"] for b in fake.posted}, {"close-o42-AAA-261120-L-1127", "close-o42-AAA-261120-S-1127"})

    def test_lost_half_the_premium_closes_early(self):
        fake = Fake(quotes={LONG: (0.5, 0.55), SHORT: (0.25, 0.28)}, positions=self.have(), closed=self.closed("261124"))
        out = self.go(fake, datetime(2026, 11, 25, 15, 5, tzinfo=timezone.utc))
        self.assertEqual(out["closing"], 1)

    def test_does_not_touch_other_structures(self):
        other = [{"symbol": ao.occ("ZZZ", EXP, "C", 10), "qty": "1", "avg_entry_price": "1", "unrealized_pl": "0"}]
        closed = [{"client_order_id": "long-call-ZZZ-261101-ab12", "filled_qty": "1", "legs": [{"symbol": other[0]["symbol"], "filled_qty": "1"}]}]
        fake = Fake(positions=other, closed=closed)
        self.assertEqual(self.go(fake, datetime(2026, 11, 27, 15, 5, tzinfo=timezone.utc))["open"], 0)


class ReportTests(unittest.TestCase):
    def test_closed_spread_result_comes_from_the_broker_fills(self):
        closed = [
            {"client_order_id": "debit-vertical-o42-AAA-261120", "filled_qty": "2", "filled_avg_price": "0.60", "limit_price": "0.62"},
            {"client_order_id": "close-o42-AAA-261120-L-1127", "filled_qty": "2", "filled_avg_price": "1.30", "side": "sell"},
            {"client_order_id": "close-o42-AAA-261120-S-1127", "filled_qty": "2", "filled_avg_price": "0.50", "side": "buy"},
        ]
        rep = so.report(Fake(closed=closed))
        self.assertEqual(rep["closed_spreads"], 1)
        self.assertEqual(rep["closed_pl_usd"], 40.0)             # (1.30 - 0.50 - 0.60) * 100 * 2
        self.assertEqual(rep["wins"], 1)
        self.assertEqual(rep["mean_fill_vs_limit_usd"], 4.0)


class ReconIgnoresOptionsTests(unittest.TestCase):
    def test_an_option_position_is_not_an_unexplained_stock_position(self):
        res = recon.reconcile([{"symbol": LONG, "qty": "2"}], [], NOW)
        self.assertEqual(res["status"], "OK")


if __name__ == "__main__":
    unittest.main()
