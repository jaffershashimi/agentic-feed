"""SEC EDGAR filings as a dated, point-in-time event source (S-053). Standard library only. Public data, no key.

SEC fair-access rules: at most 10 requests a second and a User-Agent that names the requester. This client keeps to 8 a second and reads the User-Agent from the
environment variable EDGAR_USER_AGENT (default: a generic research-bot name with no personal detail).

What it gives: for a ticker, every filing the SEC lists with its accession number, form, 8-K item codes and the EXACT acceptance time (UTC). The acceptance time is
when the filing became public, so a filing can be placed before or after a decision time without look-ahead.

  python3 -m consumer.edgar_events coverage TRADES.json CACHE_DIR      # counts only: how many trades map to a company, filing counts, flagged shares (never reads returns)
"""
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_UA = "AgenticLabResearch research-bot"
MIN_INTERVAL = 0.125            # 8 requests a second, under the SEC's 10
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
OLDER_URL = "https://data.sec.gov/submissions/{name}"

# Pre-registered classes (S-053 spec). 8-K item codes: https://www.sec.gov/files/form8-k.pdf
NOISE_ITEMS = {"7.01", "9.01"}                                   # Regulation FD disclosure and exhibits only: not a company event
EVENT_ITEMS = {"1.01", "1.02", "2.01", "2.02", "2.04", "2.05", "2.06", "3.01", "4.02", "5.02"}
#  1.01 and 1.02 material agreement signed or ended, 2.01 acquisition or disposal, 2.02 results, 2.04 default or acceleration, 2.05 exit costs, 2.06 impairment,
#  3.01 delisting notice, 4.02 non-reliance on past financials, 5.02 director or officer departure or appointment


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


class Client:
    """Rate-limited reader with an on-disk cache (the SEC files are public; the cache is private working data, not committed)."""

    def __init__(self, cache_dir, user_agent=None, opener=None, sleep=time.sleep, clock=time.monotonic):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.ua = user_agent or os.environ.get("EDGAR_USER_AGENT") or DEFAULT_UA
        self._open, self._sleep, self._clock, self._last = opener or urllib.request.urlopen, sleep, clock, 0.0

    def get(self, url, name):
        path = self.cache / name
        if path.exists():
            return json.loads(path.read_text())
        wait = MIN_INTERVAL - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip"})
        for attempt in range(3):
            try:
                self._last = self._clock()
                raw = self._open(req, timeout=40).read()
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass
                data = json.loads(raw)
                path.write_text(json.dumps(data))
                return data
            except Exception:
                if attempt == 2:
                    raise
                self._sleep(2 * (attempt + 1))

    def ticker_map(self):
        raw = self.get(TICKER_URL, "company_tickers.json")
        return {v["ticker"].upper(): int(v["cik_str"]) for v in raw.values()}

    def filings(self, cik):
        """All filings the SEC lists for this company: the recent block plus the older pages. Rows: accession, form, items (list), accepted (UTC datetime)."""
        first = self.get(SUBMISSIONS_URL.format(cik=cik), f"CIK{cik:010d}.json")
        blocks = [first["filings"]["recent"]]
        for f in first["filings"].get("files", []):
            blocks.append(self.get(OLDER_URL.format(name=f["name"]), f["name"]))
        rows = []
        for b in blocks:
            for i, form in enumerate(b["form"]):
                acc = b.get("acceptanceDateTime", [None] * len(b["form"]))[i]
                if not acc:
                    continue
                items = [x.strip() for x in (b["items"][i] or "").split(",") if x.strip()]
                rows.append({"accession": b["accessionNumber"][i], "form": form, "items": items, "accepted": parse_ts(acc)})
        return rows


def lookup(tickers, symbol):
    """Map one of our symbols to a CIK. Share classes use a dash at the SEC (BRK.B is BRK-B)."""
    for s in (symbol.upper(), symbol.upper().replace(".", "-")):
        if s in tickers:
            return tickers[s]
    return None


def in_window(filings, start, end):
    return [f for f in filings if start <= f["accepted"] <= end]


def flags(filings):
    """(V1, V2) for the filings in a window. V1: an 8-K with at least one item other than 7.01 and 9.01. V2: an 8-K with at least one event item."""
    v1 = v2 = False
    for f in filings:
        if f["form"] != "8-K":
            continue
        items = set(f["items"])
        if items - NOISE_ITEMS:
            v1 = True
        if items & EVENT_ITEMS:
            v2 = True
    return v1, v2


def coverage(trades, client, window_for, windows):
    """Counts only: mapped share, filings loaded and flagged shares per window. Reads symbol and entry_date only, never a return."""
    tk = client.ticker_map()
    by_window, mapped, total = {w: [0, 0, 0] for w in windows}, 0, 0
    cache = {}
    for t in trades:
        total += 1
        cik = lookup(tk, t["symbol"])
        w = next((n for n, (lo, hi) in windows.items() if lo <= t["entry_date"] <= hi), None)
        if w is None:
            continue
        if cik is None:
            continue
        mapped += 1
        if cik not in cache:
            cache[cik] = client.filings(cik)
        win = window_for(t["entry_date"])
        if not win:
            continue
        v1, v2 = flags(in_window(cache[cik], *win))
        by_window[w][0] += 1
        by_window[w][1] += v1
        by_window[w][2] += v2
    return {"trades": total, "mapped": mapped, "companies": len(cache),
            "by_window": {w: {"trades": n, "v1_share": round(a / n, 4) if n else None, "v2_share": round(b / n, 4) if n else None, "v1": a, "v2": b} for w, (n, a, b) in by_window.items()}}


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 3 or a[0] != "coverage":
        print(__doc__)
        return 2
    from consumer import news_veto_study as nv
    from consumer import oversold_panel as op
    panel = Path(os.environ.get("PANEL_DIR", "/home/user/research_panel"))
    data, _, _ = op.load_panel(panel)
    calendar = sorted(data["SPY"]["date"])
    trades = json.loads(Path(a[1]).read_text())
    res = coverage(trades, Client(a[2]), lambda d: nv.news_window(d, calendar), op.WINDOWS)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
