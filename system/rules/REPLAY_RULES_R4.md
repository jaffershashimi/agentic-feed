# Replay rules R4 (replay-only room filter test)

Approved by the owner 2026-11-02 as a replay-only test (proposal from the Nov 1 research scan: every trigger on Oct 28 to 30 failed F4). The 1:23 PT run writes `claude/paper/replay_rules_R4.md` once and freezes it, with `R4_HASH` (first 12 hex of sha256 of the file minus its first line) on top. R4 is **never** used for PAPER entries; paper stays on R2 until the owner decides.

## Difference from R2

Only filter F4 (room to resistance) changes. R2 needs at least 2% room from price to the nearest resistance. R4 needs at least the smaller of:

- 2% of price, and
- one daily ATR(14) as a fraction of price (average true range of the 14 prior completed sessions).

So a quiet stock whose normal daily range is below 2% is judged against its own range instead of a fixed 2%. Every other filter, the D3 score (context, trigger, liquidity, room, risk), sizing, fills, stops and exits are exactly R2.

## Engine

`consumer/replay_r2.py --rules R4 --r2-hash <R4_HASH>`. Arms `REPLAY-A-R4-<SYM>` / `REPLAY-B-R4-<SYM>`, trade ids `R4A-` / `R4B-`, session keys `R4|YYYY-MM-DD|SYM`. Judged against R2 with the paired comparison (`compare_arms --base R2 --test R4`) on sessions from Nov 2, 2026.
