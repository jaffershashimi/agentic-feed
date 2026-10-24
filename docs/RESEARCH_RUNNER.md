# Offline research runner

`feed_pipeline/research.py` replays the desk's documented +30% / −15% long-option structure over real S&P 500 and VIX history (Cboe CSVs). It is standard-library Python, read-only and offline, and it never places orders. It runs inside the existing publish workflow and writes `feed/research.json` (schema `research-run/1`).

## What it does

- **Data:** S&P 500 and VIX closes aligned by date. Malformed or non-positive rows are skipped, never filled.
- **Options:** priced with Black-Scholes from the VIX close: an at-the-money option with 21 sessions to expiry. These are modeled prices, not historical option quotes, so the data class is `HISTORICAL_UNDERLYING_MODELED_OPTIONS`.
- **Exits:** checked at each daily close for up to 10 sessions. A gap fills at the observed close, never at the level. Entry pays half the 4% spread, exit pays the other half, and fees are 1% of the debit.
- **Pre-registered rules** (none fitted):
  - cash;
  - a call every session;
  - a call only when the S&P 500 is above its 200-session average;
  - a put every session;
  - a put only when VIX is at least 25.
- **Chronological splits:** train 1990–2014, validation 2015–2020, holdout 2021 onwards. An outcome whose window crosses a split boundary is purged and counted.
- **Metrics per rule and split:**
  - count, win rate, mean net return and payoff ratio;
  - 5% and 1% tails, and the worst outcome;
  - exit mix;
  - a moving-block bootstrap 95% confidence interval for the mean, and whether it excludes zero;
  - max drawdown of the non-overlapping trade sequence at 10% allocation.
- **Uncertainty:** about one million bootstrap-resampled outcomes. They are reported as `scenarioCount`, separate from `uniqueObservationCount`; resamples are not new observations.
- **Tail stress:** a −7% overnight gap with IV ×1.6 on day one, applied to holdout entries. It is labeled `TAIL_STRESS_SYNTHETIC` and reported separately.

## Manifest and reproducibility

Every artifact carries:
- `runKey`, `codeCommit` and `codeHash`;
- `datasetHash` and the raw `sourceHashes`;
- `policyHash`, `strategyHash`, `costModelHash`, `seed`, `dataClass`, `availableAt` and `splits`;
- the unique-observation and scenario counts, `attemptedModelCount` and the attempted strategy hashes;
- metrics, coverage, limitations and `artifactHash`.

**Cache:** the run key hashes the dataset, code, policy, strategies, costs, splits, seed and resample count. When nothing changed, the previous artifact is reused unchanged, so no new evidence is manufactured.

**Failure handling:** writes are atomic (temporary file plus rename), a lock file prevents two runs at once, and a failed run leaves the previous artifact in place.

## Limits

- Modeled prices have no real bid/ask, skew, fills or assignment, and exits are checked at closes only.
- Index options are only a proxy for single-stock options.
- The holdout is reported once per code version and must not be used for tuning.
- A green run or a positive simulated mean is not a trading edge, and it changes no desk rule by itself.

`tests/test_research.py` covers:
- gap-through-stop, target, time and flat-market losses, and put/call symmetry;
- the manifest contract and split purging;
- cache reuse and invalidation;
- bad data and duplicate dates;
- the lock;
- atomic fail-soft behaviour on corrupt input.
