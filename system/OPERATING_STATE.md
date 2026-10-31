# Agentic Lab operating state (public, no personal data)

Updated 2026-10-31. This is the one public page a scheduled run can fetch to know how the system works today. The private hub is `jaffershashimi/Agentic-Lab`; this public repo holds the feed, the pinned tools and the rule texts. The former City app and its repository were deleted on 2026-10-31.

## Roles

| Who | Role |
|---|---|
| Owner | Approves every rule change and places every real order. Live automated trading is paused. |
| Claude | Orchestrator: paper desk, replay lab, daily review, weekly research scan, planning. |
| ChatGPT | Assistant: Research Student notes in Drive, Weekly Scorecard; other tasks only when Claude or the owner assigns them. |

## Current mode

- Paper trading only, stocks first. Options are tested in paper and replay only, until stock paper results prove an edge.
- Manual SPY 0DTE trading is stopped.
- Every new strategy idea is TEST ONLY: replay first, then a paired holdout test, then the owner decides.

## Weekday schedule (Pacific time)

| Time | Run | Output |
|---|---|---|
| 6:52 | Claude Paper Desk Open | entries, skips, SPY snapshot |
| 7:47, 9:47, 11:47 | Claude hourly check | exits, pending triggers |
| 8:47 | Claude Manage | manage plus one scan |
| 10:47 | Claude Midday | manage plus one scan, last entry 12:45 |
| 12:41 | Claude Close | overnight decisions, ledger rows to Drive |
| 13:23 | Claude Review and Replay Lab | R2 and R3 by the pinned engine, checker, score check, paired comparison |
| 13:55 | ChatGPT Records Check | checks the day's Drive records, flags gaps |
| 14:15 | Claude Daily Review | brief to the owner |
| twice daily | ChatGPT Research Student | research notes in Drive |
| Fri 13:30 | ChatGPT Weekly Scorecard | paper desk scorecard |
| Sun 15:37 | Claude Research Scan | digest, one TEST ONLY experiment, resource center update |
| Sun 16:00 | ChatGPT Week Ahead | earnings and macro dates for the coming week |

Shared files live in the Google Drive folder "Agentic Lab Paper Desk" (ledger rows, handoffs, reviews, snapshots, records checks) and "Agentic Lab Research Library" (research notes). Nothing goes to the retired City.

## Rules in force

| Rule | File | Hash | Use |
|---|---|---|---|
| Paper policy p2 | [rules/PAPER_POLICY_P2.md](rules/PAPER_POLICY_P2.md) | 8782fd93fbd2 | PAPER entries and limits |
| Replay rules R2 | [rules/REPLAY_RULES_R2.md](rules/REPLAY_RULES_R2.md) | c879c06e24a5 | PAPER and REPLAY |
| Replay rules R3 | [rules/REPLAY_RULES_R3.md](rules/REPLAY_RULES_R3.md) | set when frozen | REPLAY only (score test) |

These are copies for reference; the running desk reads its own frozen copies, and a hash mismatch means the copy here is out of date.

## Pinned tools (raw URLs at a fixed commit, sha256 verified before use)

| Tool | Commit | sha256 |
|---|---|---|
| consumer/paper_ledger.py | 59b1eeb | bbed2756813ece1b0eddd60ee44d2659c1eee2681c91e9b8eb52251de02e976d |
| consumer/score_check.py | 59b1eeb | 1ca793e864893e94b5a249b14a2cb8156976e7c61cc3d194d75084dbd81e3b56 |
| consumer/compare_arms.py | 3e90c0a | de033bf8ea4067f645103fc6c179961ece94f8d6bad82806a79d35f8efe0f54f |
| consumer/replay_r2.py (engine) | 6843276 | bd2cc914973edd816b8b1abd6f7b77af7be5192081922de419eb4ea2eabffdd3 |

Docs: [REPLAY_R2](../docs/REPLAY_R2.md), [COMPARE_ARMS](../docs/COMPARE_ARMS.md), [SCORE_CHECK](../docs/SCORE_CHECK.md), [DESK_CONSUMER](../docs/DESK_CONSUMER.md).

## Judging results

- PAPER, REPLAY, REPLAY-DAILY and SYNTHETIC are never pooled.
- Sessions Oct 20 to 27, 2026 are development data; only sessions from Nov 2, 2026 are the holdout.
- A rule proposal needs a holdout paired result past `insufficient_evidence` (30 pairs, 15 trades per group, 10 sessions) and the owner's approval.
- Unknown fees mean unknown net. Small samples prove nothing.

## Go-live gate (all required, then the owner decides)

4 clean weeks, 100+ closed paper trades, positive net after fees, profit factor 1.2 or more, drawdown within limits.

## Adding data and what comes next

- New APIs: follow [docs/ADDING_A_SOURCE.md](../docs/ADDING_A_SOURCE.md) (public feed vs private desk).
- The plan to run the whole system 24/7 on a mini PC is in the private hub: `Agentic-Lab/system/07-engineering/BLUEPRINT_24-7.md`.

## Known issues (Oct 30)

- Runs hit the usage limit on Oct 28 to 30; the owner has set aside usage for paper runs.
- Some run sandboxes blocked downloading the pinned checker; the review reports it when it happens.
