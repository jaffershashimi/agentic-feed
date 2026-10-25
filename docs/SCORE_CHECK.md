# Entry-score check

`consumer/score_check.py` asks one question of a paper or replay ledger: do higher entry scores lead to better trades? It reads the same fills CSV as `paper_ledger.py` plus a `score` column on entry rows. It is standard-library Python, read-only and offline.

```
python3 -I -m consumer.score_check fills.csv --mode REPLAY --buckets 6,7,8,9-10
```

## What it reports

- One mode at a time (PAPER or REPLAY), never pooled.
- Only closed, valid trades with known fees and one consistent entry score count. Open trades, unknown fees, rejected lifecycles and missing or mixed scores are listed under `skipped`.
- Per score bucket: trades, distinct entry sessions, win rate, mean return % and mean net USD after fees.
- A 95% interval for each bucket's mean return, the Spearman rank correlation between score and return, and the gap between the top bucket and the one below it.
- The correlation in the earlier and later halves of the sessions, to see whether the direction holds over time.

## Why session-cluster intervals

Trades entered on the same day move together, so they are not independent evidence. The bootstrap redraws whole entry sessions, not single trades. With few sessions the intervals are wide and unstable, so the verdict stays `insufficient_evidence` below `--min-trades` (default 30) or `--min-sessions` (default 10).

## Verdicts

- `ranks`: the correlation interval is above zero.
- `inverted`: the interval is below zero.
- `no_rank_evidence`: the interval includes zero.
- `insufficient_evidence`: too few trades or sessions.

A verdict describes the ledger it was run on. It is not a trading edge and changes no desk rule.

## First run (Oct 19 to 23, 2026 replay, policy p2)

| Score | Trades | Sessions | Win rate | Mean return | 95% interval |
|---|---|---|---|---|---|
| 6 | 11 | 4 | 36% | -0.55% | -1.08% to -0.04% |
| 7 | 27 | 5 | 44% | +0.05% | -0.71% to +1.05% |
| 8 | 49 | 5 | 45% | -0.00% | -0.32% to +0.56% |
| 9-10 | 42 | 5 | 31% | -0.47% | -1.04% to -0.13% |

- Spearman -0.03 (interval -0.13 to +0.08): across all 129 closed trades, the score does not rank outcomes.
- Score 9-10 trailed score 8 by 0.46 points of return (interval -1.27 to -0.12).
- Verdict: `insufficient_evidence`, because there are only 5 sessions. One correlated week cannot settle it.

**Next step:** the score's parts are not in the ledger, so this check can say the score fails to rank but not which part is wrong. Logging each entry's score components with its decision would let the next run test them one at a time.
