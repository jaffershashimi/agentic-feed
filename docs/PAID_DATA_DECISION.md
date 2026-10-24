# Paid-data decision (P6-1)

Decided October 24, 2026. **Recommendation: buy nothing now. Revisit only when a validated signal's measured lift exceeds the yearly cost by a wide margin.**

The rule (from the issue) is to buy only when the free pipeline shows a measurable gap and the backtested lift beats the cost at the actual account size. The desk trades a four-figure account. Any feed costing $300 a year needs about +30% a year of extra edge on $1,000 just to break even. No signal on `docs/SIGNAL_SCORECARD.md` has shown any out-of-sample edge yet, and the research runner's tested long-option rules are negative on the holdout.

| Candidate | What it adds | Rough cost | Gap it would fill | Measured lift | Decision |
|---|---|---|---|---|---|
| Polygon / Massive options | OPRA chains, history | Hundreds to thousands $/yr | P1-7 IV history, real fills for research | None measured | No |
| Databento | Tick and OPRA data | Usage-based, typically $100s+ | Same | None | No |
| ORATS | Option analytics, history | ~$1,000+/yr | IV rank, backtests on real quotes | None | No (the strongest candidate if P3 ever needs real option quotes) |
| Tradier | Broker API with chains | Account-tied | Chains | None | No (the desk already reads chains through its broker) |
| Alpaca SIP | Consolidated equity data | ~$100/month tier | Per-ticker prices | None | No |
| FlashAlpha paid | GEX, levels, VIX state | Subscription | P2-4 dealer proxies | None | No (the free tier is used consumer-side) |
| Unusual Whales | Flow | Subscription | Flow context | None | No |
| Cboe DataShop | Licensed EOD and history | Per file / license | Redistribution rights for Cboe data | None | No; revisit only if Cboe website terms block the current public use |
| Nasdaq Data Link | Assorted datasets | Varies | Varies | None | No |

The owner decides any purchase. This table recommends against all of them until evidence exists.
