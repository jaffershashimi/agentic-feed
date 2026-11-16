# News and hype strategies, and the learning loop

Added 2026-11-16 at the owner's request: new strategies that react to headlines (a geopolitical shock, a single stock that collapses on news), plus a loop that collects
data so the system learns which headlines matter. Everything is research or Alpaca PAPER only (BROKER_PAPER class, never pooled, never counts toward go-live). No real order is placed by AI.

## The strategies, as pre-registered tests
| Id | Idea | Spec (Agentic-Lab) | Study |
|---|---|---|---|
| S-043 | A cluster of geopolitical-shock headlines (for example a US strike on Iran): does SPY keep falling for the next hour or day, enough to profit from puts after costs? | `system/06-research/specs/S-043-geopolitical-shock-spy.md` | `consumer/news_shock_study.py` |
| S-044 | A stock that drops 8% or more: does a drop attached to news keep falling over 5 to 10 sessions (drift, so short or buy puts), while a no-news drop rebounds? (Chan 2003) | `system/06-research/specs/S-044-news-drop-drift.md` | `consumer/news_drop_study.py` |

The rules were written and merged BEFORE any return was computed. A result can license only a small paper observation; rule changes stay with the owner.

## The learning loop
* `consumer/news_watch.py`: polls Alpaca (Benzinga) headlines every minute for the S-043 symbol set, runs the frozen shock detector, and when an event fires logs it with the SPY quote and records SPY at +5, +15, +30 and +60 minutes. Read only.
* `consumer/news_drop_forward.py`: after each close, finds drops of 8% or more, classifies each NEWS / ANALYST / NO_NEWS with a subgroup, logs it, and later fills in 5 and 10 session market-adjusted outcomes. Read only.
* Both write private logs (Alpaca data terms). Only aggregates are published.
* Multiple-testing discipline: a new hypothesis is added only with a frozen spec; subgroup tests are Holm-adjusted; results go through `consumer/robust_stats.py` before anything is called promising.

## Money
The new strategies size from `consumer/paper_risk.py` (paper account, about 100,000 USD of equity): max loss per trade 2,500, max notional per stock 10,000, max open risk 7,500, daily loss halt 3,000,
at most 6 open news trades, defined-risk options only, never short stock. The S-042 forward test keeps its frozen 1,000 USD size so its protocol stays valid. These numbers are one decision in one file with tests.

## What is not claimed
No news strategy is validated until its study says so and a forward observation confirms it. Options cannot be backtested here (Alpaca history has trade prints, not quotes), so option structures are judged by the underlying move and then observed forward.
