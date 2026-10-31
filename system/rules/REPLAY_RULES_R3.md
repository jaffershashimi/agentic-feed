# Replay rules R3 (replay-only score test)

Approved by the owner 2026-10-26 as a replay-only test (closes proposal P-2026-10-26-B1). The 1:23 PT run writes `claude/paper/replay_rules_R3.md` once and freezes it, with `R3_HASH` (first 12 hex of sha256 of the file minus its first line) on top. R3 is **never** used for PAPER entries; paper stays on R2 until the owner decides.

## Difference from R2

Identical to [R2](REPLAY_RULES_R2.md) in every section except the D3 score. The trigger event stays a required condition and ADV20 stays filter F1, but neither is scored.

| Criterion | Points |
|---|---|
| context | as R2 |
| room | as R2 |
| risk | as R2 |
| trend_1h_4h | 2 if the last completed 1h close > 1h EMA21 AND the last completed 4h close > 4h EMA20; 1 if exactly one; 0 if neither |
| rel_volume | relative volume as defined in R2: 2 if >= 1.5, 1 if >= 1.0, 0 below 1.0 |

Entry still needs 6+. Arms `REPLAY-A-R3-<SYM>` / `REPLAY-B-R3-<SYM>`, source `REPLAY_R3;R3_<R3_HASH>`, session keys `R3|YYYY-MM-DD|SYM`. Back-replayed over Oct 20 to 23 and Oct 26 so R2 and R3 cover the same dates; reported side by side.

## Why

Under R1 and R2 the score did not rank outcomes (see RESULTS.md). R3 tests whether scoring trend alignment and relative volume directly, instead of the trigger and liquidity criteria, separates winners from losers. `consumer/replay_r2.py` keeps the score in one function (`evaluate`), so an R3 scorer is a small addition.
