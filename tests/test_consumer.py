import copy
import io
import json
import random
import unittest
from contextlib import redirect_stdout

from consumer.check_release_context import check, main

# Synthetic snapshot in the published shape; values are invented.
DECISION = "2026-10-24T12:52:00Z"


def snapshot(**over):
    def record(sid, value):
        return {"seriesId": sid, "observationDate": "2026-10-16", "value": value, "seriesUpdatedAt": "2026-10-21T12:34:00Z",
                "availableAt": "2026-10-24T11:30:00+00:00", "historyHash": "a" * 64, "changedSincePrevious": False}
    doc = {"schema": "release-context/1", "releaseId": 221, "status": "AVAILABLE", "receivedAt": "2026-10-24T11:30:00+00:00",
           "complete": True, "pages": 4, "records": [record("ANFCI", "-0.5"), record("NFCI", "-0.4")]}
    doc.update(over)
    return doc


def raw(doc):
    return json.dumps(doc).encode()


class ConsumerTest(unittest.TestCase):
    def test_a_fresh_complete_snapshot_is_used_as_context_only(self):
        r = check(raw(snapshot()), DECISION)
        self.assertEqual(r["status"], "USED", r["reasons"])
        self.assertEqual(r["surprise"], "UNKNOWN")
        self.assertEqual([s["seriesId"] for s in r["series"]], ["ANFCI", "NFCI"])
        self.assertEqual(r["collectionAgeHours"], 1.37)

    def test_each_failure_is_ignored_with_its_reason(self):
        cases = {
            "missing": b"",
            "not JSON": b"{oops",
            "schema": raw(snapshot(schema="release-context/2")),
            "releaseId": raw(snapshot(releaseId=10)),
            "status": raw(snapshot(status="UNAVAILABLE")),
            "incomplete": raw(snapshot(complete=False)),
            "older than": raw(snapshot(receivedAt="2026-10-22T23:00:00Z")),
            "after the decision": raw(snapshot(receivedAt="2026-10-24T13:00:00Z")),
            "timezone required": raw(snapshot(receivedAt="2026-10-24T11:30:00")),
            "no records": raw(snapshot(records=[])),
            "oversized": b" " * 64_001,
        }
        for reason, body in cases.items():
            r = check(body, DECISION)
            self.assertEqual(r["status"], "IGNORED", reason)
            self.assertTrue(any(reason in x for x in r["reasons"]), (reason, r["reasons"]))

    def test_future_dates_non_finite_values_and_unexpected_series(self):
        for field, value, reason in [("availableAt", "2026-10-25T00:00:00Z", "future"), ("seriesUpdatedAt", "2026-10-25T00:00:00Z", "future"),
                                     ("observationDate", "2026-10-30", "future"), ("value", "NaN", "finite"), ("seriesId", "CPIAUCSL", "unexpected")]:
            doc = snapshot()
            doc["records"][0][field] = value
            r = check(raw(doc), DECISION)
            self.assertEqual(r["status"], "IGNORED", field)
            self.assertTrue(any(reason in x for x in r["reasons"]), (field, r["reasons"]))

    def test_duplicates_and_mixed_revisions(self):
        doc = snapshot()
        doc["records"].append(copy.deepcopy(doc["records"][1]))
        self.assertEqual(check(raw(doc), DECISION)["status"], "USED", "an exact duplicate is harmless")
        revised = copy.deepcopy(doc["records"][1])
        revised.update(value="-0.3", seriesUpdatedAt="2026-10-22T12:00:00Z")
        doc["records"][-1] = revised
        r = check(raw(doc), DECISION)
        self.assertEqual(r["status"], "IGNORED")
        self.assertIn("NFCI appears twice with different content", r["reasons"])

    def test_missing_series_null_values_and_weekly_staleness_are_visible(self):
        doc = snapshot()
        del doc["records"][0]
        self.assertIn("missing series ANFCI", check(raw(doc), DECISION)["reasons"])
        doc = snapshot()
        doc["records"][0]["value"] = None
        r = check(raw(doc), DECISION)
        self.assertEqual(r["status"], "USED")
        self.assertEqual(r["series"][0]["flags"], ["VALUE_UNAVAILABLE"], "null stays null, never zero")
        old = snapshot()
        for rec in old["records"]:
            rec["observationDate"] = "2026-10-02"
        self.assertEqual(check(raw(old), DECISION)["series"][0]["flags"], ["STALE_OBSERVATION"])

    def test_overlapping_runs_agree_and_outages_never_fail_the_caller(self):
        body = raw(snapshot())
        self.assertEqual(check(body, DECISION), check(body, DECISION), "two runs on one snapshot give one record")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--file", "/nonexistent/release-context.json", "--decision-at", DECISION]), 0)
        r = json.loads(out.getvalue())
        self.assertEqual((r["status"], r["reasons"]), ("IGNORED", ["unreachable: FileNotFoundError"]))

    def test_seeded_mutations_never_raise(self):
        rng = random.Random(221)
        base = snapshot()
        junk = [None, "", "x", -1, 1e309, [], {}, True, "2026-13-40", "9999-01-01T00:00:00Z"]
        for _ in range(500):
            doc = copy.deepcopy(base)
            target = doc if rng.random() < 0.4 else rng.choice(doc["records"])
            target[rng.choice(list(target))] = rng.choice(junk)
            body = raw(doc) if rng.random() < 0.9 else raw(doc)[: rng.randrange(1, 200)]
            r = check(body, DECISION)
            self.assertIn(r["status"], ("USED", "IGNORED"))
            if r["status"] == "USED":
                self.assertEqual(r["reasons"], [])


if __name__ == "__main__":
    unittest.main()
