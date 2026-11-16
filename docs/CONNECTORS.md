# Connectors and MCP servers

How the desk can reach market data and tools without anyone pasting numbers by hand. Everything here is read-only research plumbing.
No connector can place a real order, and live trading stays paused. Paper orders on the Alpaca PAPER account go only through
`consumer/alpaca_paper.py`, which has its own kill switch, size limits and audit log.

## Alpaca MCP server (committed as `.mcp.json`)

The official server is [alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) (MIT, published on PyPI as `alpaca-mcp-server`).
It exposes Alpaca's API to any MCP client as tools. The server groups its tools into toolsets and reads which ones to load from
`ALPACA_TOOLSETS`. Toolsets it ships: `account`, `trading`, `watchlists`, `assets`, `stock-data`, `crypto-data`, `options-data`, `corporate-actions`,
`news`, `fixed-income-data`, `locates` (read from the package contents, not from a running server).

What we load and why:

| Setting | Value | Reason |
|---|---|---|
| `ALPACA_PAPER_TRADE` | `true` | paper host only |
| `ALPACA_TOOLSETS` | `account,assets,stock-data,options-data,corporate-actions,news` | read-only research tools |
| left out on purpose | `trading`, `watchlists`, `locates` | no order tools in the MCP server; orders stay in our own guarded code |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | `${...}` placeholders | read from the environment of whoever runs it, never stored in the repo |

Status: **configured but the server has not been run in this project.** The first launch attempt was blocked by the sandbox, and it was not worked
around. Before relying on it, run `uvx alpaca-mcp-server` once on a machine you control and confirm the tool list contains no order tools.
`tests/test_mcp_config.py` fails the build if someone adds the `trading` toolset, switches off paper mode, or commits a key.

## Other data connectors available to the Claude account

These exist as connectors in the Claude account the desk runs under. None is wired into a rule yet. Each line says what it could add and what to check first.

| Connector | Could add | Check first |
|---|---|---|
| Alpha Vantage | earnings calendar (skip names about to report), splits and dividends, listing status, technical indicators for cross-checks | free-tier rate limits; do not make it a hard dependency |
| FlashAlpha | option chains with Greeks, volatility risk premium, expected move, VIX state | paid tier limits; confirm whether history is point in time before any backtest |
| Financial Datasets, FMP, Twelve Data, TipRanks | fundamentals, estimates, news, insider and filing data | license terms before storing anything; none are point-in-time guaranteed |
| TradingView | alert hooks and Pine backtests for a second opinion | alerts are notifications only, they cannot trade |

Rule of thumb: a connector may feed a research question or a data-quality cross-check. It does not get to change a frozen rule or place an order.

## Not adopted

[OpenBB Open Data Platform](https://github.com/OpenBB-finance/OpenBB) and its MCP server: public sources disagree on the license of the MCP package (AGPL in some places,
permissive in others). Until that is checked in the repository's own LICENSE files, it is not added. See `docs/OPEN_SOURCE_LANDSCAPE.md`.
