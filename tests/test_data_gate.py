import unittest
from datetime import date, timedelta

from consumer import data_gate as dg


def bars(closes, start=date(2026, 1, 5)):
    ds = []
    d = start
    while len(ds) < len(closes):
        if d.weekday() < 5:
            ds.append(d.isoformat())
        d += timedelta(days=1)
    return {"date": ds, "o": list(closes), "h": [c * 1.01 for c in closes], "l": [c * 0.99 for c in closes], "c": list(closes), "v": [1e6] * len(closes)}


class SeriesTests(unittest.TestCase):
    def test_clean_series_has_no_issues(self):
        self.assertEqual(dg.check_series(bars([100 + i * 0.1 for i in range(120)])), [])

    def test_unadjusted_two_for_one_split_is_suspected_but_a_real_crash_is_not(self):
        c = [100.0 + (i % 3) * 0.1 for i in range(60)] + [50.0 + (i % 3) * 0.1 for i in range(60)]
        self.assertIn("SPLIT_SUSPECT", dg.check_series(bars(c)))
        crash = [100.0 + (i % 3) * 0.1 for i in range(60)] + [57.0 + (i % 3) * 0.1 for i in range(60)]  # -43 percent, not a split ratio
        self.assertNotIn("SPLIT_SUSPECT", dg.check_series(bars(crash)))
        small = [100.0 + (i % 3) * 0.1 for i in range(60)] + [97.0 + (i % 3) * 0.1 for i in range(60)]
        self.assertEqual(dg.check_series(bars(small)), [])

    def test_reverse_split_ratio_is_suspected(self):
        self.assertIn("SPLIT_SUSPECT", dg.check_series(bars([10.0 + (i % 3) * 0.01 for i in range(60)] + [100.0 + (i % 3) * 0.1 for i in range(60)])))

    def test_bad_prices_inconsistent_bars_and_unsorted_dates(self):
        d = bars([100.0] * 40)
        d["c"][5] = 0.0
        self.assertIn("NONPOSITIVE", dg.check_series(d))
        d = bars([100.0] * 40)
        d["h"][7] = 90.0
        d["l"][7] = 95.0
        self.assertIn("OHLC_INCONSISTENT", dg.check_series(d))
        d = bars([100.0 + i for i in range(40)])
        d["date"][10] = d["date"][9]
        self.assertIn("UNSORTED", dg.check_series(d))

    def test_gap_zero_volume_and_flat_tape(self):
        d = bars([100.0 + (i % 3) for i in range(60)])
        del d["date"][30:34]
        for k in ("o", "h", "l", "c", "v"):
            del d[k][30:34]
        self.assertIn("GAP", dg.check_series(d))
        d = bars([100.0 + (i % 3) for i in range(60)])
        d["v"][-3:] = [0, 0, 0]
        self.assertIn("ZERO_VOLUME", dg.check_series(d))
        self.assertIn("FLAT_TAPE", dg.check_series(bars([100.0] * 60)))
        self.assertEqual(dg.check_series({"date": [], "o": [], "h": [], "l": [], "c": [], "v": []}), ["EMPTY"])

    def test_gate_splits_clean_from_quarantined_and_ignores_missing(self):
        data = {"OK": bars([100 + i * 0.1 for i in range(80)]), "BAD": bars([100.0] * 40 + [50.0] * 40)}
        clean, bad = dg.gate(data, ["OK", "BAD", "NONE"])
        self.assertEqual(clean, ["OK"])
        self.assertEqual(list(bad), ["BAD"])


class ActionsTests(unittest.TestCase):
    def test_collects_splits_in_window_and_fails_soft(self):
        asof = date(2026, 11, 13)

        def call(method, path, body=None, host="paper"):
            self.assertEqual(host, "data")
            self.assertIn("/v1/corporate-actions", path)
            return {"corporate_actions": {"forward_splits": [{"symbol": "AAA", "ex_date": "2026-11-17"}, {"symbol": "OLD", "ex_date": "2026-01-02"}],
                                          "spin_offs": [{"initial_symbol": "BBB", "process_date": "2026-11-18"}]}, "next_page_token": None}

        r = dg.upcoming_actions(call, ["AAA", "BBB", "OLD"], asof)
        self.assertEqual(sorted(r["actions"]), ["AAA", "BBB"])
        self.assertIsNone(r["error"])

        def boom(*a, **k):
            raise RuntimeError("HTTP 500")

        r = dg.upcoming_actions(boom, ["AAA"], asof)
        self.assertEqual(r["actions"], {})
        self.assertIn("500", r["error"])


if __name__ == "__main__":
    unittest.main()
