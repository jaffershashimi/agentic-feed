# Private City consumer guide

`agentic-feed` is a public, optional market/macro data publisher. A consumer must not treat this repository as the private City, a broker connector or an options-history vendor.

## Every consuming research run

- Pin the feed commit or record the SHA-256 of the exact file and the source/session dates.
- Follow `docs/DESK_CONSUMER.md`: reject stale, invalid or unhealthy data and preserve `USED`/`IGNORED` reasons; missing values stay missing.
- Keep current market observations, historical option quotes, owner-provided paper evidence, modeled options and synthetic paths in separate data classes.
- Save the derived run receipt in the consuming project's private City or private ledger, with the feed hash, code hash, unique run ID, coverage, exclusions and limitations. Read the same ID back. The public feed cannot write to that private store and does not carry its credentials.
- Never publish raw licensed provider data, account balances, positions, orders, private City records, API keys or account identifiers here.
- A public feed read is not proof a scheduled task has connector access, a citizen executed, a strategy has edge, or an order was placed. Keep all output research-only unless a separate, explicitly governed execution system is documented.

This checklist is a consumer contract, not a new market signal or trading rule.
