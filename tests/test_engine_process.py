"""Tests for the desk process layer: limits register, pre-trade gate, post-trade cost analysis, reconciliation, risk report, model monitoring, desk pack."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from consumer import alpaca_paper as ap
from consumer import oversold_forward as of
from engine import deskpack, gate, limits, monitor, recon, riskreport, runner, tca
from tests.test_oversold_forward import Fake as BrokerFake, fetch_for, market

MORNING = datetime(2026, 11, 18, 15, 5, tzinfo=timezone.utc)   # 10:05 ET (standard time), a Wednesday
NIGHT = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)
FIXTURE_MORNING = datetime(2026, 11, 16, 15, 5, tzinfo=timezone.utc)   # 10:05 ET the Monday after the fixture's last session (Fri 2026-11-13)
HEALTHY = [{"check": "stops present", "status": "OK", "detail": ""}, {"check": "data fresh", "status": "OK", "detail": ""}, {"check": "news source", "status": "WARN", "detail": ""}]


def good_plan(**over):
    b = {"symbol": "AAA", "qty": "10", "side": "buy", "type": "market", "time_in_force": "day", "order_class": "oto", "stop_loss": {"stop_price": "90.00"}, "client_order_id": "s042v3-AAA-261117"}
    p = {"asof": "2026-11-17", "stress": True, "v3_signals": 2, "slots": 3, "market_open": True, "entries": [b], "exits": [{"sym": "ZZZ", "action": "EXIT", "reason": "SMA5"}],
         "skipped": [], "corporate_actions_error": None}
    p.update(over)
    return p


class LimitsTests(unittest.TestCase):
    def test_register_reads_the_live_constants_and_ids_are_unique(self):
        ids = [r[0] for r in limits.REGISTER]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(limits.get("S042_POSITION_CAP"), of.POSITION_CAP)
        self.assertEqual(limits.get("S042_NOTIONAL_USD"), of.NOTIONAL_USD)
        self.assertEqual(limits.get("S042_STOP_FRACTION"), of.STOP_FRACTION)
        self.assertEqual(limits.get("PLUMBING_MAX_ORDER_USD"), ap.MAX_ORDER_USD)

    def test_proposed_limits_are_marked_not_owner_set(self):
        for row in limits.as_rows():
            self.assertEqual(row["owner_set"], not row["id"].startswith("PROPOSED_"), row["id"])


class GateTests(unittest.TestCase):
    def ids_failed(self, r):
        return {c["id"] for c in r["checks"] if not c["ok"]}

    def test_a_clean_plan_in_the_window_passes(self):
        r = gate.decide(good_plan(), HEALTHY, now=MORNING)
        self.assertEqual((r["decision"], self.ids_failed(r)), (gate.PASS, set()))

    def test_outside_the_window_or_market_closed_blocks_entries_only(self):
        r = gate.decide(good_plan(), HEALTHY, now=NIGHT)
        self.assertEqual((r["decision"], self.ids_failed(r)), (gate.BLOCK_ENTRIES, {"in_execution_window"}))
        self.assertEqual(gate.decide(good_plan(market_open=False), HEALTHY, now=MORNING)["decision"], gate.BLOCK_ENTRIES)

    def test_kill_switch_blocks_everything(self):
        self.assertEqual(gate.decide(good_plan(), HEALTHY, halted=True, now=MORNING)["decision"], gate.BLOCK_ALL)

    def test_malformed_entries_are_named_by_reason_and_never_by_symbol(self):
        bad = good_plan()["entries"][0]
        cases = {
            "no stop": {**bad, "order_class": "simple"},
            "too big": {**bad, "qty": "40"},
            "not a buy": {**bad, "side": "sell"},
            "bad symbol": {**bad, "symbol": "TOOLONGSYM"},
            "bad id": {**bad, "client_order_id": "manual-1"},
            "zero qty": {**bad, "qty": "0"},
        }
        for label, body in cases.items():
            r = gate.decide(good_plan(entries=[body]), HEALTHY, now=MORNING)
            self.assertEqual(r["decision"], gate.BLOCK_ENTRIES, label)
            self.assertIn("entry_orders_well_formed", self.ids_failed(r), label)
        missing = {k: v for k, v in bad.items() if k != "stop_loss"}
        self.assertEqual(gate.decide(good_plan(entries=[missing]), HEALTHY, now=MORNING)["decision"], gate.BLOCK_ENTRIES)
        self.assertNotIn("TOOLONGSYM", gate.render(gate.decide(good_plan(entries=[cases["bad symbol"]]), HEALTHY, now=MORNING)))

    def test_a_size_just_under_the_cap_passes_and_rounding_does_not_cause_a_false_block(self):
        e = good_plan()["entries"][0]
        edge = {**e, "qty": "33", "stop_loss": {"stop_price": "81.82"}}   # 33 shares at a 90.91 close is 3,000 USD
        self.assertEqual(gate.decide(good_plan(entries=[edge]), HEALTHY, now=MORNING)["decision"], gate.PASS)

    def test_more_entries_than_free_slots_blocks(self):
        e = good_plan()["entries"][0]
        two = [e, {**e, "symbol": "BBB", "client_order_id": "s042v3-BBB-261117"}]
        self.assertEqual(gate.decide(good_plan(entries=two, slots=1), HEALTHY, now=MORNING)["decision"], gate.BLOCK_ENTRIES)
        self.assertEqual(gate.decide(good_plan(entries=two, slots=2), HEALTHY, now=MORNING)["decision"], gate.PASS)

    def test_duplicate_entries_block(self):
        e = good_plan()["entries"][0]
        r = gate.decide(good_plan(entries=[e, dict(e)], slots=5), HEALTHY, now=MORNING)
        self.assertIn("no_duplicate_entries", self.ids_failed(r))

    def test_corporate_action_lookup_failure_blocks_new_entries(self):
        r = gate.decide(good_plan(corporate_actions_error="HTTP 500"), HEALTHY, now=MORNING)
        self.assertEqual((r["decision"], self.ids_failed(r)), (gate.BLOCK_ENTRIES, {"corporate_action_data_ok"}))

    def test_only_blocking_health_failures_stop_new_risk(self):
        news_down = [{"check": "news source", "status": "FAIL", "detail": ""}, *HEALTHY[:2]]
        self.assertEqual(gate.decide(good_plan(), news_down, now=MORNING)["decision"], gate.PASS)   # news is information only
        no_stop = [{"check": "stops present", "status": "FAIL", "detail": "NO FULL STOP on: AAA"}, *HEALTHY[1:]]
        r = gate.decide(good_plan(), no_stop, now=MORNING)
        self.assertEqual(r["decision"], gate.BLOCK_ENTRIES)
        self.assertNotIn("AAA", gate.render(r))
        stale = [HEALTHY[0], {"check": "data fresh", "status": "FAIL", "detail": ""}]
        self.assertEqual(gate.decide(good_plan(), stale, now=MORNING)["decision"], gate.BLOCK_ENTRIES)

    def test_fail_closed_when_health_or_plan_is_missing(self):
        self.assertEqual(gate.decide(good_plan(), None, now=MORNING)["decision"], gate.BLOCK_ENTRIES)
        for bad in (None, {}, "garbage", {"asof": "x"}):
            self.assertEqual(gate.decide(bad, HEALTHY, now=MORNING)["decision"], gate.BLOCK_ENTRIES)

    def test_gate_decides_correctly_on_a_real_dry_run_plan(self):
        with tempfile.TemporaryDirectory() as td:
            fake = BrokerFake(is_open=True)
            plan = of.run(fake, ["AAA", "BBB", "CCC"], FIXTURE_MORNING, Path(td), False, fetch_for(market()), "morning")
        self.assertTrue(plan["entries"], "the fixture should produce at least one entry")
        self.assertEqual(gate.decide(plan, HEALTHY, now=FIXTURE_MORNING)["decision"], gate.PASS)

    def test_exits_only_run_sends_exits_and_skips_entries_by_name(self):
        held = {"symbol": "BBB", "qty": "5", "avg_entry_price": "100"}
        closed = [{"id": "b0", "client_order_id": "s042v3-BBB-261110", "side": "buy", "symbol": "BBB", "filled_qty": "5", "filled_avg_price": "100", "filled_at": "2026-11-10T15:00:00Z", "legs": []}]
        with tempfile.TemporaryDirectory() as td:
            fake = BrokerFake(positions=[held], closed=closed, is_open=True)
            out = of.run(fake, ["AAA", "BBB", "CCC"], FIXTURE_MORNING, Path(td), True, fetch_for(market()), "morning", entries_allowed=False)
            buys = [b for b in fake.posts() if b["side"] == "buy"]
            self.assertEqual(buys, [])
            self.assertTrue(any(s["why"] == "ENTRY_BLOCKED_BY_GATE" for s in out["skipped"]))

    def test_halt_file_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(gate.halted(td))
            ap.halt_file(Path(td)).write_text("x")
            self.assertTrue(gate.halted(td))


def buy(sym, qty, px, at="2026-11-10T15:06:00Z", sub="2026-11-10T15:05:00Z", stop=None, cid=None, legs=None):
    return {"id": f"b-{sym}-{at}", "client_order_id": cid or f"s042v3-{sym}-261110", "side": "buy", "symbol": sym, "qty": str(qty), "filled_qty": str(qty), "filled_avg_price": str(px),
            "filled_at": at, "submitted_at": sub, "status": "filled",
            "legs": legs if legs is not None else ([{"side": "sell", "stop_price": str(stop), "filled_qty": "0"}] if stop else [])}


def sell(sym, qty, px, at="2026-11-13T15:06:00Z", cid=None):
    return {"id": f"s-{sym}-{at}", "client_order_id": cid or f"s042vx3-{sym}", "side": "sell", "symbol": sym, "qty": str(qty), "filled_qty": str(qty), "filled_avg_price": str(px),
            "filled_at": at, "submitted_at": at, "status": "filled", "legs": []}


class TcaTests(unittest.TestCase):
    def test_entry_cost_against_signal_close_and_arrival(self):
        o = buy("AAA", 10, 100.30, stop=90.0)             # signal close 100.00, so the fill is 30 bps worse than the decision price
        rows = tca.entry_costs([o], arrival=lambda s, t: 100.10)   # arrival 100.10, fill 100.30 is about 20 bps worse
        self.assertAlmostEqual(rows[0]["vs_signal_close_bps"], 30.0, places=1)
        self.assertAlmostEqual(rows[0]["vs_arrival_bps"], 19.98, places=1)

    def test_orders_queued_overnight_are_measured_against_the_open_of_the_fill_session(self):
        queued = buy("AAA", 10, 100.0, sub="2026-11-09T22:30:00Z", at="2026-11-10T14:30:05Z", stop=90.0)   # sent 5:30 PM ET, filled 9:30:05 AM ET
        asked = []
        tca.entry_costs([queued], arrival=lambda s, t: asked.append(t) or 100.0)
        self.assertEqual(asked, ["2026-11-10T14:30:00+00:00"])
        live = buy("AAA", 10, 100.0, sub="2026-11-10T15:05:00Z", at="2026-11-10T15:05:02Z", stop=90.0)   # a morning-mode order is measured from the moment it was sent
        asked.clear()
        tca.entry_costs([live], arrival=lambda s, t: asked.append(t) or 100.0)
        self.assertEqual(asked, ["2026-11-10T15:05:00+00:00"])

    def test_missing_arrival_or_stop_leaves_that_measure_blank_not_zero(self):
        rows = tca.entry_costs([buy("AAA", 10, 100.0)], arrival=lambda s, t: None)
        self.assertEqual(rows, [{"vs_signal_close_bps": None, "vs_arrival_bps": None, "mode": "market_at_open"}])

    def test_other_strategies_and_unfilled_orders_are_ignored(self):
        other = buy("ZZZ", 1, 5.0, cid="xlab-zzz-opg")
        unfilled = {**buy("AAA", 10, 100.0, stop=90.0), "filled_qty": "0"}
        self.assertEqual(tca.entry_costs([other, unfilled], arrival=lambda s, t: 100.0), [])

    def test_exit_cost_is_worse_when_we_sell_lower(self):
        rows = tca.exit_costs([sell("AAA", 10, 99.50, cid="s042v3x-AAA-261113")], arrival=lambda s, t: 100.0)
        self.assertAlmostEqual(rows[0]["vs_arrival_bps"], 50.0, places=1)

    def test_fill_stats_show_unfilled_and_partial(self):
        full = buy("AAA", 10, 100.0)
        part = {**buy("BBB", 10, 50.0), "filled_qty": "4"}
        none = {**buy("CCC", 10, 50.0), "filled_qty": "0", "status": "canceled"}
        f = tca.fill_stats([full, part, none, buy("ZZZ", 1, 1.0, cid="manual")])
        self.assertEqual((f["sent"], f["filled"], f["partial"], f["unfilled"]), (3, 1, 1, 1))
        self.assertAlmostEqual(f["fill_rate"], 0.333, places=3)

    def test_verdict_needs_enough_fills_then_compares_to_the_model(self):
        self.assertEqual(tca.verdict({"n": 4, "mean_bps": 500})[0], "OK")
        self.assertEqual(tca.verdict({"n": 20, "mean_bps": 12})[0], "OK")
        self.assertEqual(tca.verdict({"n": 20, "mean_bps": 19})[0], "WARN")
        self.assertEqual(tca.verdict({"n": 20, "mean_bps": 28})[0], "FAIL")

    def test_analyze_end_to_end_with_a_fake_broker(self):
        class C:
            def __call__(self, method, path, body=None, host="paper"):
                if path.startswith("/v2/orders"):
                    return [buy("AAA", 10, 100.30, stop=90.0, sub="2026-11-16T15:05:00Z", at="2026-11-16T15:06:00Z")]
                if path.startswith("/v2/stocks/bars"):
                    return {"bars": {"AAA": [{"o": 100.10}]}}
                raise AssertionError(path)
        r = tca.analyze(C(), now=datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc), days=10)
        self.assertEqual((r["entries_filled"], r["by_mode"]["market_at_open"]["entry_vs_arrival"]["n"], r["by_mode"]["morning_limit"]["entries_filled"], r["status"]), (1, 1, 0, "OK"))
        self.assertNotIn("AAA", json.dumps(r))

    def test_the_two_execution_modes_are_judged_against_their_own_model(self):
        orders = []
        for i in range(12):   # legacy market-at-open fills 30 bps over arrival: fine against its 32 bps model
            orders.append({**buy(f"M{i}", 10, 100.30, stop=90.0, sub="2026-11-15T22:30:00Z", at="2026-11-16T14:30:05Z"), "type": "market"})
        for i in range(12):   # morning limit fills 30 bps over arrival: three times the 9 bps model
            orders.append({**buy(f"L{i}", 10, 100.30, stop=90.0, sub="2026-11-16T15:05:00Z", at="2026-11-16T15:05:02Z"), "type": "limit"})

        class C:
            def __call__(self, method, path, body=None, host="paper"):
                if path.startswith("/v2/orders"): return orders
                if path.startswith("/v2/stocks/bars"): return {"bars": {s: [{"o": 100.0}] for s in path.split("symbols=")[1].split("&")[0].split(",")}}
                raise AssertionError(path)
        r = tca.analyze(C(), now=datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc))
        self.assertEqual((r["by_mode"]["market_at_open"]["status"], r["by_mode"]["morning_limit"]["status"], r["status"]), ("OK", "FAIL", "FAIL"))

    def test_a_data_error_on_one_fill_does_not_stop_the_report(self):
        class C:
            def __call__(self, method, path, body=None, host="paper"):
                if path.startswith("/v2/orders"):
                    return [buy("AAA", 10, 100.0, stop=90.0, sub="2026-11-16T15:05:00Z")]
                raise ap.GuardError("HTTP 500")
        r = tca.analyze(C(), now=datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc))
        self.assertEqual((r["entries_filled"], r["by_mode"]["market_at_open"]["entry_vs_arrival"]["n"]), (1, 0))


NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)


def pos(sym, qty): return {"symbol": sym, "qty": str(qty)}


class ReconTests(unittest.TestCase):
    def test_a_clean_book_has_no_breaks(self):
        r = recon.reconcile([pos("AAA", 10)], [buy("AAA", 10, 100.0, at="2026-11-16T15:00:00Z")], NOW)
        self.assertEqual((r["status"], r["breaks"]), ("OK", []))

    def test_quantity_break_is_a_fail(self):
        r = recon.reconcile([pos("AAA", 7)], [buy("AAA", 10, 100.0, at="2026-11-16T15:00:00Z")], NOW)
        self.assertEqual((r["status"], r["break_counts"]), ("FAIL", {"QTY_BREAK": 1}))

    def test_stop_leg_and_rule_exit_net_the_position_to_zero(self):
        stopped = buy("AAA", 10, 100.0, at="2026-11-10T15:00:00Z", legs=[{"side": "sell", "stop_price": "90", "filled_qty": "10"}])
        exited = [buy("BBB", 5, 50.0, at="2026-11-10T15:00:00Z"), sell("BBB", 5, 55.0, cid="s042v3x-BBB-261113")]
        r = recon.reconcile([], [stopped] + exited, NOW)
        self.assertEqual((r["status"], r["breaks"]), ("OK", []))

    def test_unexplained_position_is_a_warning(self):
        r = recon.reconcile([pos("ZZZ", 3)], [], NOW)
        self.assertEqual((r["status"], r["break_counts"]), ("WARN", {"UNEXPLAINED_POSITION": 1}))

    def test_position_gone_without_an_exit_is_missing(self):
        r = recon.reconcile([], [buy("AAA", 10, 100.0, at="2026-11-12T15:00:00Z")], NOW)
        self.assertEqual((r["status"], r["break_counts"], r["oldest_break_days"]), ("WARN", {"MISSING_POSITION": 1}, 5))

    def test_overdue_exit_flags_a_position_held_past_the_time_stop_plus_margin(self):
        old = buy("AAA", 10, 100.0, at="2026-11-01T15:00:00Z")
        self.assertEqual(recon.reconcile([pos("AAA", 10)], [old], NOW)["break_counts"], {"OVERDUE_EXIT": 1})
        fresh = buy("AAA", 10, 100.0, at="2026-11-12T15:00:00Z")
        self.assertEqual(recon.reconcile([pos("AAA", 10)], [fresh], NOW)["status"], "OK")

    def test_more_s042_positions_than_the_cap_is_a_fail(self):
        syms = [f"S{i}" for i in range(of.POSITION_CAP + 1)]
        orders = [buy(s, 1, 10.0, at="2026-11-16T15:00:00Z", cid=f"s042v3-{s}-261116") for s in syms]
        r = recon.reconcile([pos(s, 1) for s in syms], orders, NOW)
        self.assertEqual((r["status"], r["break_counts"]), ("FAIL", {"CAP_BREACH": 1}))

    def test_a_full_page_of_history_is_flagged_and_skips_the_missing_check(self):
        orders = [buy("AAA", 10, 100.0, at="2026-11-12T15:00:00Z")] + [sell("Q%d" % i, 1, 1.0) for i in range(499)]
        r = recon.reconcile([], orders, NOW)
        self.assertIn("HISTORY_TRUNCATED", r["break_counts"])
        self.assertNotIn("MISSING_POSITION", r["break_counts"])

    def test_a_stop_guard_stop_that_filled_nets_the_position_to_zero(self):
        entry = buy("AAA", 10, 100.0, at="2026-11-10T15:00:00Z")
        stopfill = sell("AAA", 10, 90.0, cid="s042v3s-AAA-261112")
        self.assertEqual(recon.reconcile([], [entry, stopfill], NOW)["breaks"], [])

    def test_experiment_orders_with_a_close_pair_net_to_zero(self):
        a = buy("AAA", 1, 100.0, cid="xlab-aaa-opg_market", at="2026-11-17T14:30:00Z")
        b = sell("AAA", 1, 100.1, cid="xlab-aaa-close")
        self.assertEqual(recon.reconcile([], [a, b], NOW)["breaks"], [])

    def test_summary_never_names_a_symbol(self):
        r = recon.reconcile([pos("SECRETCO", 3)], [], NOW)
        self.assertNotIn("SECRETCO", recon.summary(r))


class RiskReportTests(unittest.TestCase):
    ACCOUNT = {"equity": "100000", "last_equity": "100000"}

    def test_quiet_book_is_green(self):
        r = riskreport.measure(self.ACCOUNT, [{"market_value": "4000"}] * 2, [100000.0] * 5)
        self.assertEqual((r["status"], r["measures"]["gross_exposure_pct"], r["enforced"]), ("OK", 8.0, False))

    def test_daily_loss_tiers(self):
        self.assertEqual(riskreport.measure({"equity": "98000", "last_equity": "100000"}, [], [100000.0])["tiers"]["day_result_pct"], "WARN")
        self.assertEqual(riskreport.measure({"equity": "96500", "last_equity": "100000"}, [], [100000.0])["tiers"]["day_result_pct"], "FAIL")

    def test_drawdown_is_measured_from_the_peak_not_the_start(self):
        r = riskreport.measure({"equity": "94000", "last_equity": "94000"}, [], [100000.0, 110000.0, None, 105000.0])
        self.assertAlmostEqual(r["measures"]["drawdown_from_peak_pct"], -14.55, places=2)
        self.assertEqual(r["tiers"]["drawdown_from_peak_pct"], "FAIL")

    def test_concentration_and_exposure_tiers(self):
        r = riskreport.measure(self.ACCOUNT, [{"market_value": "-12000"}, {"market_value": "30000"}], [])
        self.assertEqual((r["tiers"]["largest_position_pct"], r["tiers"]["gross_exposure_pct"]), ("FAIL", "OK"))

    def test_missing_history_and_empty_book_do_not_crash(self):
        r = riskreport.measure(self.ACCOUNT, [], None)
        self.assertEqual((r["status"], r["measures"]["largest_position_pct"]), ("OK", 0.0))

    def test_analyze_survives_a_history_endpoint_error(self):
        class C:
            def __call__(self, method, path, body=None, host="paper"):
                if path == "/v2/account": return {"equity": "100000", "last_equity": "100000"}
                if path == "/v2/positions": return []
                raise ap.GuardError("HTTP 500")
        self.assertEqual(riskreport.analyze(C())["status"], "OK")


def trade(r, mode="morning_limit"): return {"ret_pct": r, "mode": mode}


class MonitorTests(unittest.TestCase):
    def by(self, rows): return {r["trigger"]: r for r in rows}

    def test_early_days_report_progress_and_no_verdicts(self):
        m = self.by(monitor.evaluate([trade(1.0)] * 5, {"status": "OK", "detail": "x", "fills": {"sent": 5, "filled": 5, "fill_rate": 1.0}}, HEALTHY))
        self.assertEqual(m["evidence_progress"]["detail"], "5 closed forward trades (5 under the current morning-limit execution) toward the go-live minimum of 100")
        self.assertEqual({r["status"] for r in m.values()}, {"OK"})

    def test_return_drift_warns_when_far_below_the_backtest_and_fails_when_negative_after_50(self):
        weak = [trade(-0.5 if i % 2 else 0.3) for i in range(40)]
        self.assertEqual(self.by(monitor.evaluate(weak, None, HEALTHY))["return_drift"]["status"], "WARN")
        losing = [trade(-0.4 if i % 3 else 0.2) for i in range(60)]
        self.assertEqual(self.by(monitor.evaluate(losing, None, HEALTHY))["return_drift"]["status"], "FAIL")

    def test_legacy_market_at_open_trades_never_raise_a_return_alarm(self):
        legacy = [trade(-0.6 if i % 2 else 0.1, "market_at_open") for i in range(80)]
        m = self.by(monitor.evaluate(legacy, None, HEALTHY))
        self.assertEqual(m["return_drift"]["status"], "OK")
        self.assertIn("0 morning-limit trades", m["return_drift"]["detail"])

    def test_return_in_line_with_the_backtest_is_ok(self):
        ok = [trade(0.9 if i % 2 else -0.45) for i in range(40)]
        self.assertEqual(self.by(monitor.evaluate(ok, None, HEALTHY))["return_drift"]["status"], "OK")

    def test_low_fill_rate_warns_and_health_failure_is_red(self):
        t = {"status": "OK", "detail": "x", "fills": {"sent": 20, "filled": 8, "fill_rate": 0.4}}
        self.assertEqual(self.by(monitor.evaluate([], t, HEALTHY))["fill_rate"]["status"], "WARN")
        bad = [{"check": "stops present", "status": "FAIL", "detail": ""}]
        self.assertEqual(self.by(monitor.evaluate([], None, bad))["plumbing"]["status"], "FAIL")

    def test_cost_drift_passes_through_the_tca_verdict(self):
        t = {"status": "FAIL", "detail": "too high", "fills": {}}
        self.assertEqual(self.by(monitor.evaluate([], t, HEALTHY))["cost_drift"]["status"], "FAIL")


class PackBroker:
    """A small fake broker good enough for every stage of the pack."""
    def __init__(self, fail=None):
        self.fail = fail

    def __call__(self, method, path, body=None, host="paper"):
        if self.fail and self.fail in path:
            raise ap.GuardError("HTTP 500")
        if path == "/v2/positions": return [{"symbol": "SECRETCO", "qty": "10", "market_value": "1000"}]
        if path.startswith("/v2/orders?status=open"): return [{"symbol": "SECRETCO", "side": "sell", "type": "stop", "qty": "10", "status": "new", "client_order_id": "x", "legs": []}]
        if path.startswith("/v2/orders?status=closed"): return [buy("SECRETCO", 10, 100.0, at="2026-11-16T15:00:00Z", stop=90.0)]
        if path.startswith("/v1beta1/news"): return {"news": [{"source": "benzinga", "created_at": "2026-11-17T14:59:00Z"}]}
        if path.startswith("/v2/stocks/bars"):
            if "1Min" in path: return {"bars": {"SECRETCO": [{"o": 100.0}]}}
            return {"bars": {"SPY": [{"t": "2026-11-16T05:00:00Z"}]}}
        if path == "/v2/account": return {"equity": "100000", "last_equity": "100000"}
        if path.startswith("/v2/account/portfolio/history"): return {"equity": [100000.0, 100000.0]}
        raise AssertionError(path)


class DeskPackTests(unittest.TestCase):
    def test_pack_walks_all_nine_stages_and_never_prints_a_symbol(self):
        pack = deskpack.build(PackBroker(), NOW, plan=good_plan(), gate=gate.decide(good_plan(), HEALTHY, now=MORNING), sent={"mode": "morning", "submit": True, "orders": [{}], "skipped": []})
        self.assertEqual([s["n"] for s in pack["stages"]], list(range(1, 10)))
        text = deskpack.render(pack)
        for needle in ("SECRETCO", "AAA", "ZZZ"):
            self.assertNotIn(needle, text)
        self.assertIn("Desk pack", text)

    def test_one_failing_stage_does_not_hide_the_others(self):
        pack = deskpack.build(PackBroker(fail="portfolio/history") if False else PackBroker(fail="/v2/account"), NOW)
        by = {s["n"]: s for s in pack["stages"]}
        self.assertEqual(by[7]["status"], "FAIL")
        self.assertIn(by[6]["status"], ("OK", "WARN", "FAIL"))
        self.assertEqual(pack["overall"], "FAIL")
        self.assertTrue(any("stage 7" in d for d in pack["owner_decisions"]))

    def test_a_dead_broker_gives_a_red_pack_instead_of_a_crash(self):
        class Dead:
            def __call__(self, *a, **k): raise ap.GuardError("network error")
        pack = deskpack.build(Dead(), NOW)
        self.assertEqual(pack["overall"], "FAIL")
        self.assertEqual(len(pack["stages"]), 9)


class RunnerTests(unittest.TestCase):
    def test_gate_command_writes_the_decision_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            plan, out = Path(td) / "plan.json", Path(td) / "gate.json"
            plan.write_text(json.dumps(good_plan()))
            with mock.patch.object(ap, "make_client", return_value=PackBroker()), mock.patch.dict("os.environ", {"ALPACA_PAPER_STATE": td}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(["gate", str(plan), "--out", str(out)]), 0)
            self.assertIn(json.loads(out.read_text())["decision"], (gate.PASS, gate.BLOCK_ENTRIES))

    def test_gate_command_fails_closed_without_a_working_client(self):
        with tempfile.TemporaryDirectory() as td:
            plan, out = Path(td) / "plan.json", Path(td) / "gate.json"
            plan.write_text(json.dumps(good_plan()))
            with mock.patch.object(ap, "make_client", side_effect=ap.GuardError("no keys")), mock.patch.dict("os.environ", {"ALPACA_PAPER_STATE": td}):
                with contextlib.redirect_stdout(io.StringIO()):
                    runner.main(["gate", str(plan), "--out", str(out)])
            self.assertEqual(json.loads(out.read_text())["decision"], gate.BLOCK_ENTRIES)

    def test_pack_command_appends_to_the_summary_file(self):
        with tempfile.TemporaryDirectory() as td:
            summary = Path(td) / "summary.md"
            with mock.patch.object(ap, "make_client", return_value=PackBroker()):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(["pack", "--summary-file", str(summary)]), 0)
            self.assertIn("Desk pack", summary.read_text())

    def test_limits_command_lists_the_register(self):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(runner.main(["limits"]), 0)
        self.assertIn("S042_POSITION_CAP", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
