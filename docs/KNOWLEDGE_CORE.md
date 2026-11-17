# Knowledge core

One index of the methods the trading system uses, where each lives, and how far it has been tested. A method earns a place here by being (a) written down as a frozen rule or tested code, (b) checked on sample data, and (c) recorded with the result, good or bad. Updated 2026-11-17.

## Source packs
| Pack | License | Where | Status |
|---|---|---|---|
| [day-trader-skill](https://github.com/jakeschincariol/day-trader-skill) by Jake Schincariol, 11 day-trading skills, pinned commit `3d0f4ef` | MIT | `third_party/day_trader_skill/` (unedited), notice in `third_party/NOTICE-day-trader-skill.md` | Reviewed 2026-11-17; code read for network, process and prompt risks (none found beyond public-data fetches and one `pdftotext` call in the Pelosi reader); 315 upstream tests pass in CI as a subprocess test |

## The eleven skills: what we kept, what we did not
| Skill | What it does | Decision | How it is used here |
|---|---|---|---|
| trader-scanner | Universe from Nasdaq Trader lists, daily setups (20-day closing high, bull flag, reclaimed 20-day, oversold bounce), intraday opening-range scan | METHOD + CODE (vendored) | Opening-range rule is S-046 (tested below). Daily setups are registry ideas S-048. Our universes stay point-in-time from Alpaca, not Nasdaq Trader's current list (survivorship) |
| trader-news | Yahoo headlines and SEC 8-K filings per ticker, tagged (earnings, analyst, deal, halt, regulatory) | METHOD | Tag categories mirror our S-044 subgroups. Our feed is Benzinga via Alpaca; SEC 8-K as a second source is an idea, not built |
| trader-entry | First 5-minute bar that CLOSES above the opening-range high; wicks do not count; one trigger per day; closed bars only | CODE (vendored) + our evaluator | `consumer/orb_study.breakout` implements the same trigger and is checked against the pack's TSLA reel |
| trader-exit | Stop at range midpoint (rounded away from entry), target in R, refuses risk under 0.05% of price, flat by 15:55 | CODE via adapter | `consumer/day_trade_kit.exit_plan` calls the upstream arithmetic; reproduces the reel (364.18 entry, stop 358.32, target 370.04) |
| trader-timemachine | 10,000 bootstrap replays of the rest of the day from the stock's own bars at that time of day plus a replay of the rule on every past session; break-even-aware verdict (1 in 1+R) | CODE via adapter | `day_trade_kit.time_machine` runs it on Alpaca bars; reproduces the pack's published numbers exactly (target first 29.38%, stop first 25.48%, 33 historical triggers). Sample runs recorded below |
| trader-risk | Gate: stop required, loss per trade, reward:risk, daily loss, trades per day, entry window, fresh-price rule, duplicates; never blocks an exit | METHOD + OWN CODE | `consumer/paper_risk.check_day_trade` (tests included). The fingerprinted verdict file is not copied |
| trader-budget | Size from the max loss, refuse rather than shrink when the stop is too wide | METHOD | Same logic as `paper_risk.stock_shares` |
| trader-broker | Ticket in plain words, explicit yes per order, Claude's Allow-once card, Alpaca MCP setup | NOT ADOPTED as a gate | Our paper account is delegated to the orchestrator and real orders are never placed by AI, so a per-order human yes would defeat the delegation. The idea of a plain-words ticket and "needs approval" on any real-money tool stays. Alpaca MCP: `docs/CONNECTORS.md` |
| trader-alerts | Stop, target and flat-by alerts, NYSE hours and early closes, free delayed quotes | METHOD | Our calendar covers 2025 to 2027 (`feed_pipeline/calendar.py`). Alerts for paper stops are the broker's own stop orders |
| trader-analyst | Earnings facts from SEC XBRL (revenue, margins, EPS, year on year) | IDEA | Possible data source for an earnings-reaction hypothesis (registry S-049); not built |
| trader-pelosi | Reads House Periodic Transaction Reports as filed | IDEA | Registry S-047. Public data with a delay of up to 45 days; needs a frozen spec and a point-in-time test before any use |

## Lessons the pack teaches that are now rules of thumb here
1. A setup that looks good on one stock for one day is usually a coin flip. The pack's own flagship example (TSLA ORB, 59 sessions) came out "close to a coin flip" and says so. We keep that honesty.
2. A bigger target needs to win less often to break even: 1 in (1+R), so 50% at 1R, 33% at 2R, 25% at 3R. Judge a plan against that, not against 50%.
3. When one bar touches both stop and target, count the stop. A gap through a stop fills worse than 1R.
4. Only closed bars count; a wick is not a signal; one trigger per setup per day.
5. Set stop and target before entry; never widen a stop.
6. Free delayed data can lag. Say so when minutes matter.
7. Upstream states FINRA removed the 25,000 USD pattern-day-trader minimum effective 2026-06-04 (Regulatory Notice 26-10) with a phase-in to 2027-10-20. We have not verified that; it does not matter for a paper account.

## Sample test record
| Date | Test | Result |
|---|---|---|
| 2026-11-17 | Upstream 315 tests on the vendored copy | pass (also run in our CI) |
| 2026-11-17 | Adapter parity: pack's TSLA fixture rewritten as Alpaca-style bars through `day_trade_kit.time_machine`, seed 19 | identical to the pack: 29.38 / 25.48 / 45.14 percent, 33 historical triggers, verdict "close to a coin flip" |
| 2026-11-17 | Time Machine on live Alpaca bars, 15 recent ORB triggers (TSLA, NVDA, AAPL, 5 each, seed 19) | ran without error; pooled target-first 30.1% vs stop-first 29.3%, mean R -0.007 (a coin flip), single-run verdicts noisy (7 lean target, 5 lean stop, 3 coin flip); details in the hub note `system/06-research/s046-orb-2026-11-17.md` |
| 2026-11-17 | Day-trade risk gate `paper_risk.check_day_trade` against the pack's reel numbers | pass (9 unit tests in `tests/test_paper_risk.py`) |
| 2026-11-17 | S-046 opening-range breakout, 37 liquid stocks, 2023 to 2026-11-13, realistic fills | **REJECTED.** All three variants lose about 0.06 R per trade after costs (pooled 2025 to 2026 interval for V1 -0.089 to -0.030); the breakout beats the buy-at-09:45 control (-0.215 R) but neither makes money |

## Maintenance
Re-vendoring a newer upstream commit means copying the folders unchanged, updating the pinned hash in `third_party/NOTICE-day-trader-skill.md` and `tests/test_third_party_day_trader.py`, and re-running the sample tests above. The Sunday scan checks upstream for new commits and only reports; adopting a change is a reviewed PR.
