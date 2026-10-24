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
| `macro.*` | 2y and 10y Treasury yields, 10y–2y spread, Chicago Fed NFCI, broad dollar index, and a v0 z-score composite (`score` > 0 means easier conditions, `tilt`) with its `missing` inputs. The composite also uses the ICE BofA HY OAS internally; that raw value is not published, because FRED licenses ICE index data for internal use only |
| `events` | Next 10 calendar days: `market_closed`, `early_close`, `fomc_statement` (time in PT), `monthly_opex` |
| `data_health` | `core_ok` (VIX, VIX3M and the Cboe S&P 500 index present and fresh), plus `stale` and `failed` source names |
| `note` | Usage caveat |

`feed/health.json` lists each source's provider, `as_of`, `ok`, `stale`, error text and URL.

Schema changes bump the version. Consumers reject versions they don't know.

## `feed/vol-core.json` (`vol-core/1`)

This is a separate file, so `desk-feed.json` is unchanged. It covers the S&P 500 only until daily prices (P1-2) and our own option-implied-vol history (P1-7) exist. All values are computed from data on or before `session`; `null` means not enough history.

| Field | Meaning |
|---|---|
| `rv20_pct` | 20-session realized vol, annualized %, measured four ways: `close_to_close`, `parkinson`, `garman_klass`, `yang_zhang` |
| `garch11` | Gaussian GARCH(1,1) fitted by maximum likelihood with variance targeting on up to 1,000 daily returns. Gives `alpha`, `beta`, `persistence`, `long_run_vol`, forecast vols for 1, 5 and 21 days, and 1- and 5-day expected moves. Needs at least 250 returns |
| `iv_rv_ratio` | VIX divided by close-to-close RV20 |
| `vrp_vol_pts` | VIX minus RV20, in volatility points |
| `vrp_variance` | (VIX² − RV20²) / 100 |
| `iv_rank_252` | Where VIX sits in its own 252-session range, 0 to 100 |
| `iv_pctile_252` | Share of the last 252 sessions with VIX at or below today's level, 0 to 100 |
| `term_slope_3m_minus_1m` | VIX3M minus VIX |
| `term_slope_1m_minus_9d` | VIX minus VIX9D |
| `skew_index` | Cboe SKEW close |
| `exp_move_1d_pct`, `exp_move_5d_pct` | One-sigma S&P 500 moves implied by VIX, in % |
| `errors` | Sources that failed this run |
