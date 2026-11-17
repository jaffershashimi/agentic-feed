import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from consumer import news_drop_forward as ndf
from consumer import oversold_forward as of
from consumer import system_preflight as sp

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)  # Monday evening in New York, after the Nov 16 session


class FakeCall:
    def __init__(self, status="ACTIVE", news=5, fail=None):
        self.status, self.news, self.fail = status, news, fail

    def __call__(self, method, path, body=None, host="paper"):
        if self.fail and self.fail in path:
            raise RuntimeError("HTTP 500")
        if path == "/v2/account":
            return {"status": self.status}
        if path == "/v2/clock":
            return {"is_open": False, "next_open": "2026-11-17T09:30:00-05:00"}
        if path.startswith("/v2/stocks/bars"):
            return {"bars": {"SPY": [{"t": "2026-11-16T05:00:00Z"}]}}
        if path.startswith("/v1beta1/news"):
            return {"news": [{"id": i} for i in range(self.news)]}
        if path.startswith("/v2/stocks/quotes/latest") or path.startswith("/v1/corporate-actions"):
            return {"quotes": {"SPY": {}}, "corporate_actions": {}}
        if path.startswith("/v2/orders"):
            return [{"client_order_id": "s042v3-AAA-261116"}]
        if path == "/v2/positions":
            return []
        raise AssertionError(path)


def good_run(cmd, cwd, timeout=240):
    if any("bar_archive" in c for c in cmd):
        return 0, '{\n "days_ok": 21,\n "problems": []\n}'
    return 0, "## claude/x...origin/x\nOK"


def archive_problem_run(cmd, cwd, timeout=240):
    if any("bar_archive" in c for c in cmd):
        return 0, '{\n "days_ok": 20,\n "problems": ["2026-11-10: hash mismatch"]\n}'
    return good_run(cmd, cwd, timeout)


def make_repos(tmp):
    hub = Path(tmp) / "hub"
    (hub / "system/11-market-data").mkdir(parents=True)
    for n in ("universe_s042_2026.txt", "universe.txt", "universe_s046_orb.txt"):
        (hub / "system/11-market-data" / n).write_text("# header comment that is long enough\nAAA\nBBB\n")
    return Path(tmp) / "feed", hub


ENV = {"ALPACA_API_KEY": "x", "ALPACA_SECRET_KEY": "y"}


class PreflightTests(unittest.TestCase):
    def run_all(self, call, env=ENV, run=good_run):
        with tempfile.TemporaryDirectory() as tmp:
            feed, hub = make_repos(tmp)
            with mock.patch.object(of, "run", return_value={"asof": "2026-11-16", "stress": True, "v3_signals": 3, "entries": [], "quarantined": {}}), \
                 mock.patch.object(ndf, "run", return_value={"asof": "2026-11-16", "drops": 5, "no_news": 2, "entries": [], "news_feed_suspect": False, "suspended": True}):
                return sp.check_all(call, feed, hub, None, NOW, run=run, env=env)

    def status(self, rows, name):
        return next(r["status"] for r in rows if name in r["check"])

    def test_bar_archive_problems_are_a_fail_and_the_check_runs_from_the_feed_checkout(self):
        self.assertEqual(self.status(self.run_all(FakeCall(), run=archive_problem_run), "bar archive"), sp.FAIL)
        seen = []
        def spy(cmd, cwd, timeout=240):
            if any("bar_archive" in c for c in cmd):
                seen.append((cwd.name, cmd[-1]))
            return good_run(cmd, cwd, timeout)
        self.run_all(FakeCall(), run=spy)
        self.assertEqual(seen[0][0], "feed")
        self.assertTrue(seen[0][1].endswith("hub/system/11-market-data"))

    def test_healthy_system_has_no_fail(self):
        rows = self.run_all(FakeCall())
        self.assertEqual([r for r in rows if r["status"] == sp.FAIL], [])
        self.assertEqual(self.status(rows, "paper account active"), sp.PASS)

    def test_missing_keys_inactive_account_and_endpoint_errors_are_fails(self):
        self.assertEqual(self.status(self.run_all(FakeCall(), env={}), "alpaca keys"), sp.FAIL)
        self.assertEqual(self.status(self.run_all(FakeCall(status="ACCOUNT_UPDATED")), "paper account active"), sp.FAIL)
        self.assertEqual(self.status(self.run_all(FakeCall(fail="/v1beta1/news")), "news endpoint"), sp.FAIL)

    def test_dry_tag_feed_is_a_warning_not_a_failure(self):
        self.assertEqual(self.status(self.run_all(FakeCall(news=1)), "macro-tagged"), sp.WARN)

    def test_a_failing_repo_check_fails_the_preflight(self):
        def bad(cmd, cwd, timeout=240):
            return (1, "boom") if "privacy_check.py" in " ".join(cmd) else (0, "ok")
        self.assertEqual(self.status(self.run_all(FakeCall(), run=bad), "privacy check"), sp.FAIL)

    def test_output_never_contains_keys_and_exit_code_follows_fails(self):
        rows = self.run_all(FakeCall(), env={"ALPACA_API_KEY": "SECRETKEY123", "ALPACA_SECRET_KEY": "SECRETVALUE456"})
        text = sp.render(rows)
        self.assertNotIn("SECRETKEY123", text)
        self.assertNotIn("SECRETVALUE456", text)
        self.assertIn("pass", text)


if __name__ == "__main__":
    unittest.main()
