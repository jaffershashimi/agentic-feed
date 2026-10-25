# How a consumer must read the feed

The feed is optional input. A consumer must work exactly as before when the feed is missing, stale, invalid or unhealthy. It is never a single point of failure.

## Read

1. Fetch `https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/desk-feed.json`. One request per run.
2. If the fetch fails, the JSON does not parse, or `schema` is not `agentic-feed/1`, ignore the feed.

## Use only if fresh

Use the feed only when **all** of these hold:

- `session` equals the last completed US equity session at read time. Before 16:00 ET (13:00 ET on early-close days), that is the previous trading day. Weekends and holidays roll back to the last trading day. So a Monday 05:52 PT read correctly uses Friday's session.
- `data_health.core_ok` is `true`.
- The encoded file is at most 6,000 bytes.

This replaces a flat "24 hours old" rule, which would wrongly reject every Monday-morning and post-holiday read.

## When the feed is used

- Treat each number as context with its stated meaning. `null` means unavailable. Never substitute zero or a guess.
- `regime` and `macro` are v0 heuristics. Do not use them as entry signals or to raise position size. Until `docs/SIGNAL_SCORECARD.md` promotes a signal with out-of-sample evidence, they are informational only.
- Useful today:
  - `vol.vix_over_rv20`, as an index-level richness check next to single-name IV/RV;
  - `vol.exp_move_1d_pct` and `vol.exp_move_5d_pct`, for index reachability context;
  - `events`, for calendar blackouts;
  - `vol.term_3m_over_1m`: below 1 means inverted, which is a stress flag.

## Backup channels

A Google Drive file or a Notion page may mirror the feed later. Until a backup channel is proven by a real scheduled run, the fallback is the consumer's existing method.

## Release context (FRED v2 supplement)

`feed/release-context.json` is checked separately with `consumer/check_release_context.py` (standard library, read-only). It prints one JSON line with `USED` or `IGNORED` and every reason, and it never fails the caller: an outage or a bad file is an `IGNORED` reason.

The snapshot is `USED` only if all of these hold:
- `schema` is `release-context/1`, `releaseId` is 221, `status` is `AVAILABLE` and `complete` is `true`.
- `receivedAt` has a timezone, is not after the decision time, and is at most 36 hours old (the disclosed collection-age threshold; `--max-age-hours` changes it).
- NFCI and ANFCI are both present, and neither has a future `availableAt`, `seriesUpdatedAt` or `observationDate`.
- Values are finite or `null`. A `null` stays `null` and is flagged `VALUE_UNAVAILABLE`.
- No series appears twice with different content (a mixed revision). An exact duplicate is harmless.

Weekly observations older than 14 days are flagged `STALE_OBSERVATION` but stay context. The output carries `snapshotSha256` so a private consumer can store the exact snapshot it saw.

`surprise` is always `UNKNOWN`: this feed has no consensus source. The values are financial-conditions background only, never an entry reason, a size change or a rule override, and a weekly observation date is not a live quote.

## Paper-desk ledger check

`consumer/paper_ledger.py` checks a private paper-trading fills CSV. It uses the standard library only, reads its input without writing, uses no network and talks to no broker.

**Columns:** event_id, mode, arm, trade_id, kind, occurred_at, quote_time, symbol, instrument, side, qty, price, ref_price, multiplier, fees, policy_hash, decision_id.

**Reported:** results per mode (PAPER or REPLAY, never mixed) and per arm. The statistics use accepted trades only, and are net after fees and in closing order.

**Rejected trades** (malformed lifecycles, excluded from all statistics):
- an inconsistent symbol, instrument, multiplier or policy hash;
- events without an entry;
- an exit before the entry;
- an oversell;
- a fill better than the quote it cites.

**Unmeasured trades:** any trade with an unknown fee. A blank fee means unknown, never 0.

**Rule breaches** (listed, but they don't remove a valid trade):
- a stop missing from the 0 to 5 minutes after entry;
- a stop looser than 15% for options or 20% for equity;
- a stop widened later;
- a stop recorded before the entry;
- a stale quote;
- concurrent positions;
- entries per Pacific day;
- entries after the first loss (listed under `notes` instead with `--no-first-loss-stop`, for a policy such as paper p2 that has no first-loss stop).

The settings must be finite and in range.

Alerts are notifications, never fills. Missing quotes between checks stay unresolved. Consumers download the checker pinned by commit, check its sha256, and run it with `python3 -I` on their own file; the ledger never comes to this repository. The same tool lives in the private Agentic-Lab repository.
