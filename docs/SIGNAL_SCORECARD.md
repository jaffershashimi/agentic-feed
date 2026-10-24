# Signal scorecard

A signal is promoted only when all of these hold:
- it improves out-of-sample, cost-aware results;
- its confidence interval excludes zero;
- it passed a pre-registered test with a chronological holdout and multiple-testing control.

Rejected and inconclusive signals stay listed: negative results are results.

| Signal | Status | Evidence |
|---|---|---|
| `regime` v0 rules | Informational only, not tested | None yet |
| `macro` v0 z-score composite | Informational only, not tested | None yet |
| VIX / realized-vol ratio | Context only, not tested as a filter | `docs/DESK_PROPOSAL.md` proposes it as a cost-based skip condition, not as a signal |
| `optionmath.option_structure` EV (P2-2) | Calculator, not tested | A stated EV threshold is an input, not evidence. It needs validation against realized option paths. |
| Long SPX call, every session, +30% / −15%, 21-day expiry, 10-day hold (research runner) | **Rejected** (Oct 24, 2026) | Holdout 2021 onward: n 1449, win rate 29.8%, mean net −5.6% (95% CI −8.2% to −2.8%). Modeled prices from VIX. |
| Same, only when SPX is above its 200-day SMA | **Rejected** (Oct 24, 2026) | Holdout: n 1157, win rate 29.0%, mean −6.0% (CI −10.0% to −3.5%). The trend filter did not rescue the structure. |
| Long SPX put, every session, same structure | **Rejected** (Oct 24, 2026) | Holdout: n 1449, win rate 26.9%, mean −7.6% (CI −9.8% to −4.9%) |
| Long SPX put only when VIX ≥ 25 | **Rejected** (Oct 24, 2026) | Holdout: n 193, win rate 25.4%, mean −13.4% (CI −18.8% to −6.9%) |

The research rows come from `feed/research.json` (run 3340c71e8597). That run had five pre-registered strategies including cash, a holdout reported once, a moving-block bootstrap and cross-split purging. Its limits are in `docs/RESEARCH_RUNNER.md`, and each later run is recorded on the `data` branch (`RESEARCH_LOG.md`).

No signal has been promoted. No performance improvement is claimed.
