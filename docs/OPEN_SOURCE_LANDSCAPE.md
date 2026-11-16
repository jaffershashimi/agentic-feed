# Open-source landscape: what we adopted, what we did not

Date: 2026-11-16. Purpose: stop re-inventing wheels without importing risk. The rule for every row is the same: the desk is a paper research system
with a frozen-rule process, so a tool earns a place only if it makes evidence more honest or plumbing safer. A tool that promises profit does not.

Grades: **BUILT** = we wrote our own small, tested version of the idea (stdlib only, so CI needs nothing installed). **CONFIGURED** = in the repo, not yet run.
**LATER** = useful, not now. **REFERENCE** = read it, do not depend on it. **NO** = rejected.

## What we took from the ecosystem today

| # | Addition | Idea comes from | Grade | Where |
|---|---|---|---|---|
| 1 | Probabilistic and Deflated Sharpe, minimum track record, Probability of Backtest Overfitting, stationary bootstrap | Bailey and Lopez de Prado, [Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551); Bailey, Borwein, Lopez de Prado and Zhu, [Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) | BUILT | `consumer/robust_stats.py` |
| 2 | S-042 parameter-plateau sweep (36 configs) with DSR and PBO | same papers, applied to our own rule | BUILT | `consumer/oversold_robust.py` |
| 3 | Causal two-state HMM stress detector (filtered, never smoothed) | Hamilton, [regime switching](https://doi.org/10.2307/1912559); the lookahead trap is well known in the HMM literature | BUILT | `consumer/regime_hmm.py` |
| 4 | SVG tear sheet | layout idea from QuantStats and pyfolio tear sheets | BUILT | `consumer/tearsheet.py` |
| 5 | Data-quality gate and corporate-action look-ahead | Alpaca's own [corporate actions data endpoint](https://docs.alpaca.markets/reference/corporateactions-1) | BUILT | `consumer/data_gate.py` |
| 6 | Indicator cross-check against pandas reference formulas (the same Wilder RSI that ta, pandas-ta and TA-Lib implement) | the three libraries above; installing them was blocked, so the reference is plain pandas | BUILT | `tests/test_indicator_crosscheck.py` |
| 7 | Cost model: half spread plus square-root impact, and realised entry gap | Toth et al., [square-root impact](https://doi.org/10.1103/PhysRevX.1.021006) | BUILT | `consumer/cost_model.py` |
| 8 | Overnight quote monitor for the Dec 6 evening session | Alpaca [overnight feed](https://docs.alpaca.markets/docs/real-time-stock-pricing-data) | BUILT | `consumer/overnight_monitor.py` |
| 9 | Official Alpaca MCP server, read-only toolsets | [alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) (MIT) | CONFIGURED, not run | `.mcp.json`, `docs/CONNECTORS.md` |
| 10 | This decision record | | BUILT | this file |

## Projects looked at

| Project | What it is | Decision | Reason |
|---|---|---|---|
| [alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) | Official MCP server for Alpaca | CONFIGURED | MIT, toolsets let us leave out trading. Runtime not verified here. |
| [QuantConnect LEAN](https://github.com/QuantConnect/Lean) | Full event-driven engine, Apache-2.0 | LATER | Strong engine, but a large dependency and a second source of truth next to our records checkpoint. Revisit if we need tick-level or multi-asset realism. |
| [OpenBB Open Data Platform](https://github.com/OpenBB-finance/OpenBB) and its MCP server | Data aggregation layer | NO for now | Sources disagree on the MCP package license; verify before use. |
| [vectorbt](https://github.com/polakowo/vectorbt) | Fast vectorised backtesting on pandas and numba | REFERENCE | Fast for sweeps, but the license carries a use restriction, and our sweep already runs in minutes. Read the license before any use. |
| [backtrader](https://github.com/mementum/backtrader) | Event-driven Python backtester | REFERENCE | Mature, but it adds nothing our own engine lacks. |
| [zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) | Maintained fork of the Quantopian engine | REFERENCE | Same reasoning as backtrader. |
| [hmmlearn](https://github.com/hmmlearn/hmmlearn) | HMM fitting in Python | REFERENCE | We wrote a 2-state filtered version so CI stays dependency free. Use hmmlearn to cross-check the fit if we ever grow to 3 states. |
| [pandas-ta](https://github.com/twopirllc/pandas-ta), [ta](https://github.com/bukosabino/ta), TA-Lib | Indicator libraries | REFERENCE | Cross-checked by formula, not installed. |
| [QuantStats](https://github.com/ranaroussi/quantstats), [pyfolio](https://github.com/quantopian/pyfolio) | Tear sheets | REFERENCE | Their layout inspired ours; ours is a 300-line stdlib file. |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents), [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | LLM agents that argue about trades | NO | They produce persuasive text, not tested edge. Our own record shows score-style ranking did not rank outcomes. An LLM committee would add the same flaw with more cost. |
| [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Reinforcement learning for trading | NO | High overfitting risk with the small samples we have. Revisit only after 100+ forward trades exist. |

## What this does not claim

None of these additions finds new edge. They make the existing evidence harder to fool. Result of the first honest run (S-042 V3, details in the hub results note):
the edge survives a 36-config parameter sweep (all positive in the Judge and Final windows, all negative in Development), but the Deflated Sharpe at 42 ideas tried is 0.36,
so luck is not ruled out. That is why S-042 stays PAPER_OBSERVATION until the forward test has 100 closed trades.
