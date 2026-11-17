import base64
import json
import tempfile
import unittest
from pathlib import Path

from consumer import databento_client as dc


class FakeOpener:
    def __init__(self, cost="0.42", body=b"ts,bid,ask\n1,10.0,10.1\n"):
        self.cost, self.body, self.reqs = cost, body, []

    def __call__(self, req, timeout=0):
        self.reqs.append(req)

        class R:
            def __init__(s, b):
                s.b = b

            def read(s):
                return s.b
        return R(self.cost.encode() if "metadata.get_cost" in req.full_url else self.body)


class ClientTests(unittest.TestCase):
    def test_estimate_uses_basic_auth_with_the_key_as_user_and_returns_dollars(self):
        op = FakeOpener()
        c = dc.estimate_cost("XNAS.ITCH", "mbp-1", ["AAPL", "MSFT"], "2026-11-17T14:30", "2026-11-17T15:30", key="db-SECRET", opener=op)
        self.assertEqual(c, 0.42)
        req = op.reqs[0]
        self.assertIn("symbols=AAPL%2CMSFT", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Basic " + base64.b64encode(b"db-SECRET:").decode())
        self.assertNotIn("db-SECRET", req.full_url)

    def test_a_missing_key_is_a_clear_skip_not_a_crash(self):
        with self.assertRaises(dc.KeyMissing):
            dc._key({})
        self.assertEqual(dc.main(["cost", "XNAS.ITCH", "mbp-1", "AAPL", "a", "b"]), 0)

    def test_budget_cap_defaults_to_five_and_blocks_anything_over_it(self):
        self.assertEqual(dc.budget({}), 5.0)
        self.assertEqual(dc.budget({"DATABENTO_BUDGET_USD": "20"}), 20.0)
        self.assertEqual(dc.budget({"DATABENTO_BUDGET_USD": "abc"}), 5.0)
        dc.guard(4.99, {})
        with self.assertRaises(dc.BudgetError):
            dc.guard(5.01, {})

    def test_download_never_starts_when_the_estimate_is_over_the_cap(self):
        op = FakeOpener(cost="12.5")
        out = Path(tempfile.mkdtemp()) / "x.csv"
        with self.assertRaises(dc.BudgetError):
            dc.download("XNAS.ITCH", "mbp-1", ["AAPL"], "a", "b", out, key="k", opener=op, env={})
        self.assertFalse(out.exists())
        self.assertEqual(len(op.reqs), 1)            # only the free estimate was requested

    def test_download_within_the_cap_writes_the_private_file(self):
        op = FakeOpener(cost="0.5")
        out = Path(tempfile.mkdtemp()) / "x.csv"
        cost = dc.download("XNAS.ITCH", "mbp-1", ["AAPL"], "a", "b", out, key="k", opener=op, env={})
        self.assertEqual(cost, 0.5)
        self.assertIn(b"bid,ask", out.read_bytes())
        self.assertEqual([r.get_method() for r in op.reqs], ["GET", "POST"])


if __name__ == "__main__":
    unittest.main()
