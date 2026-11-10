import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from consumer import records_store as rs

HEADER = ["event_id", "mode", "qty", "price"]


def write_csv(path, rows, header=HEADER):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def mk_rows(a, b):
    return [[f"E{i:04d}", "PAPER", "1", f"{100 + i}.5"] for i in range(a, b)]


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.live = self.dir / "ledger.csv"
        self.store = self.dir / "store"

    def sync(self, created="2026-11-10T00:00:00Z"):
        return rs.sync(self.live, self.store, None, created)

    def rec(self, n):
        return self.store / f"seg-{n:06d}.json"

    def seg(self, n):
        return self.store / f"seg-{n:06d}.csv"


class SyncTests(StoreCase):
    def test_first_sync_writes_everything(self):
        write_csv(self.live, mk_rows(0, 10))
        rep, code = self.sync()
        self.assertEqual(code, 0)
        self.assertEqual(rep["action"], "wrote")
        self.assertEqual(rep["store_rows"], 10)
        self.assertTrue(self.seg(1).exists() and self.rec(1).exists())

    def test_second_sync_adds_only_new_rows(self):
        write_csv(self.live, mk_rows(0, 10))
        self.sync()
        write_csv(self.live, mk_rows(0, 14))
        rep, code = self.sync()
        self.assertEqual((code, rep["wrote"]["rows"]), (0, 4))
        self.assertEqual(rep["store_rows"], 14)
        self.assertEqual(rep["segments"], 2)

    def test_no_change_writes_nothing(self):
        write_csv(self.live, mk_rows(0, 5))
        self.sync()
        before = sorted(p.name for p in self.store.iterdir())
        rep, code = self.sync()
        self.assertEqual((code, rep["action"]), (0, "none"))
        self.assertEqual(before, sorted(p.name for p in self.store.iterdir()))

    def test_never_edits_existing_files(self):
        write_csv(self.live, mk_rows(0, 5))
        self.sync()
        first = (self.seg(1).read_bytes(), self.rec(1).read_bytes())
        write_csv(self.live, mk_rows(0, 9))
        self.sync()
        self.assertEqual(first, (self.seg(1).read_bytes(), self.rec(1).read_bytes()))

    def test_deterministic_for_same_input(self):
        write_csv(self.live, mk_rows(0, 6))
        self.sync()
        a = (self.seg(1).read_bytes(), self.rec(1).read_bytes())
        other = self.dir / "s2"
        rs.sync(self.live, other, None, "2026-11-10T00:00:00Z")
        self.assertEqual(a, ((other / "seg-000001.csv").read_bytes(), (other / "seg-000001.json").read_bytes()))

    def test_receipt_chain_links_previous(self):
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        write_csv(self.live, mk_rows(0, 6))
        self.sync()
        r2 = json.loads(self.rec(2).read_text())
        self.assertEqual(r2["prev_receipt_sha256"], rs.sha256_bytes(self.rec(1).read_bytes()))
        self.assertEqual(json.loads(self.rec(1).read_text())["prev_receipt_sha256"], rs.GENESIS)


class DetectionTests(StoreCase):
    def test_stale_overwrite_is_detected_and_refused(self):
        # The Nov 3 failure: the live file is replaced by an older copy with rows missing.
        write_csv(self.live, mk_rows(0, 400))
        self.sync()
        write_csv(self.live, mk_rows(0, 37))
        rep, code = self.sync()
        self.assertEqual(code, 3)
        self.assertEqual(rep["lost_rows"], 363)
        self.assertEqual(rep["action"], "refused")
        self.assertEqual(rep["segments"], 1)  # nothing new written

    def test_stale_overwrite_can_be_restored(self):
        write_csv(self.live, mk_rows(0, 50))
        self.sync()
        write_csv(self.live, mk_rows(0, 20))
        out = self.dir / "restored.csv"
        rep, code = rs.restore(self.store, "event_id", out)
        self.assertEqual(code, 0)
        header, rows = rs.read_table(out)
        self.assertEqual(rows, mk_rows(0, 50))

    def test_changed_row_detected(self):
        write_csv(self.live, mk_rows(0, 5))
        self.sync()
        rows = mk_rows(0, 5)
        rows[2][3] = "999.9"
        write_csv(self.live, rows)
        rep, code = rs.reconcile(self.live, self.store)
        self.assertEqual((code, rep["changed_rows"], rep["lost_rows"]), (3, 1, 0))
        self.assertEqual(rep["changed_sample"], ["E0002"])

    def test_unsynced_rows_are_not_a_problem(self):
        write_csv(self.live, mk_rows(0, 5))
        self.sync()
        write_csv(self.live, mk_rows(0, 8))
        rep, code = rs.reconcile(self.live, self.store)
        self.assertEqual((code, rep["unsynced_rows"]), (0, 3))

    def test_tampered_segment_detected(self):
        write_csv(self.live, mk_rows(0, 5))
        self.sync()
        self.seg(1).write_text(self.seg(1).read_text().replace("100.5", "101.5"))
        rep, code = rs.verify(self.store, "event_id")
        self.assertEqual(code, 3)
        self.assertIn("SEGMENT_HASH", [p["code"] for p in rep["store_problems"]])

    def test_deleted_middle_receipt_is_a_gap(self):
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        write_csv(self.live, mk_rows(0, 6))
        self.sync()
        write_csv(self.live, mk_rows(0, 9))
        self.sync()
        self.rec(2).unlink()
        self.seg(2).unlink()
        rep, code = rs.verify(self.store, "event_id")
        codes = [p["code"] for p in rep["store_problems"]]
        self.assertEqual(code, 3)
        self.assertIn("GAP", codes)
        self.assertIn("CHAIN_BROKEN", codes)

    def test_edited_receipt_breaks_chain(self):
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        write_csv(self.live, mk_rows(0, 6))
        self.sync()
        text = self.rec(1).read_text().replace('"rows": 3', '"rows": 3 ')
        self.rec(1).write_text(text)
        rep, code = rs.verify(self.store, "event_id")
        self.assertIn("CHAIN_BROKEN", [p["code"] for p in rep["store_problems"]])

    def test_damaged_store_blocks_sync(self):
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        self.seg(1).write_text("garbage\n")
        write_csv(self.live, mk_rows(0, 6))
        rep, code = self.sync()
        self.assertEqual((code, rep["action"]), (3, "refused"))
        self.assertFalse(self.seg(2).exists())

    def test_header_change_refused(self):
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        write_csv(self.live, [r + ["x"] for r in mk_rows(0, 4)], header=HEADER + ["extra"])
        rep, code = self.sync()
        self.assertEqual((code, rep["header_changed"]), (3, True))

    def test_tail_deletion_limit_is_documented(self):
        # Deleting the newest segment and receipt together is not visible from the chain.
        # The head value in the report is what a person compares across runs.
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        write_csv(self.live, mk_rows(0, 6))
        rep2, _ = self.sync()
        self.rec(2).unlink()
        self.seg(2).unlink()
        rep, code = rs.verify(self.store, "event_id")
        self.assertEqual(code, 0)
        self.assertNotEqual(rep["head"], rep2["head"])


class InputTests(StoreCase):
    def test_duplicate_key_in_live_table_is_an_error(self):
        write_csv(self.live, mk_rows(0, 3) + mk_rows(1, 2))
        with self.assertRaises(rs.StoreError):
            self.sync()

    def test_ragged_row_is_an_error(self):
        self.live.write_text("event_id,mode,qty,price\nE1,PAPER,1\n")
        with self.assertRaises(rs.StoreError):
            self.sync()

    def test_empty_file_is_an_error(self):
        self.live.write_text("")
        with self.assertRaises(rs.StoreError):
            self.sync()

    def test_unknown_table_needs_key(self):
        other = self.dir / "mystery.csv"
        write_csv(other, mk_rows(0, 2))
        with self.assertRaises(rs.StoreError):
            rs.sync(other, self.store)

    def test_custom_key_columns(self):
        write_csv(self.live, [["A", "x", "1", "1"], ["A", "y", "1", "1"]])
        rep, code = rs.sync(self.live, self.store, "event_id,mode", "2026-11-10T00:00:00Z")
        self.assertEqual((code, rep["store_rows"]), (0, 2))

    def test_restore_never_overwrites(self):
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        out = self.dir / "o.csv"
        out.write_text("keep")
        with self.assertRaises(rs.StoreError):
            rs.restore(self.store, "event_id", out)
        self.assertEqual(out.read_text(), "keep")

    def test_restore_refuses_damaged_store(self):
        write_csv(self.live, mk_rows(0, 3))
        self.sync()
        self.seg(1).write_text("garbage\n")
        rep, code = rs.restore(self.store, "event_id", self.dir / "o.csv")
        self.assertEqual((code, rep["action"]), (3, "refused"))
        self.assertFalse((self.dir / "o.csv").exists())

    def test_quoted_commas_survive_round_trip(self):
        rows = [["E1", "PAPER", "1", "a,b \"c\""]]
        write_csv(self.live, rows)
        self.sync()
        out = self.dir / "o.csv"
        rs.restore(self.store, "event_id", out)
        self.assertEqual(rs.read_table(out)[1], rows)


class CliTests(StoreCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rs.main(list(argv))
        return code, json.loads(buf.getvalue())

    def test_cli_exit_codes(self):
        write_csv(self.live, mk_rows(0, 5))
        code, rep = self.run_cli("sync", "--table", str(self.live), "--store", str(self.store))
        self.assertEqual(code, 0)
        code, rep = self.run_cli("reconcile", "--table", str(self.live), "--store", str(self.store))
        self.assertEqual((code, rep["lost_rows"]), (0, 0))
        write_csv(self.live, mk_rows(0, 2))
        code, rep = self.run_cli("reconcile", "--table", str(self.live), "--store", str(self.store))
        self.assertEqual((code, rep["lost_rows"]), (3, 3))
        code, rep = self.run_cli("sync", "--table", str(self.dir / "missing.csv"), "--store", str(self.store))
        self.assertEqual(code, 2)


class CheckpointTests(StoreCase):
    def cp(self, prev_rows=None, prev_digest=None, out=None):
        return rs.checkpoint(self.live, prev_rows, prev_digest, out, "2026-11-10T00:00:00Z")

    def test_baseline_without_previous(self):
        write_csv(self.live, mk_rows(0, 10))
        rep, code = self.cp()
        self.assertEqual((code, rep["status"], rep["rows"]), (0, "BASELINE", 10))

    def test_growth_is_ok(self):
        write_csv(self.live, mk_rows(0, 10))
        base, _ = self.cp()
        write_csv(self.live, mk_rows(0, 17))
        rep, code = self.cp(base["rows"], base["digest"])
        self.assertEqual((code, rep["status"], rep["grew_by"]), (0, "OK", 7))

    def test_unchanged_is_ok_with_zero_growth(self):
        write_csv(self.live, mk_rows(0, 10))
        base, _ = self.cp()
        rep, code = self.cp(base["rows"], base["digest"])
        self.assertEqual((code, rep["status"], rep["grew_by"]), (0, "OK", 0))

    def test_nov3_rollback_is_lost(self):
        write_csv(self.live, mk_rows(0, 400))
        base, _ = self.cp()
        write_csv(self.live, mk_rows(0, 37))
        rep, code = self.cp(base["rows"], base["digest"])
        self.assertEqual((code, rep["status"], rep["lost_rows"]), (3, "LOST", 363))

    def test_edited_old_row_is_changed(self):
        write_csv(self.live, mk_rows(0, 10))
        base, _ = self.cp()
        rows = mk_rows(0, 14)
        rows[3][3] = "1.0"
        write_csv(self.live, rows)
        rep, code = self.cp(base["rows"], base["digest"])
        self.assertEqual((code, rep["status"]), (3, "CHANGED"))

    def test_same_count_but_swapped_history_is_changed(self):
        write_csv(self.live, mk_rows(0, 10))
        base, _ = self.cp()
        write_csv(self.live, mk_rows(100, 110))
        rep, code = self.cp(base["rows"], base["digest"])
        self.assertEqual((code, rep["status"]), (3, "CHANGED"))

    def test_header_change_is_changed(self):
        write_csv(self.live, mk_rows(0, 5))
        base, _ = self.cp()
        write_csv(self.live, mk_rows(0, 9), header=["event_id", "mode", "qty", "px"])
        rep, code = self.cp(base["rows"], base["digest"])
        self.assertEqual((code, rep["status"]), (3, "CHANGED"))

    def test_line_endings_do_not_cause_a_false_alarm(self):
        write_csv(self.live, mk_rows(0, 10))
        base, _ = self.cp()
        self.live.write_bytes(self.live.read_bytes().replace(b"\n", b"\r\n"))
        rep, code = self.cp(base["rows"], base["digest"])
        self.assertEqual((code, rep["status"]), (0, "OK"))

    def test_problem_writes_nothing(self):
        write_csv(self.live, mk_rows(0, 10))
        base, _ = self.cp()
        write_csv(self.live, mk_rows(0, 4))
        out = self.dir / "cp.json"
        rep, code = self.cp(base["rows"], base["digest"], out)
        self.assertEqual(code, 3)
        self.assertFalse(out.exists())

    def test_writes_small_json_and_never_overwrites(self):
        write_csv(self.live, mk_rows(0, 6))
        out = self.dir / "cp.json"
        rep, code = self.cp(out=out)
        saved = json.loads(out.read_text())
        self.assertEqual((saved["rows"], saved["digest"]), (6, rep["digest"]))
        self.assertEqual(sorted(saved), ["created", "digest", "raw_sha256", "rows", "table"])
        with self.assertRaises(rs.StoreError):
            self.cp(out=out)

    def test_needs_both_previous_values(self):
        write_csv(self.live, mk_rows(0, 3))
        with self.assertRaises(rs.StoreError):
            self.cp(prev_rows=3)
        with self.assertRaises(rs.StoreError):
            self.cp(prev_digest="ab")

    def test_cli_checkpoint_round_trip(self):
        write_csv(self.live, mk_rows(0, 8))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rs.main(["checkpoint", "--table", str(self.live), "--out", str(self.dir / "a.json")])
        base = json.loads(buf.getvalue())
        self.assertEqual((code, base["status"]), (0, "BASELINE"))
        write_csv(self.live, mk_rows(0, 11))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rs.main(["checkpoint", "--table", str(self.live), "--prev-rows", str(base["rows"]),
                            "--prev-digest", base["digest"]])
        self.assertEqual((code, json.loads(buf.getvalue())["status"]), (0, "OK"))
        write_csv(self.live, mk_rows(0, 2))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rs.main(["checkpoint", "--table", str(self.live), "--prev-rows", str(base["rows"]),
                            "--prev-digest", base["digest"]])
        self.assertEqual((code, json.loads(buf.getvalue())["status"]), (3, "LOST"))


if __name__ == "__main__":
    unittest.main()
