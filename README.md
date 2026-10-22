# agentic-feed

A small, public, **market-data-only** research feed built from free public sources by scheduled GitHub Actions.

- `feed/desk-feed.json`: compact machine-readable feed (schema `agentic-feed/1`, at most 6,000 bytes).
- `feed/desk-feed.md`: the same content for people.
- `feed/health.json`: per-source status, `as_of` dates and errors.

Raw URLs:

- https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/desk-feed.json
- https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/health.json

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
