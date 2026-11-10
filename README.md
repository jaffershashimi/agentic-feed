# agentic-feed

[![ci](https://github.com/jaffershashimi/agentic-feed/actions/workflows/ci.yml/badge.svg)](https://github.com/jaffershashimi/agentic-feed/actions/workflows/ci.yml)
![python 3.12](https://img.shields.io/badge/python-3.12-3776AB)
![dependencies none](https://img.shields.io/badge/dependencies-none-6EA8FE)
![paper only](https://img.shields.io/badge/mode-paper%20only-F2C94C)
![market data only](https://img.shields.io/badge/data-market%20data%20only-2ea44f)

The public, reproducible half of Agentic Lab, an AI-run paper-trading research system. It holds the market-data feed and the **pinned research tools**: a deterministic replay engine, a ledger checker, a score check and a paired comparison. Every tool is pinned by commit and sha256 so a result can be re-run by anyone and match byte for byte.

**Honest status (2026-11-10):** paper trading and simulation only. No strategy has shown an edge after costs yet, and the project says so in writing. The value here is the method: rules frozen before testing, results never pooled across paper, replay and backtest, small samples labeled as such, and failed ideas kept on record.

## The feed

A small, public, **market-data-only** feed built from free public sources by scheduled GitHub Actions.

- `feed/desk-feed.json`: compact machine-readable feed (schema `agentic-feed/1`, at most 6,000 bytes).
- `feed/desk-feed.md`: the same content for people.
- `feed/health.json`: per-source status, `as_of` dates and errors.

Raw URLs:

- https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/desk-feed.json
- https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/health.json

## System state for scheduled runs

How Agentic Lab runs today (roles, schedule, rules, pinned tools, how results are judged): [system/OPERATING_STATE.md](system/OPERATING_STATE.md). Rule texts: [system/rules/](system/rules/).

## What this repository never contains

Account balances, positions, orders, broker identifiers, personal records, private site addresses, scheduler identifiers or secrets. `scripts/privacy_check.py` enforces a pattern guard in CI and before every publish. Free-tier API keys, when added, live only in Actions secrets.

## Run locally

Python 3.12 or newer, standard library only:

```
python3 -m unittest discover -s tests -t .
python3 scripts/privacy_check.py
python3 -m feed_pipeline.build --out feed
```

## Honesty rules

Values carry their source and date. Missing data is `null` and listed in `data_health`, never guessed. The `regime` and `macro` labels are v0 heuristics, not validated signals. See `docs/SIGNAL_SCORECARD.md`. Nothing here is investment advice or a guarantee of any result.

Docs:
- `docs/FEED_SCHEMA.md`
- `docs/DESK_CONSUMER.md` (how a consumer must read the feed)
- `docs/DATA_SOURCES.md`
- `docs/SIGNAL_SCORECARD.md`
- `docs/SCORE_CHECK.md` (does a higher entry score lead to better trades?)
- `docs/REPLAY_R2.md` (deterministic replay of frozen rules R2 over saved bars)
- `docs/COMPARE_ARMS.md` (paired R2 vs R3 and tag comparison, Holm-adjusted)
- `docs/RECORDS_STORE.md` (append-only, hash-chained copy of the paper records; detects and undoes a stale overwrite)
- `docs/REPLAY_R2.md`, section "Resolving open trades" (`--resolve`: closes replay trades that entered too late in a session to get an exit)
