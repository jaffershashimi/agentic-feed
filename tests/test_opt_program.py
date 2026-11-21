"""Options program: structure math, selection and filter, ledger chain and checkpoint, catalysts, the engine's caps and halts and approval gate, the account check, and the report."""
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from consumer.opt_program import broker as B, catalysts as C, engine as E, ledger as L, market as M, policy as P, report as R, select as sel, specs as SP, structures as S

EXP = date(2027, 1, 8)            # a Friday, 46 days after the test day
DAY = date(2026, 11, 23)          # a Monday, the program's first session
NOW = datetime(2026, 11, 23, 15, 47, tzinfo=timezone.utc)   # 10:47 ET


def occ(cp, k, e=EXP):
    return f"SPY{e.strftime('%y%m%d')}{cp}{int(k * 1000):08d}"


def leg(side, cp, k, bid, ask, ratio=1, delta=None, e=EXP, oi=None):
    return S.Leg(occ(cp, k, e), side, ratio, cp, float(k), e, bid, ask, delta, oi)


class FakeMarket:
    spot_px = 690.0

    def __init__(self, drop=0.0):
        self.drop = drop  # a price shock applied to put values, to force stops

    def spot(self, root):
        return self.spot_px

    def daily_closes(self, root, today, n=80):
        return [690.0 + (i % 5) * 0.5 for i in range(80)]

    def contracts(self, root, cp, lo, hi, spot, band=0.2):
        t = "call" if cp == "C" else "put"
        return [{"symbol": occ(cp, k), "type": t, "strike_price": str(k), "expiration_date": EXP.isoformat(), "tradable": True, "open_interest": "500"} for k in range(600, 761)]

    def quotes(self, symbols):
        out = {}
        for s in symbols:
            cp, k = s[-9], int(s[-8:]) / 1000
            if cp == "P":
                delta = -max(0.01, min(0.99, (k - 560) / (2 * (self.spot_px - 560))))
                mid = 0.002 * (k - 560) ** 2 * (1 + self.drop)
            else:
                delta = max(0.01, min(0.99, (760 - k) / (2 * (760 - self.spot_px))))
                mid = 0.002 * (760 - k) ** 2
            mid = max(mid, 0.1)
            out[s] = {"bid": round(mid - 0.02, 2), "ask": round(mid + 0.02, 2), "mid": mid, "delta": delta, "iv": 0.2}
        return out


class FakeBroker:
    def __init__(self):
        self.orders, self.fill, self.held = [], True, set()

    def positions(self):
        return [{"symbol": s, "asset_class": "us_option"} for s in sorted(self.held)]

    def send_and_wait(self, kind, legs, qty, net_limit, closing, tag, **kw):
        self.orders.append((kind, closing, net_limit, tag))
        if self.fill:
            for lg in legs:
                (self.held.discard if closing else self.held.add)(lg.symbol)
        return {"order_id": "o" + str(len(self.orders)), "status": "filled" if self.fill else "canceled", "filled": self.fill, "broker_net": net_limit if self.fill else None}


def spec(**kw):
    s = {"id": "O-901", "name": "test put credit spread", "family": "credit-spread", "structure": "credit-vertical-put", "underlying_type": "etf", "underlyings": ["SPY"], "dte_min": 38,
         "dte_max": 52, "dte_target": 45, "legs": {"short_delta": 0.30, "width": 5}, "entry": {"always": True},
         "exit": {"profit_target_pct": 0.5, "loss_stop_pct": 0.5, "time_exit_dte": 21, "max_hold_sessions": 25}}
    s.update(kw)
    return s


def approval(s):
    return {s["id"]: {"approved": True, "spec_sha256": SP.sha(s)}}


class Structures(unittest.TestCase):
    def test_credit_vertical_loss_and_profit(self):
        legs = [leg("sell", "P", 640, 3.00, 3.10), leg("buy", "P", 635, 1.50, 1.60)]
        e = S.economics("credit-vertical-put", legs)
        self.assertEqual(e["width"], 5.0)
        # model fill: 75 percent of the way from mid to natural. Sell leg 3.05 mid -> 3.0125; buy leg 1.55 mid -> 1.5875; net credit 1.425
        self.assertAlmostEqual(e["net"], -1.425, 4)
        self.assertAlmostEqual(S.net(legs, "natural"), -1.40, 4)
        self.assertAlmostEqual(S.net(legs, "mid"), -1.50, 4)
        self.assertLess(e["net"], 0)
        self.assertAlmostEqual(e["max_loss_usd"], (5 - (-e["net"])) * 100 + e["fees_round_trip_usd"], 2)
        self.assertAlmostEqual(e["fees_round_trip_usd"], 0.05 * 2 * 2, 2)  # 2 legs x 2 sides x 0.05

    def test_naked_and_miscovered_structures_refused(self):
        with self.assertRaises(S.StructureError):
            S.validate("credit-vertical-put", [leg("sell", "P", 640, 3, 3.1)])
        with self.assertRaises(S.StructureError):  # long leg above the short put: not covering
            S.validate("credit-vertical-put", [leg("sell", "P", 640, 3, 3.1), leg("buy", "P", 645, 5, 5.1)])
        with self.assertRaises(S.StructureError):  # diagonal whose long call is above the short call: loss beyond the debit
            S.validate("diagonal-call", [leg("buy", "C", 700, 3, 3.1, e=date(2027, 2, 19)), leg("sell", "C", 690, 5, 5.1)])

    def test_iron_condor_and_butterfly(self):
        ic = [leg("buy", "P", 620, 0.5, 0.6), leg("sell", "P", 625, 1.0, 1.1), leg("sell", "C", 760, 1.0, 1.1), leg("buy", "C", 765, 0.5, 0.6)]
        e = S.economics("iron-condor", ic)
        self.assertEqual(e["width"], 5.0)
        self.assertLess(e["net"], 0)
        bf = [leg("buy", "C", 685, 8.0, 8.1), leg("sell", "C", 690, 5.0, 5.1, ratio=2), leg("buy", "C", 695, 3.0, 3.1)]
        eb = S.economics("long-butterfly-call", bf)
        self.assertEqual(eb["width"], 10.0)
        self.assertGreater(eb["max_profit_usd"], 0)
        with self.assertRaises(S.StructureError):
            S.validate("long-butterfly-call", [leg("buy", "C", 685, 8, 8.1), leg("sell", "C", 690, 5, 5.1, ratio=2), leg("buy", "C", 700, 3, 3.1)])

    def test_pnl_sign(self):
        legs = [leg("buy", "C", 690, 1.0, 1.0), leg("sell", "C", 695, 0.5, 0.5)]
        self.assertAlmostEqual(S.pnl_usd(0.5, -0.9, 1, legs, fees_sides=0), 40.0)       # debit 0.50, closed for 0.90 credit
        credit = [leg("sell", "P", 640, 1, 1), leg("buy", "P", 635, 0.5, 0.5)]
        self.assertAlmostEqual(S.pnl_usd(-1.5, 0.5, 1, credit, fees_sides=0), 100.0)    # credit 1.50, bought back for 0.50


class Selection(unittest.TestCase):
    def test_expiry_rules(self):
        s = spec()
        self.assertEqual(sel.choose_expiry([EXP], DAY, s), EXP)
        self.assertIsNone(sel.choose_expiry([date(2026, 11, 27)], date(2026, 11, 9), spec(dte_min=9, dte_max=30)))   # Thanksgiving week expiry is blocked
        self.assertIsNone(sel.choose_expiry([date(2026, 11, 26)], date(2026, 11, 9), spec(dte_min=9, dte_max=30)))
        # short legs may not be held into expiration week: time exit 8 days out, but a max hold that runs past it is cut by planned_exit (min of the two)
        late = spec(dte_min=9, dte_max=20, exit={"profit_target_pct": 0.5, "loss_stop_pct": 0.5, "time_exit_dte": 8, "max_hold_sessions": 25})
        self.assertEqual(sel.choose_expiry([date(2026, 12, 11)], date(2026, 11, 30), late), date(2026, 12, 11))

    def test_spec_validation(self):
        with self.assertRaises(SP.SpecError):
            SP.validate(spec(exit={"profit_target_pct": 0.5, "loss_stop_pct": 0.5, "time_exit_dte": 5, "max_hold_sessions": 10}))   # short legs into expiration week
        with self.assertRaises(SP.SpecError):
            SP.validate(spec(entry={"conditions": {"bogus": 1}}))
        SP.validate(spec(structure="long-call", family="long-option", legs={"long_delta": 0.5}, exit={"profit_target_pct": 1.0, "loss_stop_pct": 0.5, "time_exit_dte": 5, "max_hold_sessions": 10}, dte_min=20))

    def test_liquidity_filter(self):
        ok = [leg("sell", "P", 640, 3.00, 3.04), leg("buy", "P", 635, 1.50, 1.54)]
        self.assertEqual(sel.liquidity_ok(ok), (True, ""))
        self.assertEqual(sel.liquidity_ok([leg("sell", "P", 640, 3.00, 3.40)])[1], "LEG_SPREAD_TOO_WIDE")
        self.assertEqual(sel.liquidity_ok([leg("sell", "P", 640, 0.02, 0.04)])[1], "SOLD_LEG_BID_TOO_LOW")
        self.assertEqual(sel.liquidity_ok([leg("buy", "P", 640, 0, 0.04)])[1], "NO_TWO_SIDED_QUOTE")
        self.assertEqual(sel.liquidity_ok([leg("buy", "P", 640, 3.0, 3.04, oi=10)])[1], "OPEN_INTEREST_TOO_LOW")

    def test_size_rule_steps_down_then_fallback_then_refuses(self):
        wide = [("credit-vertical-put", [leg("sell", "P", 640, 3.0, 3.04), leg("buy", "P", 590, 0.4, 0.44)]),   # width 50
                ("credit-vertical-put", [leg("sell", "P", 640, 3.0, 3.04), leg("buy", "P", 635, 1.4, 1.44)])]    # width 5
        k, legs, e, info = sel.pick(wide, DAY)
        self.assertEqual(e["width"], 5.0)
        self.assertFalse(info["used_fallback_cap"])
        big_only = [("credit-vertical-put", [leg("sell", "P", 640, 3.0, 3.04), leg("buy", "P", 625, 1.0, 1.04)])]   # width 15, credit about 2 -> loss about 1300
        k, legs, e, info = sel.pick(big_only, DAY)
        self.assertTrue(info["used_fallback_cap"])
        self.assertLessEqual(e["max_loss_usd"], P.MAX_LOSS_FALLBACK_USD)
        huge = [("credit-vertical-put", [leg("sell", "P", 640, 3.0, 3.04), leg("buy", "P", 620, 1.0, 1.04)])]       # width 20 -> loss about 1800
        self.assertEqual(sel.pick(huge, DAY)[3], "TOO_BIG_EVEN_SMALLEST")


class LedgerTests(unittest.TestCase):
    def test_chain_checkpoint_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            led = L.Ledger(d)
            led.append("RUN", day="2026-11-23")
            led.append("RUN", day="2026-11-24")
            cp = led.write_checkpoint("2026-11-24")
            self.assertEqual(cp["rows"], 2)
            led.append("RUN", day="2026-11-25")
            self.assertEqual(led.verify()["rows"], 3)
            led.write_checkpoint("2026-11-24")                     # same day, more rows: a new file, the old one is kept
            self.assertEqual(len(list((Path(d) / "checkpoints").glob("*.json"))), 2)
            lines = (Path(d) / "ledger.jsonl").read_text().splitlines()
            (Path(d) / "ledger.jsonl").write_text("\n".join([lines[0].replace("2026-11-23", "2026-11-22")] + lines[1:]) + "\n")
            with self.assertRaises(L.LedgerError):
                led.append("RUN", day="x")

    def test_lost_history_refused(self):
        with tempfile.TemporaryDirectory() as d:
            led = L.Ledger(d)
            for i in range(3):
                led.append("RUN", i=i)
            led.write_checkpoint("2026-11-23")
            lines = (Path(d) / "ledger.jsonl").read_text().splitlines()
            (Path(d) / "ledger.jsonl").write_text(lines[0] + "\n")       # two rows lost; the remaining chain is internally valid
            with self.assertRaises(L.LedgerError):
                led.append("RUN", i=9)


class CatalystTests(unittest.TestCase):
    def test_fomc_cpi_and_stock_without_earnings_source(self):
        t = C.tag("etf", date(2026, 12, 1), date(2026, 12, 11))
        kinds = {e["type"] for e in t["events"]}
        self.assertTrue({"fomc_meeting_day1", "fomc_statement", "cpi", "nfp"} <= kinds)
        self.assertTrue(t["catalyst_tagged"])
        self.assertFalse(C.tag("etf", date(2026, 12, 14), date(2026, 12, 18))["catalyst_tagged"] and False)
        self.assertIn("earnings_unknown", {e["type"] for e in C.tag("stock", date(2026, 12, 14), date(2026, 12, 18))["events"]})


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.led = L.Ledger(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def eng(self, specs, approvals, market=None, broker=None, submit=True, now=NOW, **kw):
        return E.Engine(market or FakeMarket(), broker or FakeBroker(), self.led, specs, approvals, now, submit, ignore_window=kw.get("ignore_window", False))

    def test_no_approval_no_trades(self):
        s = spec()
        b = FakeBroker()
        c = self.eng({s["id"]: s}, {}, broker=b).run()
        self.assertEqual(b.orders, [])
        self.assertEqual(c["approved_specs"], 0)
        self.assertEqual(L.reduce(self.led.rows())["open"], {})

    def test_wrong_hash_is_not_approval(self):
        s = spec()
        c = self.eng({s["id"]: s}, {s["id"]: {"approved": True, "spec_sha256": "0" * 64}}).run()
        self.assertEqual(c["approved_specs"], 0)

    def test_dry_run_writes_nothing_and_sends_nothing(self):
        s = spec()
        b = FakeBroker()
        c = self.eng({s["id"]: s}, approval(s), broker=b, submit=False).run()
        self.assertEqual(c["would_open"], 1)
        self.assertEqual(b.orders, [])
        self.assertEqual(self.led.rows(), [])

    def test_entry_freezes_spec_first_and_records_tags_and_costs(self):
        s = spec()
        b = FakeBroker()
        c = self.eng({s["id"]: s}, approval(s), broker=b).run()
        self.assertEqual(c["opened"], 1)
        types = [r["type"] for r in self.led.rows()]
        self.assertLess(types.index("SPEC_FROZEN"), types.index("ENTRY"))
        entry = [r for r in self.led.rows() if r["type"] == "ENTRY"][0]
        self.assertLessEqual(entry["max_loss_usd"], P.MAX_LOSS_USD)
        self.assertIn("catalyst_tagged", entry)
        self.assertIn("smallest_version_risk_usd", entry)
        self.assertLess(entry["entry_net_model"], 0)                       # a credit
        self.assertLess(b.orders[0][2], 0)                                 # signed limit: negative for a credit
        self.assertTrue(self.led.latest_checkpoint())

    def test_one_open_per_underlying_and_three_per_strategy(self):
        s = spec(underlyings=["SPY"])
        b = FakeBroker()
        self.eng({s["id"]: s}, approval(s), broker=b).run()
        c2 = self.eng({s["id"]: s}, approval(s), broker=b).run()
        self.assertEqual(c2["opened"], 0)
        self.assertIn("ALREADY_OPEN_ON_UNDERLYING", c2["skipped"])

    def test_spec_change_after_freeze_is_refused(self):
        s = spec()
        b = FakeBroker()
        self.eng({s["id"]: s}, approval(s), broker=b).run()
        changed = spec(dte_target=44)
        c = self.eng({changed["id"]: changed}, approval(changed), broker=b, now=datetime(2026, 11, 24, 15, 47, tzinfo=timezone.utc)).run()
        self.assertIn("SPEC_CHANGED_AFTER_FREEZE", c["skipped"])
        self.assertEqual(c["opened"], 0)

    def test_open_risk_cap(self):
        s = spec()
        rows = [{"seq": 1, "type": "ENTRY", "id": "x", "spec_id": "O-OTHER", "max_loss_usd": 9800.0}]
        st = L.reduce(rows)
        e = self.eng({s["id"]: s}, approval(s))
        e.open_one(s, "SPY", st, control=False)
        self.assertIn("OPEN_RISK_CAP", e.c["skipped"])

    def test_not_a_session_and_before_program_start(self):
        s = spec()
        thanksgiving = datetime(2026, 11, 26, 15, 47, tzinfo=timezone.utc)
        self.assertEqual(self.eng({s["id"]: s}, approval(s), now=thanksgiving).run()["note"], "not a market session")
        early = datetime(2026, 11, 20, 15, 47, tzinfo=timezone.utc)
        self.assertIn("program starts", self.eng({s["id"]: s}, approval(s), now=early).run()["note"])

    def test_exit_on_profit_target_and_halts(self):
        s = spec()
        b = FakeBroker()
        self.eng({s["id"]: s}, approval(s), broker=b).run()
        later = datetime(2026, 11, 24, 15, 47, tzinfo=timezone.utc)
        m = FakeMarket()
        m.spot_px = 700.0   # puts fall in value: the credit spread gains
        self.eng({s["id"]: s}, approval(s), market=m, broker=b, now=later).run()
        # force a strategy halt: a big loss on an open structure
        with tempfile.TemporaryDirectory() as d2:
            led2 = L.Ledger(d2)
            e = E.Engine(FakeMarket(drop=0.0), FakeBroker(), led2, {s["id"]: s}, approval(s), NOW, True)
            led2.append("SPEC_FROZEN", spec_id=s["id"], spec_sha256=SP.sha(s), spec=s)
            led2.append("ENTRY", id="a", spec_id=s["id"], day="2026-11-23", kind="credit-vertical-put", qty=1, underlying="SPY", max_loss_usd=400.0, max_profit_usd=100.0,
                        entry_net_model=-1.0, entry_net_natural=-0.9, legs=[{"symbol": occ("P", 640), "side": "sell", "ratio": 1, "cp": "P", "strike": 640.0, "expiry": EXP.isoformat()},
                                                                          {"symbol": occ("P", 635), "side": "buy", "ratio": 1, "cp": "P", "strike": 635.0, "expiry": EXP.isoformat()}])
            led2.append("EXIT", id="a", spec_id=s["id"], reason="LOSS_STOP", pnl_model_usd=-1700.0, pnl_natural_usd=-1700.0)
            st = L.reduce(led2.rows())
            e.evaluate_halts(st, e.results(st, {}))
            self.assertTrue(st["halts"][("strategy", s["id"])])
            self.assertFalse(st["halts"].get(("account", None), False))
            led2.append("ENTRY", id="b", spec_id="O-902", day="2026-11-23", kind="credit-vertical-put", qty=1, underlying="QQQ", max_loss_usd=400.0, entry_net_model=-1.0,
                        entry_net_natural=-0.9, legs=[])
            led2.append("EXIT", id="b", spec_id="O-902", reason="LOSS_STOP", pnl_model_usd=-2500.0, pnl_natural_usd=-2500.0)
            st = L.reduce(led2.rows())
            e.evaluate_halts(st, e.results(st, {}))
            self.assertTrue(st["halts"][("account", None)])

    def test_account_halt_blocks_entries_until_resume(self):
        s = spec()
        self.led.append("HALT", scope="account", result_usd=-4100.0)
        c = self.eng({s["id"]: s}, approval(s)).run()
        self.assertEqual(c["opened"], 0)
        self.led.append("RESUME", scope="account", by="owner words")
        self.assertEqual(L.reduce(self.led.rows())["halts"][("account", None)], False)

    def test_reconcile_mismatch_blocks_entries(self):
        s = spec()
        self.eng({s["id"]: s}, approval(s), broker=FakeBroker()).run()
        b = FakeBroker()     # a new broker object with no positions: the account was reset, but the ledger still shows one open
        c = self.eng({s["id"]: s}, approval(s), broker=b, now=datetime(2026, 11, 24, 15, 47, tzinfo=timezone.utc)).run()
        self.assertFalse(c["reconciled"])
        self.assertEqual(c["opened"], 0)

    def test_no_fill_is_recorded_not_counted_as_a_trade(self):
        s = spec()
        b = FakeBroker()
        b.fill = False
        c = self.eng({s["id"]: s}, approval(s), broker=b).run()
        self.assertEqual(c["no_fill"], 1)
        self.assertEqual(L.reduce(self.led.rows())["open"], {})

    def test_unsupported_structure_is_not_approvable(self):
        s = spec(id="O-902", structure="calendar-call", family="calendar", legs={"long_delta": 0.5})
        self.assertEqual(SP.approved({s["id"]: s}, approval(s)), {})


class BrokerTests(unittest.TestCase):
    def test_new_env_maps_names_and_refuses_missing(self):
        e = B.new_env({"OPT_LAB_ALPACA_KEY_ID": "K\nextra", "OPT_LAB_ALPACA_SECRET_KEY": " S "})
        self.assertEqual((e["ALPACA_API_KEY"], e["ALPACA_SECRET_KEY"]), ("K", "S"))
        with self.assertRaises(Exception):
            B.new_env({})

    def test_existing_account_client_is_read_only(self):
        calls = []
        ro = B.read_only(lambda m, p, b=None, host="paper": calls.append((m, p)) or {})
        ro("GET", "/v2/account")
        for method in ("POST", "DELETE"):
            with self.assertRaises(Exception):
                ro(method, "/v2/orders", {})
        self.assertEqual(calls, [("GET", "/v2/account")])

    def test_only_the_owners_secret_names_are_used_to_send(self):
        self.assertEqual((B.NEW_KEY_NAME, B.NEW_SECRET_NAME), ("OPT_LAB_ALPACA_KEY_ID", "OPT_LAB_ALPACA_SECRET_KEY"))
        with self.assertRaises(Exception):
            B.new_env({"ALPACA_API_KEY": "existing", "ALPACA_SECRET_KEY": "existing"})   # the existing names are never a fallback

    def test_account_check_levels_and_distinct(self):
        new = lambda m, p, b=None, host="paper": {"id": "A", "account_number": "1", "status": "ACTIVE", "options_approved_level": 3, "options_trading_level": 3, "options_buying_power": "100"}
        old = lambda m, p, b=None, host="paper": {"id": "B", "account_number": "2"}
        r = B.account_check(new, old)
        self.assertTrue(r["multi_leg_ok"] and r["distinct_from_existing_account"])
        self.assertNotIn("equity", json.dumps(r))
        same = B.account_check(new, new)
        self.assertFalse(same["distinct_from_existing_account"])
        low = lambda m, p, b=None, host="paper": {"id": "A", "account_number": "1", "status": "ACTIVE", "options_approved_level": 2, "options_trading_level": 2}
        self.assertFalse(B.account_check(low)["multi_leg_ok"])

    def test_order_bodies(self):
        legs = [leg("sell", "P", 640, 3, 3.1), leg("buy", "P", 635, 1.5, 1.6)]
        b = B.Broker(lambda *a, **k: {})
        body = b.body("credit-vertical-put", legs, 1, -1.47, False, "tag")
        self.assertEqual(body["order_class"], "mleg")
        self.assertEqual(body["limit_price"], "-1.47")
        self.assertEqual([x["position_intent"] for x in body["legs"]], ["sell_to_open", "buy_to_open"])
        closing = b.body("credit-vertical-put", legs, 1, 0.5, True, "tag")
        self.assertEqual([x["position_intent"] for x in closing["legs"]], ["buy_to_close", "sell_to_close"])
        single = b.body("long-call", [leg("buy", "C", 690, 5, 5.1)], 1, 5.05, False, "t")
        self.assertEqual((single["side"], single["position_intent"]), ("buy", "buy_to_open"))


class ReportTests(unittest.TestCase):
    def trade(self, spec_id, pnl, tagged=False, small=60.0, loss=400.0):
        return {"entry": {"spec_id": spec_id, "max_loss_usd": loss, "catalyst_tagged": tagged, "smallest_version_risk_usd": small}, "exit": {"pnl_model_usd": pnl, "pnl_natural_usd": pnl - 5}}

    def test_unranked_label_until_30_trades_and_stage1(self):
        st = {"specs": {"O-1": {}}, "closed": [self.trade("O-1", 50 if i % 2 else -20, tagged=i % 3 == 0) for i in range(40)], "closed_control": [self.trade("O-1", -10) for _ in range(40)],
              "open": {}, "open_control": {}, "halts": {}, "no_fill": 0}
        rep = R.build(st, {})
        self.assertEqual(rep["strategies"][0]["label"], P.UNRANKED_LABEL)
        rep2 = R.build(st, {"O-1": {"holm_pass": True}})
        self.assertNotEqual(rep2["strategies"][0]["label"], P.UNRANKED_LABEL)

    def test_both_catalyst_groups_shown_and_win_rate_does_not_rank(self):
        st = {"specs": {}, "closed": [self.trade("O-1", 10, tagged=True) for _ in range(6)] + [self.trade("O-1", -30) for _ in range(6)], "closed_control": [], "open": {}, "open_control": {}, "halts": {}, "no_fill": 0}
        s = R.build(st)["strategies"][0]
        self.assertEqual(s["with_catalyst_tag"]["n"], 6)
        self.assertEqual(s["without_catalyst_tag"]["n"], 6)
        self.assertEqual(s["all"]["n"], 12)
        self.assertIn("win_rate_shown_not_ranked", s["all"])

    def test_transfer_flag_for_800_account(self):
        st = {"specs": {}, "closed": [self.trade("O-1", 10, small=200.0) for _ in range(8)], "closed_control": [], "open": {}, "open_control": {}, "halts": {}, "no_fill": 0}
        t = R.build(st)["strategies"][0]["transfer"]
        self.assertIn("CANNOT_TRADE_AT_800", t["flag"])
        st["closed"] = [self.trade("O-1", 10, small=60.0) for _ in range(8)]
        self.assertIsNone(R.build(st)["strategies"][0]["transfer"]["flag"])

    def test_max_drawdown(self):
        self.assertEqual(R.max_drawdown([10, -30, -20, 40]), -50.0)


class SignalTests(unittest.TestCase):
    def test_signals_and_conditions_fail_closed(self):
        closes = [100 + i * 0.1 for i in range(80)]
        sig = M.signals(closes, iv=0.2)
        self.assertTrue(sig["above_sma50"])
        self.assertEqual(M.conditions_met({"above_sma50": True, "rsi14_min": 50}, sig)[0], True)
        self.assertEqual(M.conditions_met({"ret_5d_max": -0.02}, sig), (False, "COND_ret_5d_max"))
        self.assertEqual(M.conditions_met({"iv_rv_min": 1.0}, M.signals(closes[:10]))[1], "NO_SIGNAL_DATA")


class ExitAndControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.led = L.Ledger(self.tmp.name)
        self.s = spec()
        self.b = FakeBroker()
        E.Engine(FakeMarket(), self.b, self.led, {self.s["id"]: self.s}, approval(self.s), NOW, True).run()

    def tearDown(self):
        self.tmp.cleanup()

    def later(self, market, day=date(2026, 11, 24)):
        now = datetime(day.year, day.month, day.day, 15, 47, tzinfo=timezone.utc)
        return E.Engine(market, self.b, self.led, {self.s["id"]: self.s}, approval(self.s), now, True).run()

    def exits(self):
        return [r for r in self.led.rows() if r["type"] == "EXIT"]

    def test_profit_target(self):
        c = self.later(FakeMarket(drop=-0.9))
        ex = self.exits()
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]["reason"], "PROFIT_TARGET")
        self.assertGreater(ex[0]["pnl_model_usd"], 0)
        self.assertEqual(self.b.held, set())                          # closed, and not re-opened in the same pass
        self.assertEqual(c["skipped"].get("TRADED_TODAY"), 1)

    def test_loss_stop(self):
        self.later(FakeMarket(drop=2.0))
        ex = self.exits()
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]["reason"], "LOSS_STOP")
        self.assertLess(ex[0]["pnl_model_usd"], 0)

    def test_time_exit_at_21_dte(self):
        self.later(FakeMarket(), day=date(2026, 12, 18))     # 21 days before the 2027-01-08 expiry
        ex = self.exits()
        self.assertEqual([e["reason"] for e in ex], ["TIME_EXIT_DTE"])
        self.assertLess(date(2026, 12, 18), sel.monday_of_week(EXP))   # closed before the expiration week

    def test_exit_uses_the_worse_of_model_and_broker(self):
        self.assertEqual(E.worse_of(-1.0, -0.8), -0.8)    # received less than modelled: use what was received
        self.assertEqual(E.worse_of(-1.0, -1.2), -1.0)    # received more than modelled: keep the conservative model
        self.assertEqual(E.worse_of(0.5, None), 0.5)


class ControlAndWindowTests(unittest.TestCase):
    def test_controls_are_shadow_trades_outside_caps_and_results(self):
        old = P.CONTROL_DAILY_PROB
        P.CONTROL_DAILY_PROB = 1.0
        try:
            with tempfile.TemporaryDirectory() as d:
                led = L.Ledger(d)
                s = spec()
                b = FakeBroker()
                c = E.Engine(FakeMarket(), b, led, {s["id"]: s}, approval(s), NOW, True).run()
                self.assertEqual(c["controls_opened"], 1)
                self.assertEqual(len(b.orders), 1)                    # the control sent no order
                st = L.reduce(led.rows())
                self.assertEqual(len(st["open"]), 1)
                self.assertEqual(len(st["open_control"]), 1)
                e = E.Engine(FakeMarket(drop=-0.9), b, led, {s["id"]: s}, approval(s), datetime(2026, 11, 24, 15, 47, tzinfo=timezone.utc), True)
                e.run()
                st = L.reduce(led.rows())
                self.assertEqual(len(st["closed"]), 1)
                self.assertEqual(len(st["closed_control"]), 1)
                self.assertEqual(R.build(st)["strategies"][0]["all"]["n"], 1)   # the control is not in the strategy's own result
                self.assertEqual(R.build(st)["strategies"][0]["control"]["n"], 1)
        finally:
            P.CONTROL_DAILY_PROB = old

    def test_entry_window(self):
        et = lambda y, m, d, h, mi: datetime(y, m, d, h + 5, mi, tzinfo=timezone.utc)
        self.assertTrue(E.in_entry_window(et(2026, 11, 23, 10, 47)))
        self.assertFalse(E.in_entry_window(et(2026, 11, 23, 9, 40)))
        self.assertFalse(E.in_entry_window(et(2026, 11, 23, 12, 5)))
        self.assertFalse(E.in_entry_window(et(2026, 11, 27, 11, 40)))     # early close day ends at 11:30
        self.assertFalse(E.in_entry_window(et(2026, 11, 26, 10, 47)))     # Thanksgiving

    def test_entry_condition_blocks_a_trade(self):
        with tempfile.TemporaryDirectory() as d:
            s = spec(entry={"conditions": {"ret_5d_max": -0.5}})
            b = FakeBroker()
            c = E.Engine(FakeMarket(), b, L.Ledger(d), {s["id"]: s}, approval(s), NOW, True).run()
            self.assertEqual(c["opened"], 0)
            self.assertIn("COND_ret_5d_max", c["skipped"])


if __name__ == "__main__":
    unittest.main()
