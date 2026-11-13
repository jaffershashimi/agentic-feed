# Rank and influence audit (TEST ONLY, descriptive)

`consumer/rank_audit.py` answers one question the pinned `score_check.py` cannot: for a fixed stock-only cohort, does the R2 entry score rank outcomes, and do a few trades or sessions drive any bucket? It implements the frozen designs R2_STOCK_ONLY_RANK_AUDIT_V3 and R2_TOP_BUCKET_INFLUENCE_AUDIT_V1 proposed by the Research Student on 2026-11-12 and 2026-11-13. It changes no rule, weight, threshold or policy and writes nothing to the ledger.

```
python3 -m consumer.rank_audit ledger.csv --rule-set R2 --since 2026-11-02 [--until DATE] [--label TEXT] [--json out.json]
```

## Cohort (printed with every result)

Mode REPLAY, instrument equity, the stock arm `REPLAY-B-<rule set>-`, source `REPLAY_<rule set>`, entry date in Pacific time within `--since` and `--until`. Every entered trade stays in the cohort with a status: CLOSED (real exit), OPEN (no exit row) or UNRESOLVED_CLOSED (the Nov 9 close-out, zero P&L, never averaged). Only CLOSED trades have a return: net USD = (exit - entry) x qty x multiplier - fees, return percent over entry notional.

## What it reports

- Per score bucket (6, 7, 8, 9 to 10): trades, entry sessions, symbols, mean, median, 10 percent trimmed and winsorized means, the share of the bucket's return sum carried by its top 1, 2 and 3 trades, and the range of the mean when each session (and each trade) is left out, with whether the sign is stable.
- Spearman rank correlation (average ranks for ties) with a 95 percent interval from resampling whole entry sessions (2,000 resamples, seed 7).
- The gap between the 9 to 10 bucket and the 8 bucket with the same session-cluster interval.
- Completeness by bucket: entries, closed, open, UNRESOLVED_CLOSED, closure share; and the average return the incomplete trades would need for the closed mean to reach zero.

## Limits

- It needs the ledger only. It does not read score parts, so it cannot report component variance; the checker's component split is still missing.
- Fixed as-of marks for open trades (last close) need bar data and are not computed here; the sign-flip figure is the model-free substitute.
- Descriptive. A bucket result is never a proof of ranking or of profit; small clustered samples prove little.
