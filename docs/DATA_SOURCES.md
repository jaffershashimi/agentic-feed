# Data sources

Status as of October 22, 2026. "Verify" means the terms, limits and history depth still need a dated check (issue P0-3) before anything beyond this feed depends on the source.

| Source | Series | Access | Used now | Status |
|---|---|---|---|---|
| Cboe index history CSVs (`cdn.cboe.com`) | VIX, VIX9D, VIX3M, VVIX, SKEW, SPX | Public CSV, no key | Yes; SPX is a core input | Worked from GitHub runners on Oct 22, 2026 (VIX family). Verify terms and update time |
| FRED official API (St. Louis Fed) | DGS2, DGS10, T10Y2Y, BAMLH0A0HYM2, NFCI, DTWEXBGS | Free API key in the `FRED_API_KEY` Actions secret | Yes, when the secret is set | The keyless `fredgraph.csv` returned an HTML page from GitHub runners on Oct 22, 2026, so it is a fallback only. Verify: ICE BofA series carry their own licensing notes on FRED |
| SEC EDGAR | Filings, Form 4, companyfacts | Free; 10 requests/s; User-Agent required | Not yet (P1-4) | Verify |
| FINRA | Short interest, Reg SHO short volume | Free files | Not yet (P1-5) | Verify |
| CFTC | Commitments of Traders | Free files | Not yet (P1-5) | Verify |
| Option chains | — | No reliable free official source identified | Not yet (P1-7) | Open question: the main data gap |
| Unofficial endpoints (Stooq, yfinance, Cboe delayed quotes JSON) | — | Unofficial | Never as a sole source | Fallback only, if terms permit |

Rules:
- Every fetch identifies itself with a User-Agent and backs off on failure.
- One source failing never stops the feed; it is reported in `health.json`.
- No source is scraped against its terms.
