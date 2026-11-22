<p align="center"><img src="docs/diagrams/banner.svg" alt="agentic-feed. Frozen rules, public tools, and a written record of every paper trade. Paper trading only. No real orders. Real trading stays off until the go-live gates pass." width="100%"></p>

The public, reproducible half of Agentic Lab: a small research system that tests trading rules on paper accounts, under limits written down before any trade, with every result kept on record.

**Paper trading only. No real orders. Real trading stays off until the go-live gates pass.**

[![ci](https://github.com/jaffershashimi/agentic-feed/actions/workflows/ci.yml/badge.svg)](https://github.com/jaffershashimi/agentic-feed/actions/workflows/ci.yml)
[![publish-feed](https://github.com/jaffershashimi/agentic-feed/actions/workflows/publish.yml/badge.svg)](https://github.com/jaffershashimi/agentic-feed/actions/workflows/publish.yml)

## System at a glance

```mermaid
%%{init: {"theme":"base","fontFamily":"Inter, Helvetica, Arial, sans-serif","themeVariables":{"fontFamily":"Inter, Helvetica, Arial, sans-serif","fontSize":"15px","primaryColor":"#F1F5F9","primaryTextColor":"#0F172A","primaryBorderColor":"#64748B","lineColor":"#64748B","edgeLabelBackground":"#F1F5F9","tertiaryColor":"#F1F5F9"},"htmlLabels":false,"flowchart":{"curve":"basis","nodeSpacing":20,"rankSpacing":26,"padding":14,"htmlLabels":false}}}%%
flowchart LR
  A("Market<br/>data"):::base --> B("Frozen<br/>rules"):::base --> C("Paper<br/>accounts"):::key --> D("Checks and<br/>audit"):::base --> E("Owner<br/>decides"):::ink
  classDef base fill:#F1F5F9,stroke:#64748B,stroke-width:1.5px,color:#0F172A;
  classDef key fill:#2563EB,stroke:#2563EB,stroke-width:1.5px,color:#FFFFFF;
  classDef warn fill:#F1F5F9,stroke:#B45309,stroke-width:2.5px,color:#0F172A;
  classDef ink fill:#0F172A,stroke:#64748B,stroke-width:1.5px,color:#FFFFFF;
```

<sub>Static copy: [docs/diagrams/glance.svg](docs/diagrams/glance.svg)</sub>

## What this is, and what it is not

| This is | This is not |
|---|---|
| A paper-trading research system with rules frozen before testing | A fund, a signal service or investment advice |
| Public, pinned tools anyone can re-run and match byte for byte | A claim that any strategy makes money (none has shown an edge yet) |
| A written record of every result, including the failures | A system where an AI can place a real order |

## Architecture

Market data feeds a rules engine. The engine sends orders only to Alpaca **paper** accounts. Every job writes a run log; a watchdog compares the day's expected jobs with that log and opens a GitHub Issue for anything missing or failed. A daily Claude audit reads the log and the Issues and posts one plain report to the owner's Board.

```mermaid
%%{init: {"theme":"base","fontFamily":"Inter, Helvetica, Arial, sans-serif","themeVariables":{"fontFamily":"Inter, Helvetica, Arial, sans-serif","fontSize":"15px","primaryColor":"#F1F5F9","primaryTextColor":"#0F172A","primaryBorderColor":"#64748B","lineColor":"#64748B","edgeLabelBackground":"#F1F5F9","tertiaryColor":"#F1F5F9"},"htmlLabels":false,"flowchart":{"curve":"basis","nodeSpacing":34,"rankSpacing":40,"padding":14,"htmlLabels":false}}}%%
flowchart TB
  DS("Data sources"):::base --> EN("Rules engine"):::key
  EN --> SP("Stock paper"):::base
  EN --> OP("Options paper"):::base
  SP --> RL("Run log"):::base
  OP --> RL
  RL --> WD("Watchdog"):::base
  WD --> IS("GitHub Issues"):::base
  RL --> AU("Claude audit"):::base
  IS --> AU
  AU --> BD("Board"):::ink
  classDef base fill:#F1F5F9,stroke:#64748B,stroke-width:1.5px,color:#0F172A;
  classDef key fill:#2563EB,stroke:#2563EB,stroke-width:1.5px,color:#FFFFFF;
  classDef warn fill:#F1F5F9,stroke:#B45309,stroke-width:2.5px,color:#0F172A;
  classDef ink fill:#0F172A,stroke:#64748B,stroke-width:1.5px,color:#FFFFFF;
```

<sub>Static copy: [docs/diagrams/architecture.svg](docs/diagrams/architecture.svg)</sub>

## Daily timeline

Times in Pacific with Eastern beside them. Every job checks the New York clock and the market calendar in its own code, so daylight-time changes and early closes do not move it.

```mermaid
%%{init: {"theme":"base","fontFamily":"Inter, Helvetica, Arial, sans-serif","themeVariables":{"fontFamily":"Inter, Helvetica, Arial, sans-serif","fontSize":"15px","primaryColor":"#F1F5F9","primaryTextColor":"#0F172A","primaryBorderColor":"#64748B","lineColor":"#64748B","edgeLabelBackground":"#F1F5F9","tertiaryColor":"#F1F5F9"},"htmlLabels":false,"flowchart":{"curve":"basis","nodeSpacing":34,"rankSpacing":44,"padding":14,"htmlLabels":false}}}%%
flowchart TB
  T1("Pre-open check<br/>5:31 PT<br/>8:31 ET"):::base --> T2("Open check<br/>6:37 PT<br/>9:37 ET"):::base --> T3("Stock engine<br/>7:02 PT<br/>10:02 ET"):::key --> T4("Options engine<br/>7:47 PT<br/>10:47 ET"):::key --> T5("Risk monitor<br/>hourly from 8:47<br/>to 11:47 PT"):::base --> T6("Pre-close check<br/>12:45 PT<br/>15:45 ET"):::base --> T7("After-close<br/>1:35 PT<br/>16:35 ET"):::base --> T8("Reconcile<br/>2:47 PT<br/>17:47 ET"):::base --> T9("Run audit<br/>3:23 PT<br/>18:23 ET"):::ink
  classDef base fill:#F1F5F9,stroke:#64748B,stroke-width:1.5px,color:#0F172A;
  classDef key fill:#2563EB,stroke:#2563EB,stroke-width:1.5px,color:#FFFFFF;
  classDef warn fill:#F1F5F9,stroke:#B45309,stroke-width:2.5px,color:#0F172A;
  classDef ink fill:#0F172A,stroke:#64748B,stroke-width:1.5px,color:#FFFFFF;
```

<sub>Static copy: [docs/diagrams/timeline.svg](docs/diagrams/timeline.svg)</sub>

| Job | PT | ET |
|---|---|---|
| Options pre-open check (a failure keeps the day dry) | 5:31 | 8:31 |
| Options open check (quotes live, spreads sane) | 6:37 | 9:37 |
| Stock engine start | 7:02 | 10:02 |
| Options engine (backups 8:07 and 8:27 PT) | 7:47 | 10:47 |
| Watchdog, morning | 8:01 | 11:01 |
| Risk monitor, hourly (report only unless a frozen exit or halt fires) | 8:47 to 11:47 | 11:47 to 14:47 |
| Pre-close check | 12:45 | 15:45 |
| Watchdog, afternoon | 1:17 | 16:17 |
| Stock after-close | 1:35 | 16:35 |
| Options reconcile | 2:47 | 17:47 |
| Run audit (Board entry) | 3:23 | 18:23 |

## Risk guardrails

Layers from the smallest unit up. These are the options program's frozen limits for the 25,000 USD paper account, as written in [`consumer/opt_program/policy.py`](consumer/opt_program/policy.py). A frozen limit changes only by the owner's written decision.

```mermaid
%%{init: {"theme":"base","fontFamily":"Inter, Helvetica, Arial, sans-serif","themeVariables":{"fontFamily":"Inter, Helvetica, Arial, sans-serif","fontSize":"15px","primaryColor":"#F1F5F9","primaryTextColor":"#0F172A","primaryBorderColor":"#64748B","lineColor":"#64748B","edgeLabelBackground":"#F1F5F9","tertiaryColor":"#F1F5F9"},"htmlLabels":false,"flowchart":{"curve":"basis","nodeSpacing":34,"rankSpacing":44,"padding":14,"htmlLabels":false}}}%%
flowchart TB
  L1("Per structure<br/>max loss 1,000"):::warn --> L2("Per strategy<br/>halt at -1,500"):::warn --> L3("Per account<br/>open risk 10,000"):::warn --> L4("Account halt<br/>at -4,000"):::warn --> L5("Expiration week<br/>no short legs"):::warn --> L6("Real orders<br/>never by AI"):::ink
  classDef base fill:#F1F5F9,stroke:#64748B,stroke-width:1.5px,color:#0F172A;
  classDef key fill:#2563EB,stroke:#2563EB,stroke-width:1.5px,color:#FFFFFF;
  classDef warn fill:#F1F5F9,stroke:#B45309,stroke-width:2.5px,color:#0F172A;
  classDef ink fill:#0F172A,stroke:#64748B,stroke-width:1.5px,color:#FFFFFF;
```

<sub>Static copy: [docs/diagrams/guardrails.svg](docs/diagrams/guardrails.svg)</sub>

## Go-live gates

All five must pass on paper before real trading is even discussed. Then the owner decides, and only the owner can place a real order. No AI places, previews or approves a real order.

```mermaid
%%{init: {"theme":"base","fontFamily":"Inter, Helvetica, Arial, sans-serif","themeVariables":{"fontFamily":"Inter, Helvetica, Arial, sans-serif","fontSize":"15px","primaryColor":"#F1F5F9","primaryTextColor":"#0F172A","primaryBorderColor":"#64748B","lineColor":"#64748B","edgeLabelBackground":"#F1F5F9","tertiaryColor":"#F1F5F9"},"htmlLabels":false,"flowchart":{"curve":"basis","nodeSpacing":34,"rankSpacing":40,"padding":14,"htmlLabels":false}}}%%
flowchart TB
  G1("4 clean weeks"):::base --> G2("100+ closed trades"):::base --> G3("Positive after fees"):::base --> G4("Profit factor ≥ 1.2"):::base --> G5("Drawdown in limits"):::base --> G6("Owner decides"):::ink
  classDef base fill:#F1F5F9,stroke:#64748B,stroke-width:1.5px,color:#0F172A;
  classDef key fill:#2563EB,stroke:#2563EB,stroke-width:1.5px,color:#FFFFFF;
  classDef warn fill:#F1F5F9,stroke:#B45309,stroke-width:2.5px,color:#0F172A;
  classDef ink fill:#0F172A,stroke:#64748B,stroke-width:1.5px,color:#FFFFFF;
```

<sub>Static copy: [docs/diagrams/golive.svg](docs/diagrams/golive.svg)</sub>

## Failure monitoring loop

GitHub's scheduled jobs on this account start late or are dropped, so time-critical runs are started directly and each job checks its own time window in code before acting.

```mermaid
%%{init: {"theme":"base","fontFamily":"Inter, Helvetica, Arial, sans-serif","themeVariables":{"fontFamily":"Inter, Helvetica, Arial, sans-serif","fontSize":"15px","primaryColor":"#F1F5F9","primaryTextColor":"#0F172A","primaryBorderColor":"#64748B","lineColor":"#64748B","edgeLabelBackground":"#F1F5F9","tertiaryColor":"#F1F5F9"},"htmlLabels":false,"flowchart":{"curve":"basis","nodeSpacing":34,"rankSpacing":40,"padding":14,"htmlLabels":false}}}%%
flowchart TB
  F1("Late cron"):::warn --> F2("Claude starts run"):::key --> F3("Clock check"):::base --> F4("Watchdog compares"):::base --> F5("GitHub Issue"):::base --> F6("Daily audit"):::base --> F7("Fix and lesson"):::ink
  F7 -.-> F1
  classDef base fill:#F1F5F9,stroke:#64748B,stroke-width:1.5px,color:#0F172A;
  classDef key fill:#2563EB,stroke:#2563EB,stroke-width:1.5px,color:#FFFFFF;
  classDef warn fill:#F1F5F9,stroke:#B45309,stroke-width:2.5px,color:#0F172A;
  classDef ink fill:#0F172A,stroke:#64748B,stroke-width:1.5px,color:#FFFFFF;
```

<sub>Static copy: [docs/diagrams/failure-loop.svg](docs/diagrams/failure-loop.svg)</sub>

## Current status (paper)

Source: the Nov 22 paper review of the stock paper ledger, sessions 2026-11-02 to 2026-11-20 (15 sessions). Counts after fees. Paper results, not money.

| Measure | Value |
|---|---|
| Sessions on the record | 15 |
| Closed paper trades (two arms, kept separate) | 34 |
| Net after fees, both arms | -357 USD |
| Profit factor | 0.07 |
| Frozen-rule replay over the same sessions | 94 closed trades, mean -3.90 USD per trade, 95% interval -6.24 to -1.48 |
| Go-live gates met | 1 of 5 (drawdown only) |

**Plain reading: no edge so far.** The interval sits below zero before and after costs. The rules stay frozen and the test continues to 2026-12-18, when a pre-registered stop line decides whether the entry rules are retired. The options paper program starts 2026-11-23 with 0 trades on record.

## Repository map

| Path | What it holds |
|---|---|
| [`feed/`](feed/) | The public market-data feed (`desk-feed.json`, `health.json`) |
| [`feed_pipeline/`](feed_pipeline/) | Feed builder, market calendar, option math |
| [`engine/`](engine/) | Stock paper engine: health, gate, limits, desk pack |
| [`consumer/`](consumer/) | Paper tools, replay, checkers; [`consumer/opt_program/`](consumer/opt_program/) is the options program |
| [`scripts/`](scripts/) | Privacy check and research scripts |
| [`tests/`](tests/) | Unit tests (standard library only) |
| [`docs/`](docs/) | Method notes; [`docs/diagrams/`](docs/diagrams/) holds the diagrams on this page |
| [`system/`](system/) | Operating state and rule texts |

## How to verify this yourself

Python 3.12 or newer, standard library only.

```bash
git clone https://github.com/jaffershashimi/agentic-feed.git && cd agentic-feed
```

```bash
python3 -m unittest discover -s tests -t .
```

```bash
python3 scripts/privacy_check.py
```

```bash
grep -nE "^(MAX_LOSS_USD|OPEN_RISK_CAP_USD|STRATEGY_HALT_USD|ACCOUNT_HALT_USD)" consumer/opt_program/policy.py
```

```bash
python3 -m feed_pipeline.build --out /tmp/feed && cat /tmp/feed/health.json
```

## Honest limits

- Small samples prove nothing. 34 paper trades is not evidence of anything except that the current rules have not worked.
- Paper fills are not real fills. Paper accounts can be kinder than a real market on price and size.
- Paper, replay and backtest results are never pooled; each is reported on its own.
- The feed's `regime` and `macro` labels are simple heuristics, not validated signals.
- Nothing here is investment advice, and nothing here claims a profit.

## What this repository never contains

Account balances, positions, orders, fills, broker identifiers, personal records, keys or secrets. `scripts/privacy_check.py` enforces a pattern guard in CI and before every publish. Keys live only in GitHub Actions secrets. No license file has been chosen yet.

<details>
<summary>Feed details and raw URLs</summary>

- `feed/desk-feed.json`: compact machine-readable feed (schema `agentic-feed/1`, at most 6,000 bytes).
- `feed/desk-feed.md`: the same content for people.
- `feed/health.json`: per-source status, `as_of` dates and errors.

Raw URLs:

- https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/desk-feed.json
- https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/health.json

Values carry their source and date. Missing data is `null` and listed in `data_health`, never guessed. How Agentic Lab runs today: [system/OPERATING_STATE.md](system/OPERATING_STATE.md). Rule texts: [system/rules/](system/rules/).

</details>

<details>
<summary>All method docs</summary>

- `docs/FEED_SCHEMA.md`
- `docs/DESK_CONSUMER.md` (how a consumer must read the feed)
- `docs/DATA_SOURCES.md`
- `docs/SIGNAL_SCORECARD.md`
- `docs/SCORE_CHECK.md` (does a higher entry score lead to better trades?)
- `docs/REPLAY_R2.md` (deterministic replay of frozen rules R2 over saved bars)
- `docs/COMPARE_ARMS.md` (paired R2 vs R3 and tag comparison, Holm-adjusted)
- `docs/RANK_AUDIT.md` (stock-only rank and influence audit of the R2 score; descriptive, TEST ONLY)
- `docs/BAR_ARCHIVE.md` (testing-mode archive of SIP 5-minute bars with a hash per day; nothing reads from it yet)
- `docs/ALPACA_PAPER.md` (guardrailed Alpaca paper order tool: paper host only, dry run by default, kill switch)
- `docs/ROBUSTNESS_TOOLKIT.md` (Deflated Sharpe, overfitting probability, causal HMM regimes, tear sheets, data gate, cost model, overnight monitor)
- `docs/OPEN_SOURCE_LANDSCAPE.md` (what open-source tools and papers we adopted and what we rejected)
- `docs/NEWS_STRATEGIES.md` (news and hype strategies S-043 and S-044, live headline watcher, drop scanner, tier-2 paper sizing)
- `docs/STRESS_TEST.md` (failure-injection scenarios, live preflight, forward projection of the paper strategies)
- `docs/KNOWLEDGE_CORE.md` (index of the methods in use, the day-trader-skill pack decisions, and the sample test record)
- `docs/CONNECTORS.md` (read-only Alpaca MCP server and other data connectors)
- `docs/RECORDS_STORE.md` (append-only, hash-chained copy of the paper records; detects and undoes a stale overwrite)
- `docs/REPLAY_R2.md`, section "Resolving open trades" (`--resolve`: closes replay trades that entered too late in a session to get an exit)

</details>
