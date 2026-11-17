import unittest
from datetime import datetime, timezone

from consumer import news_veto_study as nv

CAL = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"]


def item(created, headline, symbols=("AAA",)):
    return {"id": created + headline, "created_at": created, "headline": headline, "summary": "", "symbols": list(symbols), "source": "benzinga"}


class WindowTests(unittest.TestCase):
    def test_window_runs_from_the_close_before_the_signal_day_to_0950_et_on_the_entry_date(self):
        start, end = nv.news_window("2026-03-05", CAL)    # signal day 03-04, session before it 03-03
        self.assertEqual(start, datetime(2026, 3, 3, 21, 0, tzinfo=timezone.utc))   # 16:00 EST
        self.assertEqual(end, datetime(2026, 3, 5, 14, 50, tzinfo=timezone.utc))    # 09:50 EST
        self.assertIsNone(nv.news_window("2026-03-02", CAL))

    def test_daylight_saving_shifts_the_utc_times(self):
        start, end = nv.news_window("2026-03-10", ["2026-03-05", "2026-03-06", "2026-03-09", "2026-03-10"])
        self.assertEqual(end, datetime(2026, 3, 10, 13, 50, tzinfo=timezone.utc))   # EDT from 2026-03-08

    def test_items_outside_the_window_or_for_other_symbols_are_ignored(self):
        start, end = nv.news_window("2026-03-05", CAL)
        inside = item("2026-03-04T15:00:00Z", "AAA cuts guidance for the year")
        too_late = item("2026-03-05T14:55:00Z", "AAA cuts guidance for the year")
        too_early = item("2026-03-03T20:59:00Z", "AAA cuts guidance for the year")
        other = item("2026-03-04T15:00:00Z", "BBB cuts guidance", symbols=("BBB",))
        got = nv.items_in_window([inside, too_late, too_early, other], "AAA", start, end)
        self.assertEqual(got, [inside])


class FlagTests(unittest.TestCase):
    def test_v1_needs_informative_news_and_v2_needs_an_event_type(self):
        guidance = item("2026-03-04T15:00:00Z", "AAA cuts full-year guidance after weak quarter")
        other = item("2026-03-04T15:00:00Z", "AAA opens new office in Austin")
        self.assertEqual(nv.flags([guidance]), (True, True))
        self.assertEqual(nv.flags([other]), (True, False))
        self.assertEqual(nv.flags([]), (False, False))

    def test_a_crowded_list_item_is_not_informative(self):
        crowd = item("2026-03-04T15:00:00Z", "Stocks moving today", symbols=("AAA", "BBB", "CCC", "DDD"))
        self.assertEqual(nv.flags([crowd]), (False, False))


def rows_from(pairs, start_year=2022):
    """pairs: (window label, week index, ret_pp, flagged). Build rows with real dates inside each window."""
    base = {"Development": "2019-03-", "Judge": "2023-03-", "Final": "2025-03-"}
    out = []
    for i, (w, wk, ret, flagged) in enumerate(pairs):
        day = f"{base[w]}{1 + (wk % 25):02d}"
        out.append({"entry_date": day, "week": f"{base[w][:4]}-W{wk:02d}", "ret_pp": ret, "v1": flagged, "v2": flagged})
    return out


class StatsTests(unittest.TestCase):
    def test_d_is_no_news_minus_news_and_is_deterministic(self):
        rows = [{"week": f"w{i % 6}", "ret_pp": 1.0 if i % 2 else -1.0, "flagged": bool(i % 2 == 0)} for i in range(120)]
        a, b = nv.diff_interval(rows, resamples=300), nv.diff_interval(rows, resamples=300)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["d"], 2.0)
        self.assertEqual((a["n_news"], a["n_no_news"]), (60, 60))

    def test_an_empty_group_gives_none(self):
        self.assertIsNone(nv.diff_interval([{"week": "w", "ret_pp": 1.0, "flagged": True}]))

    def test_pass_needs_every_bar(self):
        pairs = []
        for w in ("Development", "Judge", "Final"):
            for wk in range(40):
                pairs.append((w, wk, 1.2 + (0.1 if wk % 2 else -0.1), False))     # no-news trades earn about +1.2
                if wk % 5 == 0:
                    pairs.append((w, wk, 0.1 + (0.1 if wk % 2 else -0.1), True))  # news trades earn about +0.1, 20 percent of trades
        res = nv.evaluate(rows_from(pairs))
        self.assertEqual(res["V1_any_informative"]["verdict"], "PASS")

    def test_no_difference_is_not_a_pass(self):
        pairs = [(w, wk, 0.5 + (0.2 if wk % 2 else -0.2), wk % 4 == 0) for w in ("Development", "Judge", "Final") for wk in range(60)]
        res = nv.evaluate(rows_from(pairs))
        self.assertNotEqual(res["V1_any_informative"]["verdict"], "PASS")

    def test_news_trades_doing_better_is_rejected(self):
        pairs = []
        for w in ("Development", "Judge", "Final"):
            for wk in range(40):
                pairs.append((w, wk, 0.0 + (0.1 if wk % 2 else -0.1), False))
                if wk % 5 == 0:
                    pairs.append((w, wk, 2.0, True))
        self.assertEqual(nv.evaluate(rows_from(pairs))["V1_any_informative"]["verdict"], "REJECTED")

    def test_coverage_bar_fails_when_almost_nothing_is_flagged(self):
        pairs = [(w, wk, 1.0, False) for w in ("Development", "Judge", "Final") for wk in range(40)] + [("Judge", 1, -5.0, True)]
        r = nv.evaluate(rows_from(pairs))["V1_any_informative"]
        self.assertFalse(r["bars"]["4_flagged_share_between_5_and_95_percent_in_each_window"])
        self.assertNotEqual(r["verdict"], "PASS")


class PipelineTests(unittest.TestCase):
    def test_trade_rows_join_news_by_symbol_and_window_and_scale_returns_to_percentage_points(self):
        trades = [{"symbol": "AAA", "entry_date": "2026-03-05", "ret": 0.02}, {"symbol": "AAA", "entry_date": "2026-03-02", "ret": 0.01}]
        calls = []

        def news_for(sym, start, end):
            calls.append(sym)
            return [item("2026-03-04T15:00:00Z", "AAA cuts full-year guidance after weak quarter")]
        rows, skipped = nv.trade_rows(trades, CAL, news_for)
        self.assertEqual((len(rows), skipped, calls), (1, 1, ["AAA"]))
        self.assertEqual((rows[0]["ret_pp"], rows[0]["v1"], rows[0]["v2"]), (2.0, True, True))


if __name__ == "__main__":
    unittest.main()
