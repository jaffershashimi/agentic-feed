# Paired rule and tag comparison

`consumer/compare_arms.py` asks whether a rule change (R2 vs R3) or a tag (binary event, options checklist) really changes outcomes in a paper or replay ledger. It is standard-library Python, read-only and offline. It reads the same fills CSV as `paper_ledger.py` and imports its loader and `score_check.trades`.

```
python3 -I -m consumer.compare_arms ledger.csv --base R2 --test R3 --sessions replay_sessions.csv --tags variant_tags.csv
```

## Paired rules

A setup is one decision: arm side (A or B), symbol and entry time (PT, to the minute). Every setup either rule set traded is compared with itself:

- difference = test net minus base net;
- a rule set that did not trade the setup scores 0 (cash), so avoided losses and missed gains both count;
- an open, unresolved, rejected or fee-unknown trade makes the pair unknown and leaves it out;
- only (session, symbol) pairs that both rule sets replayed count. Pass `--sessions` with the replay session keys (`RULE|YYYY-MM-DD|SYM`); without it, sessions where both rule sets traded are used.

The rule set comes from the arm: `REPLAY-A-R2-NVDA` is side A, R2; an arm without one (`REPLAY-B-NVDA`) is R1. Results are reported for all pairs and per instrument, in USD and in return points.

## Tag splits

With `--tags` (`trade_id,rule_set,binary_event,options_checklist,...`), per rule set and instrument: mean net of closed trades tagged YES minus NO (`binary_event`) and PASS minus FAIL (`options_checklist`, options only).

## Statistics

- Intervals and p-values come from a session-cluster bootstrap (whole entry sessions are resampled; same-day trades are not independent).
- Every test in the run is Holm-adjusted (`p_holm`), because many splits are tested at once and one will look significant by chance.
- Verdicts: `insufficient_evidence` below `--min-pairs` 30 (or `--min-trades` 15 per tag group) or `--min-sessions` 10; otherwise `test_better` / `base_better` / `no_difference` for pairs and `higher` / `lower` / `no_difference` for tags, using the adjusted p-value and the interval.

A verdict describes this ledger only. It is not a trading edge, and it changes no rule: only the owner turns a result into a rule, ideally after a pre-registered holdout.
