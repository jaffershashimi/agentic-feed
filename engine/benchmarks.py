"""What the validated backtest said the strategy should do, so the forward test can be held against it (model monitoring).

Source for every number: docs/ENGINE.md "First results" (S-042 V3 as deployed, 10 positions, 1,000 USD each, 2017 to Nov 2026, 1,822 trades), produced by
`python3 -m engine.runner backtest`. A benchmark changes only when the backtest is rerun and the new result is recorded in docs/ENGINE.md.
"""

S042 = {
    "tag": "s042v3",
    "backtest_trades": 1822,
    "mean_ret_pct_at_10am_scenario": 0.228,   # marketable limit about 10:00 ET, 9 bps per side: the slot S-042 now trades
    "mean_ret_pct_at_5bps": 0.255,            # what the old backtests assumed
    "modeled_cost_bps_per_side": 9.0,         # the execution cost that scenario assumed (morning marketable limits)
    "modeled_cost_bps_by_mode": {"morning_limit": 9.0, "market_at_open": 32.0},   # 32 = the measured half-spread scenario for market orders at the open (legacy mode, before 2026-11-18)
    "mean_ret_pct_market_at_open": -0.285,    # what the same backtest said legacy market-at-open execution earns (information only: that mode is retired)
    "go_live_min_closed_trades": 100,         # owner's go-live gate for any strategy
}
