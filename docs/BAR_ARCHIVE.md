# Bar archive (testing mode)

`consumer/bar_archive.py` keeps regular-session 5-minute stock bars from Alpaca's SIP feed, one gzip per day, with a manifest of sha256 hashes. Nothing reads from it for decisions yet. Approved by the owner on 2026-11-13 as a build in testing mode.

## Why

Replay results depend on price bars, and the Robinhood bars used so far get revised after the fact. Two examples from the Nov 10 to 12 reviews: the Nov 10 PLTR 12:15 fill moved from 166.8159 to 166.8011 (0.0089 percent), and a Nov 11 PLTR entry no longer re-derives at all. An archive with a hash per day makes inputs checkable and gives years of history to test on.

## Rules

- SIP data newer than 15 minutes is refused by the free plan. A day is fetched only when its session end is at least 16 minutes in the past, and an explicit end time is always sent (a date-only end was refused when tested).
- Regular session only: 9:30 to 16:00 Eastern, 13:00 on early closes (from `feed_pipeline.calendar`). Pre and post market bars are dropped.
- Values are kept as the exact text the API sent. Rows are sorted by symbol and time and the gzip header has no timestamp, so the same data always gives the same bytes and hash.
- Append only: an archived day is never rewritten. `verify` re-fetches a day and reports whether SIP changed it; `check` re-hashes every file against `bars/MANIFEST.csv`.

## Commands

```
python3 -m consumer.bar_archive fetch   --root DIR --symbols-file universe.txt --from 2026-10-19 --to 2026-11-12
python3 -m consumer.bar_archive verify  --root DIR --date 2026-11-12
python3 -m consumer.bar_archive check   --root DIR
python3 -m consumer.bar_archive compare --root DIR --date 2026-11-10 --bars bars5m.csv
```

Credentials come from the environment (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, optional `ALPACA_DATA_BASE_URL`). Only GET requests to the data host are made. Keys are never printed or written.

## First measurements (2026-11-13)

- 40 symbols, 3,117 to 3,120 bars and about 73 KB per day; 19 sessions (Oct 19 to Nov 12) is 1.4 MB. A re-fetch of Nov 12 was byte-identical.
- Against Robinhood 5-minute bars for PLTR, AMD and MSFT (Nov 10 to 12): the median price difference is 0, the 95th percentile is 0.015 to 0.036 percent, the maximum is 1.06 percent; the closes match exactly on 77 to 80 percent of bars. Robinhood volume is about 55 percent of SIP volume (a partial feed), so volume-based scores (R3 rel_volume, ADV filters) would change if the source changed.
- The Nov 10 PLTR 12:15 open that Robinhood now serves (166.7177) equals SIP; the ledger entry used the earlier 166.7325. The revised Robinhood bar moved toward the SIP value.

## Limits

- This is not a drop-in replacement: switching the replay's bar source changes volume features and needs the owner's approval and a new pin.
- SIP bars exist only for intervals with trades, so thin symbols have fewer than 78 bars a day.
- The data files live in a private repository; Alpaca's data terms were not reviewed for any wider use.
