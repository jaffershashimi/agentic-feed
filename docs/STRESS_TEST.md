# System stress test

First run 2026-11-17. Three parts: failure injection against the real tools, a live read-only preflight of everything the schedules depend on, and a forward projection of what the paper strategies are likely to earn. Reproduce with `python3 -m unittest tests.test_stress_system tests.test_system_preflight` and `python3 -m consumer.system_preflight`.

## 1. Failure injection (`tests/test_stress_system.py`, 17 scenarios, no network, no orders)
| Area | Scenario | Safe outcome |
|---|---|---|
| Data | rate-limit storm (HTTP 429) on the bar feed | clean error, exit code 2, no orders |
| Data | SPY bar missing for the as-of session | run refused |
| Data | every candidate has a bad bar | all quarantined, no orders |
| Data | corporate-action endpoint down | run continues, error flagged in the output |
| Data | **silent news outage (HTTP 200, nothing in it)** | **found a real hole, fixed:** every drop would have looked like a no-news drop. Now 6 or more drops with no headline at all is treated as a broken feed and nothing is entered |
| Broker | order fails halfway through a batch, then the run is repeated | each name sent exactly once, nothing duplicated |
| Broker | garbage position payload | stops before any order |
| Broker | live host or paper switch turned off | refused before a request is built |
| Concurrency | two runs back to back | each order once |
| Concurrency | S-042 and S-044F want the same name | the second one skips it |
| Concurrency | HALT file created between dry run and submit | submit refused |
| Scale | 300 simultaneous S-042 signals | at most 10 entries, deterministic ranking |
| Scale | 120 simultaneous S-044F drops | at most 6 entries, notional and risk inside the tier-2 limits |
| Scale | market-wide crash day (SPY down 7%) | S-044F stands aside, S-042 stays inside its cap |
| Scale | 12 positions already open (above the cap) | zero slots, no orders |
| Risk | day-trade gate and tier-2 limits | daily loss, trades per day, window, stop checks block as designed |

## 2. Live preflight (`consumer/system_preflight.py`, read only, no keys or balances shown)
Result on 2026-11-17: 19 pass, 2 warn (the dry macro-tag headline feed, and uncommitted work in the tree while it ran), 0 fail. It checks keys present (yes/no), paper host and account, kill switch, market clock, data freshness, news, overnight and corporate-action endpoints, queued orders by strategy, universe files, repo cleanliness, privacy and registry checks, the bar-archive check, and dry runs of S-042 and S-044F.

## 3. What the stress test found in the environment and the data
1. **Background processes do not survive.** The session runs in a virtual machine that is paused and restarted when idle; every process started with nohup died (four times in 24 hours). Fix: the headline watcher now has a `catchup` mode that reads the last days of headlines and SPY minute bars after the fact, and the 2:15 review runs that instead. The overnight spread sampler cannot be made persistent this way (it needs to run 8 PM to 4 AM ET); the first Dec 6 evening sessions will be observed live instead.
2. **The macro-tagged headline feed went dry.** Alpaca's news tagged to SPY, QQQ, USO, GLD, ITA, XLE, FXI and TLT returned about 1,000 items a month through October 2026 and about 4 now. The S-043 history is unaffected (it was pulled when the feed was full), but a live watcher that depends on those tags would never fire. The watcher now reads the untagged general feed and relies on its keyword and cluster rules. The preflight warns when the tag feed is dry.
3. **S-044F, run exactly as its protocol is written, loses money in its own backtest.** The protocol (5,000 USD cap, 6 open, 12% stop, highest dollar volume first, hold to session 10) was never backtested as implemented; only the group average was. Simulated at its real sizing over 2017 to 2026: 1,440 trades, -30,028 USD in total, worst month -10,931 USD, maximum drawdown -41,139 USD, 49% of months positive. Across all no-news drops the raw 10-day return is +1.03% but the market itself returned +0.89% over the same windows, so the "rebound" is mostly the market's own bounce (market-adjusted about +0.14%). The 6 queued entries were cancelled before any fill and the strategy is suspended in code (`SUSPENDED = True`).

## 4. Forward projection at the sizes in use (bootstrap of the strategies' own monthly results; paper dollars; not a promise)
| Strategy | Sizing | History 2017 to Nov 2026 | Next 12 months, median | 5th to 95th percentile | Chance of a loss |
|---|---|---|---|---|---|
| S-042 V3 | 1,000 USD per trade, at most 10 open | 2,071 trades, +2,115 USD total, worst month -2,660, max drawdown -4,154, 66% of months positive | +586 (using 2022 on) or +301 (all years) | -1,499 to +2,845 (2022 on); -2,452 to +2,455 (all years) | 32% to 40% |
| S-044F | 5,000 USD cap, at most 6 open | -30,028 USD total, max drawdown -41,139 | -1,234 | -20,439 to +16,605 | 55% (suspended) |

Read: S-042 at its frozen size is a near-zero-expectancy test with small dollars at stake. Two of the ten years lose money (2020 and 2022 at this sizing). It is worth observing; it is not a money machine.
