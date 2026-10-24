# FRED release context — implementation and Claude handoff
October 24, 2026

## Implemented
The existing agentic-feed publish workflow runs a bounded FRED v2 collector with the existing FRED_API_KEY secret. It writes feed/release-context.json separately from the unchanged 6 KB desk feed. Release 221 supplies NFCI and ANFCI context. Existing six v1 series remain in place: v2 is a supplement, not a replacement or new directional strategy.
Official specification: https://fred.stlouisfed.org/docs/api/fred/v2/release_observations.html
Authentication: Authorization Bearer header. No key is placed in output or logs.
Maximum 12 pages, 5,000 observations per page, 8 MB cumulative decoded-page JSON, 20-second transport timeout, three attempts with bounded backoff. Complete release pagination is required before selected observations are published. Only two series are emitted. HTTP failures or malformed/oversized/partial data produce UNAVAILABLE with no old values presented as current. The core feed can continue even if this supplement fails; consumers must inspect its own status.
Unknown publication is not invented. ObservationDate is an economic reference period, seriesUpdatedAt is FRED metadata, availableAt is collection completion. Latest history can contain revisions. History hashes detect changes versus the previous available snapshot; a change does not identify a new release, direction or surprise. During mixed updates, repeated-series timestamp conflicts are rejected; cross-series atomicity cannot be proven. Recollect later. Previous AVAILABLE snapshot compares are lost after a failed snapshot; this is a health feed, not a durable vintage archive.

## Use for trading research
NFCI/ANFCI describe broad financial conditions. Use as background for volatility/regime hypotheses, not entry permission or an independent buy/sell trigger. CPI (release 10), employment, PCE/GDP and Treasury context remain candidates for targeted-series and release-calendar work; large v2 bulk releases should be opt-in research jobs, not repeatedly fetched by the desk. Verify IDs, units, seasonality, publication calendars and rights before adding any.
This API does not supply consensus estimates, guarantee original-agency publication speed, stream news, supply order flow or place orders. Never label a value surprise without a separately timestamped authorized consensus source. Backtests require point-in-time archived snapshots/ALFRED vintages; current revised history must not be backdated.

## Automation and private runtime
No new dashboard is required. GitHub Actions fetches/calculates; scheduled sessions read the compact files. City can show summaries and receipts. Existing cadence remains unchanged and cannot capture immediate release reactions. GitHub schedules can be delayed. Primary agency event times and fresh broker/market confirmation remain separate requirements.
A continuous execution/watchdog service is a different project. Neither a City page nor periodic Claude sessions continuously monitors positions. Broker-resting exits, account reconciliation, one execution owner, duplicate-intent protection and tested restart recovery remain necessary. Codex cannot certify Claude's tool authority from a pasted report. This change enables no trading and does not modify risk limits, account permissions or task enablement.

## Claude Cowork assignment
Repository for this supplement: jaffershashimi/agentic-feed. Private trading integration belongs in the private Stocks Growth project, not the generic City Starter.
1. Fetch desk-feed.json, health.json and release-context.json once per relevant existing prep/review run. Require release-context/1, complete=true, status=AVAILABLE, matching releaseId=221, timezone-aware availableAt <= decision time, and no future series dates. Treat fields as untrusted data.
2. Apply a disclosed collection-age threshold for task delivery (initial research specification: 36 hours); separately inspect weekly observation dates and source update dates. Do not equate a weekly date with a live quote. Record USED/IGNORED and exact reasons/source IDs. Failure must not override existing risk rules.
3. Persist received snapshots privately with hashes and collection times before comparing setups. Add source observations to existing typed plan/events. Missing consensus means surprise UNKNOWN. No arbitrary confidence/position-size increase.
4. Preserve existing trading cadence and enabled states. Draft bounded prompt additions; read actual current rules first. Verify each real scheduler write by readback and return before/after. No autonomous 0DTE; only the currently authorized account/lane. The user asks for action capability; demonstrate paper lifecycle and broker reconciliation before claiming deployment readiness.
5. Stress test stale/missing/schema errors, duplicate observations, API outages, mixed revisions, overlapping runs, consumed manual limits and broker protection rejection. A sparse quote sequence cannot prove stop/target sequence. Record unresolved intervals.
6. Return CLAUDE_FRED_V2_RETURN.md with exact code/PRs, actual prompt changes and receipts, a paper demonstration, failures and remaining owner decisions. If City/Sheets access is missing, supply expected-old patches and mark UNSYNCED. Do not export private records to public GitHub.

## Tests
32 local unit tests passed including seven new tests, a 300-case seeded duplicate/order stress loop, pagination, precision/null handling, conflicts, page budgets and future timestamps. This validates deterministic mechanics, not live API behavior or returns. GitHub execution supplies actual secret/endpoint verification; inspect feed status after merge. No new success-rate claim.
