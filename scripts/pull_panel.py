"""Research data builder (network, long-running, private output). Usage: python3 scripts/pull_panel.py OUT_DIR
Pull a point-in-time liquid US stock panel from Alpaca (daily, split and dividend adjusted), delisted names included where Alpaca lists them.
Stage 1: weekly raw bars 2015 to now for every plain NYSE/NASDAQ/AMEX ticker (active and inactive) -> yearly dollar-volume stats.
Stage 2: daily adjusted bars for the union of each year's top 1,000 by prior-year dollar volume (price at least 5 dollars), plus market ETFs.
Data stays private (Alpaca data terms)."""
import csv, gzip, json, re, sys, time, urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from consumer import alpaca_paper as ap

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
(OUT / "daily").mkdir(exist_ok=True)
call = ap.make_client()
end = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(path):
    for attempt in range(6):
        try:
            time.sleep(0.33)
            return call("GET", path, None, "data")
        except ap.GuardError as e:
            if attempt == 5 or ("HTTP 4" in str(e) and "429" not in str(e)):
                raise
            time.sleep(2 ** attempt)


def bars(symbols, timeframe, start, adjustment):
    res, tok = defaultdict(list), None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end, "limit": "10000", "adjustment": adjustment, "feed": "sip", "sort": "asc"}
        if tok:
            q["page_token"] = tok
        j = get("/v2/stocks/bars?" + urllib.parse.urlencode(q))
        for s, bs in (j.get("bars") or {}).items():
            res[s].extend(bs)
        tok = j.get("next_page_token")
        if not tok:
            return res


cands = []
for st in ("active", "inactive"):
    for a in call("GET", f"/v2/assets?status={st}&asset_class=us_equity"):
        if a.get("exchange") in ("NYSE", "NASDAQ", "AMEX") and re.match(r"^[A-Z]{1,5}$", a["symbol"]):
            nm = (a.get("name") or "").lower()
            if any(w in nm for w in ("warrant", " right", " unit", "preferred", "etf", "etn", " trust", "fund", "notes due")):
                continue
            cands.append((a["symbol"], st))
syms = sorted({s for s, _ in cands})
print("candidates", len(syms), flush=True)

# stage 1
stats = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0.0]))  # sym -> year -> [sum dollar volume, weeks, sum close]
first, last, n = {}, {}, 0
for i in range(0, len(syms), 40):
    chunk = syms[i:i + 40]
    try:
        res = bars(chunk, "1Week", "2015-01-01T00:00:00Z", "raw")
    except ap.GuardError as e:
        print("stage1 chunk error", i, str(e)[:100], flush=True)
        continue
    for s, bs in res.items():
        for b in bs:
            y = int(b["t"][:4])
            r = stats[s][y]
            r[0] += float(b["c"]) * float(b["v"]); r[1] += 1; r[2] += float(b["c"])
        first[s], last[s] = bs[0]["t"][:10], bs[-1]["t"][:10]
    n += 1
    if n % 20 == 0:
        print("stage1", i, "of", len(syms), flush=True)
with open(OUT / "stage1_stats.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["symbol", "year", "avg_weekly_dollar_vol", "weeks", "avg_close", "first", "last"])
    for s, ys in stats.items():
        for y, (dv, wk, sc) in sorted(ys.items()):
            w.writerow([s, y, round(dv / wk), wk, round(sc / wk, 2), first[s], last[s]])

chosen = set(["SPY", "QQQ", "IWM", "DIA", "TLT", "VIXY"])
by_year = {}
for y in range(2016, 2027):
    rows = [(s, ys[y - 1][0] / ys[y - 1][1], ys[y - 1][2] / ys[y - 1][1]) for s, ys in stats.items() if (y - 1) in ys and ys[y - 1][1] >= 26]
    rows = [r for r in rows if r[2] >= 5.0]
    rows.sort(key=lambda r: -r[1])
    top = [r[0] for r in rows[:1000]]
    by_year[y] = top
    chosen |= set(top)
json.dump({str(k): v for k, v in by_year.items()}, open(OUT / "universe_by_year.json", "w"))
chosen = sorted(chosen)
print("stage1 done; union universe", len(chosen), flush=True)

# stage 2
tot = 0
for k, i in enumerate(range(0, len(chosen), 25)):
    chunk = chosen[i:i + 25]
    try:
        res = bars(chunk, "1Day", "2015-01-01T00:00:00Z", "all")
    except ap.GuardError as e:
        print("stage2 chunk error", i, str(e)[:100], flush=True)
        continue
    with gzip.open(OUT / "daily" / f"part-{k:04d}.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(["symbol", "date", "o", "h", "l", "c", "v"])
        for s in sorted(res):
            for b in res[s]:
                w.writerow([s, b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"]]); tot += 1
    if k % 10 == 0:
        print("stage2", i, "of", len(chosen), "rows", tot, flush=True)
print("DONE rows", tot, flush=True)
