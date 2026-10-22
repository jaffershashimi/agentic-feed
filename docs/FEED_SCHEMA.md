# Feed schema `agentic-feed/1`

Compact JSON with sorted keys, at most 6,000 bytes. All values are rounded. `null` means unavailable.

| Field | Meaning |
|---|---|
| `schema` | `agentic-feed/1` |
| `generated_at` | UTC build time |
| `session` | Last completed US equity session (ISO date) when built. All values are point-in-time as of this date. |
| `calendar` | Session calendar version used |
| `regime.label` | `calm_uptrend`, `neutral`, `stress` or `unavailable`. v0 rules, unvalidated. |
| `regime.reasons` | Inputs that produced the label |
| `vol.vix`, `vix9d`, `vix3m`, `vvix`, `skew` | Cboe index closes |
| `vol.term_3m_over_1m`, `term_9d_over_1m` | VIX3M/VIX and VIX9D/VIX. Below 1 means inverted. |
| `vol.spx_rv20` | S&P 500 20-session close-to-close realized volatility, annualized % |
| `vol.vix_over_rv20` | VIX divided by `spx_rv20` |
| `vol.exp_move_1d_pct`, `exp_move_5d_pct` | One-sigma index move implied by VIX, in % |
| `trend.spx`, `spx_sma200`, `above_sma200` | S&P 500 level versus its 200-session average |
| `macro.*` | 2y and 10y Treasury yields, 10y–2y spread, ICE BofA HY OAS, Chicago Fed NFCI, broad dollar index, and a v0 z-score composite (`score` > 0 means easier conditions, `tilt`) with its `missing` inputs |
| `events` | Next 10 calendar days: `market_closed`, `early_close`, `fomc_statement` (time in PT), `monthly_opex` |
| `data_health` | `core_ok` (VIX, VIX3M and the Cboe S&P 500 index present and fresh), plus `stale` and `failed` source names |
| `note` | Usage caveat |

`feed/health.json` lists each source's provider, `as_of`, `ok`, `stale`, error text and URL.

Schema changes bump the version. Consumers reject versions they don't know.
