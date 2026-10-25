import csv
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

from consumer.paper_ledger import COLUMNS, load
from consumer.score_check import analyze, main, parse_buckets, spearman

# Synthetic replay fills only: invented symbols, prices and ids.
FIELDS = COLUMNS + ("score",)


def trade(tid, day, score, entry, exit_, mode="REPLAY", fee="0", exit_score=None):
    common = dict(mode=mode, arm="B", trade_id=tid, symbol="DEMO", instrument="equity", qty="1", multiplier="1",
                  policy_hash="p", decision_id=tid, score=str(score))
    at = f"2026-11-{day:02d}T15:00:00+00:00"
    out = [dict(common, event_id=f"{tid}-E", kind="entry", occurred_at=at, quote_time=at, side="buy", price=str(entry),
                ref_price=str(entry), fees=fee)]
    if exit_ is not None:
        later = f"2026-11-{day:02d}T18:00:00+00:00"
        out.append(dict(common, event_id=f"{tid}-X", kind="exit", occurred_at=later, quote_time=later, side="sell",
                        price=str(exit_), ref_price=str(exit_), fees=fee))
        if exit_score is not None:
            out[0]["score"] = str(exit_score)
    return out


def write(tmp, rows):
    path = pathlib.Path(tmp) / "fills.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def run(rows, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        loaded, errors = load(write(tmp, rows))
    assert not errors, errors
    return analyze(loaded, resamples=400, **kw)


def ladder(sign, days=12):
    """Per day one trade per score 6..10; return rises (sign=1) or falls (sign=-1) with the score, plus day noise."""
    rows = []
    for d in range(1, days + 1):
        for s in range(6, 11):
            ret = sign * (s - 8) * 1.0 + ((d * 7 + s) % 5 - 2) * 0.1
            rows += trade(f"t{d}-{s}", d, s, 100, 100 + ret)
    return rows


class ScoreCheckTests(unittest.TestCase):
    def test_parse_buckets(self):
        self.assertEqual(parse_buckets("6,7,9-10"), [(6, 6, "6"), (7, 7, "7"), (9, 10, "9-10")])
        for bad in ("8,7", "6-8,8", "9-7"):
            with self.assertRaises(ValueError):
                parse_buckets(bad)

    def test_spearman_handles_ties_and_constants(self):
        ts = [{"score": s, "ret": r} for s, r in ((1, 1), (2, 2), (2, 2), (3, 3))]
        self.assertAlmostEqual(spearman(ts), 1.0)
        self.assertIsNone(spearman([{"score": 5, "ret": r} for r in (1, 2, 3)]))

    def test_rising_scores_rank(self):
        out = run(ladder(1))
        self.assertEqual(out["verdict"], "ranks")
        self.assertGreater(out["spearman_ci95"][0], 0)
        self.assertGreater(out["top_minus_next_return_pct"], 0)

    def test_falling_scores_are_inverted(self):
        out = run(ladder(-1))
        self.assertEqual(out["verdict"], "inverted")
        self.assertLess(out["spearman_ci95"][1], 0)
        self.assertEqual([b["trades"] for b in out["buckets"]], [12, 12, 12, 24])

    def test_too_few_sessions_is_insufficient(self):
        out = run(ladder(1, days=4))
        self.assertEqual(out["verdict"], "insufficient_evidence")
        self.assertEqual(out["sessions"], 4)

    def test_returns_are_net_of_fees(self):
        out = run(ladder(1) + trade("fee", 1, 8, 100, 101, fee="2"))
        eight = next(b for b in out["buckets"] if b["bucket"] == "8")
        self.assertEqual(eight["trades"], 13)
        self.assertLess(eight["mean_net_usd"], 0.2)

    def test_open_unknown_fee_and_mixed_score_trades_are_skipped(self):
        rows = ladder(1) + trade("open", 2, 9, 100, None) + trade("nofee", 2, 9, 100, 105, fee="")
        mixed = trade("mixed", 3, 9, 100, 104)
        mixed.insert(1, dict(mixed[0], event_id="mixed-E2", score="6"))
        mixed[2]["qty"] = "2"  # the exit closes both entries, so only the scores differ
        out = run(rows + mixed)
        self.assertEqual(out["skipped"], {"open": ["open"], "fees_unknown": ["nofee"], "score_missing_or_mixed": ["mixed"]})
        self.assertEqual(out["trades"], 60)

    def test_modes_are_never_pooled(self):
        rows = ladder(1) + [dict(r, mode="PAPER") for r in trade("p1", 1, 6, 100, 150)]
        self.assertEqual(run(rows)["trades"], 60)
        self.assertEqual(run(rows, mode="PAPER")["trades"], 1)

    def test_scores_outside_buckets_are_reported(self):
        out = run(ladder(1) + trade("low", 1, 4, 100, 99))
        self.assertEqual(out["unbucketed_scores"], [4.0])
        self.assertEqual(out["trades"], 60)

    def test_cli_prints_json_and_rejects_bad_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, ladder(1))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([str(path), "--resamples", "200"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buf.getvalue())["verdict"], "ranks")
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(main([str(path), "--buckets", "9,8"]), 2)


if __name__ == "__main__":
    unittest.main()
