# Replay rules R2 (frozen)

Copied from the Drive doc "Replay Rules R2" (2026-10-26). Drive stripped `<SYM>` placeholders; they are restored here. The canonical file is `claude/paper/replay_rules_R2.md` in Claude project storage; its hash is computed on that file, not this copy.

R2_HASH: c879c06e24a5

# Replay rules R2 (FROZEN 2026-10-26)

Approved basis: Jaffer, Oct 25, 2026 (REPLAY RULES R2 step of the 1:23 PM PT Review and Replay Lab), on top of paper policy p2 (policy_hash 8782fd93fbd2). Written once by the 2026-10-26 13:23 PT run and frozen. Any change needs Jaffer's approval and a new version (R3). R2_HASH = first 12 hex characters of sha256 of this file's UTF-8 text with the first line (the R2_HASH line) removed.

Scope: every REPLAY trade from 2026-10-26 on (ledger source starts REPLAY_R2) and, from the same day, PAPER entries use the same written filters, score and exits. R1 and R1A rows stay as they are and are never pooled with R2. Where policy p2 or DESK_CORE section 3 needs data that bars cannot give (a dated catalyst, the option spread and open interest), R2 uses the bar-based stand-ins written below. Those stand-ins are part of R2, not of p2.

## 1. Data and clock

  - Bars: Robinhood 5-minute regular-session bars (get_equity_historicals, interval 5minute, split adjusted). Bars marked interpolated are dropped. Daily bars: Robinhood interval day. Option bars: get_option_historicals 5minute, interpolated bars dropped.
  - Session 6:30 to 13:00 PT (13:30 to 20:00 UTC while PDT). Bars are labeled by their start time.
  - Derived bars are session aligned and built from the 5m bars: 15m (6:30, 6:45, ...), 1h (6:30-7:30, ..., 12:30-13:00), 4h (6:30-10:30 and 10:30-13:00). A bar counts only after its end time.
  - EMAs are standard (alpha = 2/(n+1), seeded with the first close) and run continuously across sessions from the first loaded bar (at least 15 sessions of 5m history before the first replay day).
  - Session VWAP = cumulative sum of typical price (high+low+close)/3 x volume over cumulative volume, from 6:30 PT, after each completed 5m bar.
  - ATR(5m,14) = simple mean of the last 14 true ranges of completed 5m bars.
  - No look-ahead: every decision uses only bars completed at the decision time and daily bars from prior sessions.

## 2. Decision points and trigger event

  - A decision is evaluated at each completed 15m bar whose end time is between 6:45 and 12:45 PT inclusive. The entry bar is the 5m bar that starts at that time.
  - Trigger event (required; no event, no decision): the completed 15m close P is (a) above the highest high of the 4 previous completed 15m bars (BREAK_1H_HIGH), or (b) above session VWAP while the previous 15m close was at or below its VWAP (VWAP_RECLAIM). If both, it is recorded as BREAK_1H_HIGH.

## 3. Entry filters (all must pass; a failed filter is recorded, never scored into an entry)

  - F1 liquidity: 20-session average daily volume (ADV20, prior 20 completed sessions) at least 1,000,000 shares.
  - F2 price: P at least $10.
  - F3 trend: prior session daily close above the 200-session simple moving average of daily closes (needs 221 daily closes).
  - F4 room: at least 2% from P to the next resistance R. R = the lowest of these levels strictly above P: the prior session's daily high, and every confirmed daily swing high in the last 60 completed sessions (a daily high above the highs of the 2 sessions before and the 2 sessions after it). If no level is above P, room is unlimited and F4 passes.
  - F5 not extended: z = (P - session VWAP) / ATR(5m,14) between -0.5 and +2.5 inclusive (DESK_CORE section 4 drift band).
  - F6 earnings day: no entry on a session when the symbol reports after that close (pm report dated that session).
  - F7 policy p2 D4 caps, per arm and per symbol trial (arm REPLAY-A-R2-<SYM> or REPLAY-B-R2-<SYM>): at most 3 open positions, at most 5 entries per session, total open entry size at most 75% of $5,000 ($3,750). Window 6:45 to 12:45 PT (section 2).

## 4. D3 conviction score (0 to 10; entry needs 6 or more)

Five criteria, 0 to 2 points each, named exactly as below (score_parts.csv uses these names):

  - context (stand-in for "catalyst/context"; no dated catalyst can be replayed): +1 if the prior daily close is above the 20-session EMA of daily closes; +1 if the 200-session SMA at the prior session is above the 200-session SMA 20 sessions earlier (rising).
  - trigger: 1 point for the trigger event; +1 more if BOTH the last completed 1h close is above the 1h EMA21 AND the last completed 4h close is above the 4h EMA20. Relative volume below 1.0 caps trigger at 1. Relative volume = cumulative session volume through the trigger bar / (ADV20 x minutes elapsed since 6:30 / 390).
  - liquidity (stand-in for "contract": spread and open interest cannot be replayed): 2 if ADV20 at least 5,000,000 shares; 1 if at least 1,000,000.
  - room: invalidation I = lowest low of the last 4 completed 15m bars (trigger bar included). Ratio = (R - P) / (P - I). 2 if there is no R or the ratio is at least 2.0; 1 if at least 1.0; 0 otherwise.
  - risk: window = the entry session plus the next 2 trading sessions. 0 if the symbol's earnings report date falls in the window; otherwise 1 if a scheduled macro event falls in it; otherwise 2. Macro list for Oct-Nov 2026: FOMC Wed 2026-10-28, GDP and PCE Thu 2026-10-29. Earnings dates: Robinhood get_earnings_calendar (high market cap) pulled on the run day. Score = sum of the five. Recorded score values are 6, 7, 8, 9 or 10 for entries.

## 5. Sizing (policy p2 D3)

Each symbol trial has $5,000 of notional arm equity, not compounded. Size = $500 at score 6, 7 or 8 and $750 at 9 or 10.

## 6. Instrument choice (policy p2 D1)

  - Arm A option: calls only, expiry = the first listed expiry on or after entry date + 21 calendar days (no 0DTE). Candidate strikes: the 10 listed strikes whose rough delta (Black-Scholes with IV = 1.3 x max(RV20, 15%)) lies between 0.22 and 0.68, closest to 0.45. For each candidate, IV is solved from the close of the 5m option bar that ends at the decision time (Black-Scholes, r = 4%, no dividend, T in calendar years to 16:00 ET on expiry, spot = P), and delta from that IV. Eligible: delta 0.30 to 0.60 and cost = entry-bar high x 100 + $0.68 at or under the size. Pick the eligible delta closest to 0.45. Quantity = floor(size / cost). No candidate with real bars at both the prior and the entry bar = MISSING_DATA. Candidates with data but none eligible = NO_FIT (no Arm A trade).
  - Arm B: takes the same option as Arm A when Arm A found one and its IV / RV20 is 1.5 or lower. Otherwise (NO_FIT, MISSING_DATA or IV/RV20 above 1.5) Arm B takes the stock: shares = size / fill, fractional to 6 decimals. RV20 = sample standard deviation of the last 20 daily log returns x sqrt(252).

## 7. Fills (fees per p2 D6)

  - Stock entry: ref_price = open of the entry 5m bar; price = ref x 1.0005 (0.05% slippage); quote_time = that bar's start; fees 0.
  - Stock exit: ref_price = open of the 5m bar after the trigger; price = ref x 0.9995; fees 0.
  - Option entry (ledger, HARSH): price = ref_price = entry 5m bar high; fees $0.68 per contract.
  - Option exit (ledger, HARSH): price = ref_price = the exit bar low; fees $0.68 per contract.
  - REALISTIC (review only, never in the ledger): entry = entry bar midpoint (high+low)/2 x 1.03; exit = exit bar midpoint x 0.97; same fees.
  - stop_set row at the entry time: stock stop = fill x 0.80 rounded UP to the cent; option stop = fill x 0.85 rounded UP to the cent (never looser).

## 8. Exits (first one that triggers)

Underlying rules (computed on the stock bars for both instruments; an option position exits on the first real option bar starting at or after the stock exit bar):

  - EXIT_FADING_MOMENTUM: a completed 15m bar that starts after the entry with close above the underlying entry price (green) AND below the 15m EMA9 AND below session VWAP. Exit at the next 5m bar.
  - EXIT_4H_CLOSE_UNDER_EMA20 (DESK_CORE exit review, applied mechanically): the first completed 4h bar ending after the entry with close below the 4h EMA20. Exit at the next 5m bar.
  - EXIT_EARNINGS_BEFORE_CLOSE: if the symbol reports after that session's close or before the next open, exit at the 12:55 PT bar.
  - EXIT_TIME_STOP_5D: still open at the close of the 5th session after entry. Exit at the next 5m bar. Price rules:
  - Stock: a 5m bar low at or below the stop exits at the next bar's open (EXIT_STOP_20PCT); a 5m close at or above fill x 1.30 exits at the next bar's open (EXIT_TARGET_30PCT).
  - Option: a real option bar after the entry bar with low at or below the stop exits at that bar's low (EXIT_STOP_15PCT, HARSH); a real bar close at or above fill x 1.30 exits at the next real bar's low (EXIT_TARGET_30PCT_NEXT_BAR).
  - Order inside one bar: underlying rule exit, then stop, then target.
  - Still open at the last replayed bar: UNRESOLVED, no exit row, marked at the last close in the review only and excluded from expectancy.

## 9. Records

  - Arm names REPLAY-A-R2-<SYM> and REPLAY-B-R2-<SYM>: the R2 tag keeps R2 trials separate from the R1 arms REPLAY-A- and REPLAY-B-, so the checker never applies D4 caps across R1 and R2 rows for the same dates.
  - decision_id R2-YYYYMMDD-SYM-HHMM (PT entry time); trade_id R2A-SYM-YYYYMMDD-HHMM or R2B-SYM-YYYYMMDD-HHMM; event_id = trade_id + -E, -S or -X.
  - Ledger source: REPLAY_R2;R2_<R2_HASH>;... (entry, stop and exit tags as above). mode REPLAY, policy_hash 8782fd93fbd2, score on every row of the trade.
  - score_parts.csv: one row per criterion per entered trade, points as scored, raw values in value.
  - replay_sessions.csv key: R2|YYYY-MM-DD|SYM. A key is never replayed twice under R2.
  - PAPER entries under R2 use the same filters, score, sizing and exits on live bars, with p2 D5 quote fills instead of the bar fills in section 7.