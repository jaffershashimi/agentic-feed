import math
import unittest
import urllib.error
from datetime import date

from feed_pipeline import features, sources

CBOE_OHLC = "DATE,OPEN,HIGH,LOW,CLOSE\n10/20/2026,18.1,18.9,17.5,18.2\n10/21/2026,18.2,19.4,18.0,19.0\n"
CBOE_SINGLE = "DATE,VVIX\n10/20/2026,92.1\n10/21/2026,95.4\n"
FRED_NEW = "observation_date,DGS10\n2026-10-19,4.10\n2026-10-20,.\n2026-10-21,4.15\n"
FRED_OLD = "DATE,NFCI\n2026-10-09,-0.51\n2026-10-16,-0.49\n"


class SourceParsingTest(unittest.TestCase):
    def test_cboe_formats(self):
        self.assertEqual(sources.parse_cboe(CBOE_OHLC, "VIX"), [(date(2026, 10, 20), 18.2), (date(2026, 10, 21), 19.0)])
        self.assertEqual(sources.parse_cboe(CBOE_SINGLE, "VVIX")[-1], (date(2026, 10, 21), 95.4))
        with self.assertRaises(ValueError):
            sources.parse_cboe("DATE,OTHER\n10/21/2026,1\n", "VIX")

    def test_fred_missing_values_are_skipped_not_zeroed(self):
        self.assertEqual(sources.parse_fred(FRED_NEW, "DGS10"), [(date(2026, 10, 19), 4.10), (date(2026, 10, 21), 4.15)])
        self.assertEqual(sources.parse_fred(FRED_OLD, "NFCI")[-1], (date(2026, 10, 16), -0.49))

    def test_fetch_failure_is_reported_not_raised(self):
        def broken(url):
            raise urllib.error.URLError("network down")

        result = sources.fetch_cboe("VIX", fetch=broken)
        self.assertFalse(result.ok)
        self.assertIn("network down", result.error)
        self.assertEqual(result.series, [])
        bad = sources.fetch_fred("DGS10", fetch=lambda url: "garbage")
        self.assertFalse(bad.ok)


class FredApiTest(unittest.TestCase):
    def test_api_json_is_parsed_and_missing_skipped(self):
        body = '{"observations":[{"date":"2026-10-20","value":"4.10"},{"date":"2026-10-21","value":"."}]}'
        result = sources.fetch_fred("DGS10", fetch=lambda url: body, api_key="SECRETKEY123")
        self.assertTrue(result.ok)
        self.assertEqual(result.series, [(date(2026, 10, 20), 4.10)])

    def test_api_key_never_reaches_recorded_url_or_error(self):
        seen = []

        def failing(url):
            seen.append(url)
            raise urllib.error.URLError(f"refused {url}")

        result = sources.fetch_fred("DGS10", fetch=failing, api_key="SECRETKEY123")
        self.assertIn("api_key=SECRETKEY123", seen[0])
        self.assertNotIn("SECRETKEY123", result.url)
        self.assertNotIn("SECRETKEY123", result.error)
        error_body = sources.fetch_fred("DGS10", fetch=lambda url: '{"error_message":"Bad Request"}', api_key="SECRETKEY123")
        self.assertIn("Bad Request", error_body.error)

    def test_html_instead_of_csv_is_a_failure(self):
        result = sources.fetch_fred("DGS10", fetch=lambda url: "<!doctype html><html></html>", api_key="")
        self.assertFalse(result.ok)


class FeatureTest(unittest.TestCase):
    def test_realized_vol_matches_hand_calculation(self):
        closes = [(date(2026, 1, d), 100 * (1.01 if d % 2 else 0.99) ** 0 * (1.01 ** (d % 2))) for d in range(1, 23)]
        returns = [math.log(b[1] / a[1]) for a, b in zip(closes[-21:], closes[-20:])]
        mean = sum(returns) / len(returns)
        expected = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1) * 252) * 100
        self.assertAlmostEqual(features.realized_vol(closes, 20), expected)
        self.assertIsNone(features.realized_vol(closes[:5], 20))

    def test_as_of_slice_prevents_lookahead(self):
        series = [(date(2026, 10, 20), 1.0), (date(2026, 10, 21), 2.0), (date(2026, 10, 22), 99.0)]
        self.assertEqual(features.latest(features.as_of_slice(series, date(2026, 10, 21))), 2.0)

    def test_expected_move_and_ratio(self):
        self.assertAlmostEqual(features.expected_move_pct(16.0, 252), 16.0)
        self.assertAlmostEqual(features.expected_move_pct(16.0, 1), 16.0 / math.sqrt(252))
        self.assertIsNone(features.ratio(1.0, 0))
        self.assertIsNone(features.expected_move_pct(None, 5))

    def test_regime_branches(self):
        self.assertEqual(features.regime_v0(None, 20, 1, 1)[0], "unavailable")
        self.assertEqual(features.regime_v0(30, 28, 1, 1)[0], "stress")
        self.assertEqual(features.regime_v0(20, 19, 1, 1)[0], "stress")
        self.assertEqual(features.regime_v0(15, 18, 110, 100)[0], "calm_uptrend")
        self.assertEqual(features.regime_v0(15, 18, 90, 100)[0], "neutral")

    def test_macro_score_reports_missing_inputs(self):
        flat = [(date(2026, 1, 1), 1.0)]
        score, tilt, missing = features.macro_score(flat, flat, flat)
        self.assertEqual((score, tilt), (None, "unavailable"))
        self.assertEqual(missing, ["hy_oas", "nfci", "usd"])


if __name__ == "__main__":
    unittest.main()
