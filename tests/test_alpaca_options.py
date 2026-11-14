import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from consumer import alpaca_options as ao
from consumer import alpaca_paper as ap

TODAY = date(2026, 11, 16)
EXP = date(2026, 12, 18)


def q(bid, ask, delta=0.5):
    return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 4), "delta": delta, "iv": 0.3}


class Fake:
    def __init__(self, quotes=None, positions=(), orders=(), closed=(), equity="100000", is_open=True):
        self.quotes, self.positions, self.orders, self.closed = quotes or {}, list(positions), list(orders), list(closed)
        self.equity, self.is_open, self.calls = equity, is_open, []

    def __call__(self, method, path, body=None, host="paper"):
        self.calls.append((method, path, body))
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
        if path.startswith("/v1beta1/options/snapshots"):
            syms = path.split("symbols=")[1].replace("%2C", ",").split("&")[0].split(",")
            return {"snapshots": {s: {"latestQuote": {"bp": self.quotes[s][0], "ap": self.quotes[s][1]}, "greeks": {"delta": 0.5}, "impliedVolatility": 0.3}
                                  for s in syms if s in self.quotes}}
        if method == "POST" and path == "/v2/orders":
            return {"id": "o-1", "status": "accepted"}
        raise AssertionError(f"unexpected call {method} {path}")


def qfn(table):
    return lambda syms: {s: q(*table[s]) for s in syms if s in table}


C100, P100, C110 = ao.occ("SPY", EXP, "C", 740), ao.occ("SPY", EXP, "P", 740), ao.occ("SPY", EXP, "C", 760)


class SymbolTests(unittest.TestCase):
    def test_occ_roundtrip_and_dte(self):
        s = ao.occ("SPY", EXP, "C", 740.5)
        self.assertEqual(s, "SPY261218C00740500")
        self.assertEqual(ao.parse_occ(s), ("SPY", EXP, "C", 740.5))
        self.assertEqual(ao.dte(s, TODAY), 32)
        with self.assertRaises(ap.GuardError):
            ao.parse_occ("SPY")


class PlanTests(unittest.TestCase):
    def plan(self, structure, strikes, table, **kw):
        return ao.build_plan(structure, "SPY", kw.pop("expiry", EXP), strikes, kw.pop("qty", 1), qfn(table), TODAY, **kw)

    def test_long_call_limit_inside_the_quote_and_max_loss_is_the_premium(self):
        p = self.plan("long-call", [740], {C100: (8.8, 9.2)})
        self.assertEqual(p["legs"][0]["limit"], 9.1)  # mid 9.0 + 25 percent of the 0.4 spread
        self.assertEqual((p["max_loss_usd"], p["premium_usd"]), (910.0, 910.0))

    def test_premium_over_the_cap_is_refused(self):
        with self.assertRaises(ap.GuardError) as cm:
            self.plan("long-call", [740], {C100: (11.8, 12.2)})
        self.assertIn("maximum loss", str(cm.exception))
        with self.assertRaises(ap.GuardError):  # two cheap contracts together still count against the cap
            self.plan("long-call", [740], {C100: (5.8, 6.2)}, qty=2)

    def test_straddle_strangle_and_vertical_costs(self):
        s = self.plan("straddle", [740], {C100: (4.9, 5.1), P100: (3.9, 4.1)})
        self.assertAlmostEqual(s["max_loss_usd"], (5.05 + 4.05) * 100)
        v = self.plan("debit-vertical", [740, 760], {C100: (4.9, 5.1), C110: (1.9, 2.1)})
        self.assertEqual([lg["side"] for lg in v["legs"]], ["buy", "sell"])
        self.assertAlmostEqual(v["max_profit_usd"], (20 - v["net_limit"]) * 100)
        put_v = self.plan("debit-vertical", [760, 740], {P100: (1.9, 2.1), ao.occ("SPY", EXP, "P", 760): (5.9, 6.1)})
        self.assertTrue(all(lg["symbol"].startswith("SPY261218P") for lg in put_v["legs"]))
        with self.assertRaises(ap.GuardError):
            self.plan("strangle", [760, 740], {C100: (1, 1.1), P100: (1, 1.1)})  # put strike must be below the call strike

    def test_wide_spread_missing_quote_and_dte_are_refused(self):
        with self.assertRaises(ap.GuardError):
            self.plan("long-call", [740], {C100: (1.0, 2.0)})
        with self.assertRaises(ap.GuardError):
            self.plan("long-call", [740], {})
        with self.assertRaises(ap.GuardError):
            self.plan("long-call", [740], {C100: (4.9, 5.1)}, expiry=date(2026, 11, 20))   # 4 days
        with self.assertRaises(ap.GuardError):
            self.plan("long-call", [740], {C100: (4.9, 5.1)}, expiry=date(2027, 3, 19))    # far out
        for bad in (0, 101, 1.5):
            with self.assertRaises(ap.GuardError):
                ao.build_plan("long-call", "SPY", EXP, [740], bad, qfn({C100: (4.9, 5.1)}), TODAY)
        with self.assertRaises(ap.GuardError):
            ao.build_plan("long-call", "BRK.B", EXP, [740], 1, qfn({C100: (4.9, 5.1)}), TODAY)
        with self.assertRaises(ap.GuardError):
            ao.build_plan("naked-call", "SPY", EXP, [740], 1, qfn({C100: (4.9, 5.1)}), TODAY)

    def test_covered_call_needs_shares_and_csp_needs_cash_room(self):
        with self.assertRaises(ap.GuardError):
            self.plan("covered-call", [760], {C110: (1.9, 2.1)}, shares_held=0)
        cc = self.plan("covered-call", [760], {C110: (1.9, 2.1)}, shares_held=100)
        self.assertLess(cc["net_limit"], 0)
        self.assertIn("credit_usd", cc)
        with self.assertRaises(ap.GuardError):  # 740 x 100 = 74,000 is over 25 percent of 100,000
            self.plan("cash-secured-put", [740], {P100: (3.9, 4.1)}, equity=100000.0)
        ok = ao.build_plan("cash-secured-put", "SPY", EXP, [200], 1, qfn({ao.occ("SPY", EXP, "P", 200): (2.9, 3.1)}), TODAY, equity=100000.0)
        self.assertEqual(ok["capital_usd"], 20000.0)


class OrderTests(unittest.TestCase):
    def test_single_and_multileg_bodies(self):
        p = ao.build_plan("straddle", "SPY", EXP, [740], 2, qfn({C100: (2.4, 2.6), P100: (1.9, 2.1)}), TODAY)
        b = ao.order_body(p, "t-1")
        self.assertEqual((b["order_class"], b["qty"], b["time_in_force"], b["type"]), ("mleg", "2", "day", "limit"))
        self.assertEqual([lg["position_intent"] for lg in b["legs"]], ["buy_to_open", "buy_to_open"])
        self.assertGreater(float(b["limit_price"]), 0)
        s = ao.order_body(ao.build_plan("long-call", "SPY", EXP, [740], 1, qfn({C100: (4.9, 5.1)}), TODAY), "t-2")
        self.assertNotIn("order_class", s)
        self.assertEqual((s["side"], s["position_intent"], s["time_in_force"]), ("buy", "buy_to_open", "day"))

    def test_tag_is_short_and_prefixed_with_the_structure(self):
        tag = ao.new_tag("cash-secured-put", "SPY", TODAY)
        self.assertTrue(tag.startswith("cash-secured-put-SPY-261116-"))
        self.assertLessEqual(len(tag), 48)


class DecideTests(unittest.TestCase):
    def pos(self, sym, qty, avg):
        return {"symbol": sym, "qty": str(qty), "avg_entry_price": str(avg)}

    def test_debit_stop_target_and_expiry(self):
        g = [self.pos(C100, 1, 10.0)]
        self.assertEqual(ao.decide(g, {C100: q(4.9, 5.1)}, TODAY)["action"], "CLOSE")      # -50 percent
        self.assertEqual(ao.decide(g, {C100: q(19.9, 20.1)}, TODAY)["action"], "CLOSE")    # +100 percent
        self.assertEqual(ao.decide(g, {C100: q(11.9, 12.1)}, TODAY)["action"], "HOLD")
        self.assertEqual(ao.decide(g, {C100: q(11.9, 12.1)}, date(2026, 12, 15))["action"], "CLOSE")  # 3 days left
        self.assertEqual(ao.decide(g, {}, TODAY)["action"], "HOLD")

    def test_straddle_is_judged_as_one_structure(self):
        g = [self.pos(C100, 1, 5.0), self.pos(P100, 1, 4.0)]
        self.assertEqual(ao.decide(g, {C100: q(8.9, 9.1), P100: q(0.9, 1.1)}, TODAY)["action"], "HOLD")  # +11 percent combined
        self.assertEqual(ao.decide(g, {C100: q(1.4, 1.6), P100: q(2.4, 2.6)}, TODAY)["action"], "CLOSE")  # 4.0 vs 9.0: -56 percent

    def test_short_option_rules(self):
        g = [self.pos(C110, -1, 2.0)]
        self.assertEqual(ao.decide(g, {C110: q(4.9, 5.1)}, TODAY)["action"], "CLOSE")     # 5.0 >= 2.5 x 2.0
        self.assertEqual(ao.decide(g, {C110: q(0.3, 0.5)}, TODAY)["action"], "CLOSE")     # kept 80 percent
        self.assertEqual(ao.decide(g, {C110: q(1.9, 2.1)}, TODAY)["action"], "HOLD")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)

    def run_cli(self, fake, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ao.main(["--state-dir", str(self.state), *argv], call=fake, today=TODAY)
        return code, json.loads(buf.getvalue())

    def args(self, *extra):
        return ["open", "long-call", "SPY", "--expiry", EXP.isoformat(), "--strikes", "740", *extra]

    def test_dry_run_sends_nothing_and_submit_sends_one_limit_order(self):
        fake = Fake(quotes={C100: (4.9, 5.1)})
        code, out = self.run_cli(fake, *self.args())
        self.assertEqual((code, out["submit"]), (0, False))
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])
        code, out = self.run_cli(fake, *self.args("--submit"))
        self.assertEqual((code, out["order_id"]), (0, "o-1"))
        posts = [c for c in fake.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0][2]["type"], posts[0][2]["time_in_force"]), ("limit", "day"))
        self.assertIn("o-1", (self.state / "orders.jsonl").read_text())

    def test_closed_market_refuses_to_send(self):
        fake = Fake(quotes={C100: (4.9, 5.1)}, is_open=False)
        code, out = self.run_cli(fake, *self.args("--submit"))
        self.assertEqual(code, 2)
        self.assertIn("closed", out["error"])
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_halt_blocks_open_and_manage(self):
        (self.state / "HALT").write_text("x")
        fake = Fake(quotes={C100: (4.9, 5.1)})
        self.assertEqual(self.run_cli(fake, *self.args("--submit"))[0], 2)
        self.assertEqual(self.run_cli(fake, "manage", "--submit")[0], 2)

    def test_structure_count_premium_and_duplicate_leg_limits(self):
        open_orders = [{"client_order_id": f"long-call-X-{i}", "symbol": ao.occ("AAA", EXP, "C", 10 + i)} for i in range(6)]
        code, out = self.run_cli(Fake(quotes={C100: (4.9, 5.1)}, orders=open_orders), *self.args())
        self.assertEqual(code, 2)
        self.assertIn("structures are already open", out["error"])
        held = [{"symbol": ao.occ("AAA", EXP, "C", 50), "qty": "1", "cost_basis": "4600", "avg_entry_price": "46", "market_value": "4600"}]
        code, out = self.run_cli(Fake(quotes={C100: (4.9, 5.1)}, positions=held), *self.args())
        self.assertEqual(code, 2)
        self.assertIn("premium at risk", out["error"])
        dup = [{"symbol": C100, "qty": "1", "cost_basis": "500", "avg_entry_price": "5", "market_value": "500"}]
        self.assertEqual(self.run_cli(Fake(quotes={C100: (4.9, 5.1)}, positions=dup), *self.args())[0], 2)

    def test_manage_closes_a_losing_structure_with_two_single_leg_limit_orders(self):
        pos = [{"symbol": C100, "qty": "1", "cost_basis": "500", "avg_entry_price": "5", "market_value": "100"},
               {"symbol": P100, "qty": "1", "cost_basis": "400", "avg_entry_price": "4", "market_value": "100"}]
        closed = [{"client_order_id": "straddle-SPY-261116-ab12", "filled_qty": "1", "legs": [{"symbol": C100, "filled_qty": "1"}, {"symbol": P100, "filled_qty": "1"}]}]
        fake = Fake(quotes={C100: (1.4, 1.6), P100: (1.9, 2.1)}, positions=pos, closed=closed)
        code, out = self.run_cli(fake, "manage", "--submit")
        self.assertEqual(code, 0)
        s = out["structures"][0]
        self.assertEqual((s["tag"], s["action"]), ("straddle-SPY-261116-ab12", "CLOSE"))
        posts = [c[2] for c in fake.calls if c[0] == "POST"]
        self.assertEqual(sorted(b["position_intent"] for b in posts), ["sell_to_close", "sell_to_close"])
        self.assertTrue(all(b["type"] == "limit" for b in posts))

    def test_manage_dry_run_and_hold(self):
        pos = [{"symbol": C100, "qty": "1", "cost_basis": "500", "avg_entry_price": "5", "market_value": "600"}]
        fake = Fake(quotes={C100: (5.9, 6.1)}, positions=pos)
        code, out = self.run_cli(fake, "manage")
        self.assertEqual(out["structures"][0]["action"], "HOLD")
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])


if __name__ == "__main__":
    unittest.main()
