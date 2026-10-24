# Data sources and provider verification matrix (P0-3)

Checked October 24, 2026. This repository is public, so a source qualifies for the feed only when its terms allow publishing what we derive from it.

The decisions are:
- **use**: in the public feed;
- **internal**: inputs to a published derived value, with the raw value never published;
- **consumer-side**: only in private desk runs through the consumer's own licensed connectors, never in this repository;
- **skip**.

| Source | Series | Access, limits | History | Terms checked | Decision |
|---|---|---|---|---|---|
| Cboe index history CSVs (`cdn.cboe.com`) | SPX (closes only), VIX, VIX9D, VIX3M, VVIX, SKEW | Public CSV, no key; works from GitHub runners (Oct 22 and 24) | VIX from 1990 | The public website terms were not readable from here. Cboe DataShop says redistributing its licensed EOD files needs a license. | **use, provisional (owner decision).** We publish the latest levels and derived statistics, never the history files. If the Cboe website terms prohibit even that, switch to derived-only (ratios, percentiles, realized vol). |
| FRED API: Treasury constant maturities | DGS2, DGS10, T10Y2Y | Free key (`FRED_API_KEY` secret) | Decades | US government data (Fed H.15), public domain | **use** |
| FRED API: Chicago Fed | NFCI, ANFCI | Same key | From 1971 | Free with attribution to the Chicago Fed | **use** |
| FRED API: Fed Board | DTWEXBGS | Same key | From 2006 | Public domain (H.10) | **use** |
| FRED API: ICE BofA indices | BAMLH0A0HYM2 | Same key | From 1996 | FRED series notice: ICE Data Indices; "for your internal use only", reproduction needs ICE's written permission | **internal.** It is one input to the v0 macro composite, and the raw value is no longer published (removed Oct 24, 2026). Values committed before that date stay in git history. |
| Keyless `fredgraph.csv` | Any | No key | Same | Same as FRED | **skip.** It returned HTML from GitHub runners (Oct 22). |
| SEC EDGAR | Submissions, companyfacts, Form 4, 8-K | Free; 10 requests/s; a descriptive User-Agent is required | From 1993/2001 | US government, public domain; fair-access policy | **use** when P1-4 is built |
| CFTC Commitments of Traders | ES, NQ, VX, rates | Free files, weekly (Friday release, Tuesday data) | From 1986 | US government, public domain | **use** when P1-5 is built |
| US Treasury daily par yield CSV | Par curve | Free CSV | From 1990 | Public domain | **use** when P1-3 is built (a FRED-independent check) |
| FRED: Fed balance sheet and liquidity | WALCL, RRPONTSYD, WTREGEN, SOFR | Same key | Varies | Public domain (H.4.1, NY Fed) | **use** when P1-3 is built |
| FINRA | Bi-monthly short interest, Reg SHO daily short volume | Free downloads | Varies | Site terms favour personal use; redistribution of derived values is unverified | **consumer-side** until terms are confirmed |
| Option chains (any source) | Quotes, IV, OI | No free, official, redistributable source found | — | Commercial feeds (OPRA-derived) need licenses | **consumer-side.** Desk runs read chains through their own broker connectors. P1-7 is closed for the public feed. |
| Per-ticker daily prices (Alpha Vantage, Twelve Data, Tiingo, TradingView free tiers) | OHLCV | Keys and quotas | Varies | Free-tier terms generally prohibit redistribution | **consumer-side.** Per-ticker features run in the private desk. |
| Unofficial endpoints (Stooq, yfinance, Cboe delayed-quotes JSON) | — | Unofficial | — | No usable terms | **skip** |

Rules:
- Every fetch identifies itself with a User-Agent and backs off on failure.
- One source failing never stops the feed; it is reported in `health.json`.
- No source is scraped against its terms.
- No raw value from an internal-only source is published.
- Recheck this table at least once a year and whenever a source is added.
