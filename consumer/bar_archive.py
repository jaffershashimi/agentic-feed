"""Archive of regular-session 5-minute stock bars from Alpaca's SIP feed (testing mode).

Why: replay results depend on price bars, and the bars used so far (Robinhood) are revised after the fact (Nov 10
PLTR 12:15 fill moved 0.0089 percent; a Nov 11 PLTR entry no longer re-derives). An archive with a hash per day
makes the inputs checkable and gives far more history to test on. Nothing reads from this archive for decisions yet.

Rules the tool enforces:
  * SIP data newer than 15 minutes is refused by the free plan, so a day is fetched only when its session end is at
    least 16 minutes in the past, and an explicit end time is always sent (never a date-only end).
  * Regular session only (9:30 to 16:00 Eastern, 13:00 on early closes, from feed_pipeline.calendar). Pre and
    post market bars are dropped.
  * Values are kept as the exact text the API sent, rows are sorted by symbol and time, and the gzip header carries no
    timestamp, so the same data always gives the same bytes and the same sha256.
  * Append only: a day that already has a file is never rewritten. `verify` re-fetches a day and reports whether SIP
    changed it; `check` re-hashes every archived file against the manifest.

Commands (all print JSON, none prints a key):
  fetch    fetch one day (--date) or a range (--from, --to) for the symbols given, write files and manifest rows
  verify   re-fetch one archived day and compare it with the file
  check    re-hash every file in the manifest
  compare  compare an archived day with a bars file in the replay format (symbol,begins_at,open,high,low,close,volume,...)

Credentials come from the environment (ALPACA_API_KEY, ALPACA_SECRET_KEY; ALPACA_DATA_BASE_URL is optional). Only GET
requests to the data endpoint are ever made. Standard library plus feed_pipeline.calendar.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

from feed_pipeline.calendar import ET, is_session, session_close

FIELDS = ("symbol", "t", "o", "h", "l", "c", "v", "n", "vw")
MANIFEST_FIELDS = ("date", "feed", "symbols", "rows", "bytes", "sha256", "fetched_at")
MIN_AGE = timedelta(minutes=16)  # free-plan SIP cutoff is 15 minutes; one more for clock skew
BATCH = 10
DEFAULT_BASE = "https://data.alpaca.markets"
FEED = "sip"


class ArchiveError(Exception):
    """A problem the operator must see (exit 2)."""


def session_window(day: date) -> tuple[datetime, datetime]:
    """(open, close) of the regular session as aware UTC datetimes."""
    if not is_session(day):
        raise ArchiveError(f"{day} is not an NYSE session")
    open_et = datetime.combine(day, dtime(9, 30), tzinfo=ET)
    return open_et.astimezone(timezone.utc), session_close(day).astimezone(timezone.utc)


def _parse_t(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_day(symbols, day: date, now: datetime, get) -> list[dict]:
    """All regular-session 5-minute SIP bars for the symbols on one day. `get(url)` returns parsed JSON (numbers as text)."""
    if now.tzinfo is None:
        raise ArchiveError("now must be timezone aware")
    start, end = session_window(day)
    if end > now - MIN_AGE:
        raise ArchiveError(f"{day} session end {_iso(end)} is within 16 minutes of now; the free plan refuses recent SIP data")
    syms = sorted({s.strip().upper() for s in symbols if s.strip()})
    if not syms:
        raise ArchiveError("no symbols")
    rows: list[dict] = []
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        token = None
        while True:
            q = {"symbols": ",".join(batch), "timeframe": "5Min", "start": _iso(start), "end": _iso(end), "feed": FEED,
                 "adjustment": "raw", "limit": "10000", "sort": "asc"}
            if token:
                q["page_token"] = token
            data = get("/v2/stocks/bars?" + urllib.parse.urlencode(q))
            for sym, bars in (data.get("bars") or {}).items():
                for b in bars or []:
                    t = _parse_t(b["t"])
                    if start <= t < end:  # drop pre and post market
                        rows.append({"symbol": sym, "t": _iso(t), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                                     "v": b["v"], "n": b.get("n", ""), "vw": b.get("vw", "")})
            token = data.get("next_page_token")
            if not token:
                break
    rows.sort(key=lambda r: (r["symbol"], r["t"]))
    seen = set()
    for r in rows:
        k = (r["symbol"], r["t"])
        if k in seen:
            raise ArchiveError(f"duplicate bar {k}")
        seen.add(k)
    return rows


def render(rows: list[dict]) -> bytes:
    """Deterministic gzip of the CSV: same rows, same bytes."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(FIELDS)
    for r in rows:
        w.writerow([str(r[f]) for f in FIELDS])
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    return raw.getvalue()


def read_file(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def day_path(root: Path, day: date) -> Path:
    return Path(root) / "bars" / f"{day:%Y}" / f"{day.isoformat()}.csv.gz"


def manifest_path(root: Path) -> Path:
    return Path(root) / "bars" / "MANIFEST.csv"


def read_manifest(root: Path) -> list[dict]:
    p = manifest_path(root)
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_day(root: Path, day: date, rows: list[dict], fetched_at: datetime) -> dict:
    """Write the day file and its manifest row. Never overwrites."""
    path = day_path(root, day)
    if path.exists() or any(m["date"] == day.isoformat() for m in read_manifest(root)):
        raise ArchiveError(f"{day} is already archived; the archive is append only")
    if not rows:
        raise ArchiveError(f"{day}: the API returned no bars; nothing written")
    data = render(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)
    entry = {"date": day.isoformat(), "feed": FEED, "symbols": len({r["symbol"] for r in rows}), "rows": len(rows),
             "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "fetched_at": _iso(fetched_at)}
    mp = manifest_path(root)
    new = not mp.exists()
    with open(mp, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(entry)
    return entry


def per_symbol_counts(rows) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["symbol"]] = out.get(r["symbol"], 0) + 1
    return out


def check(root: Path) -> dict:
    """Re-hash every archived file against the manifest."""
    problems, ok = [], 0
    seen = set()
    for m in read_manifest(root):
        d = m["date"]
        if d in seen:
            problems.append({"date": d, "problem": "DUPLICATE_MANIFEST_ROW"})
            continue
        seen.add(d)
        path = day_path(root, date.fromisoformat(d))
        if not path.exists():
            problems.append({"date": d, "problem": "FILE_MISSING"})
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != m["sha256"]:
            problems.append({"date": d, "problem": "HASH_MISMATCH"})
        elif len(data) != int(m["bytes"]):
            problems.append({"date": d, "problem": "SIZE_MISMATCH"})
        else:
            ok += 1
    on_disk = {p.name[:-7] for p in (Path(root) / "bars").glob("*/*.csv.gz")} if (Path(root) / "bars").exists() else set()
    for d in sorted(on_disk - seen):
        problems.append({"date": d, "problem": "FILE_NOT_IN_MANIFEST"})
    return {"days_ok": ok, "problems": problems}


def verify(root: Path, day: date, fresh_rows: list[dict]) -> dict:
    """Compare an archived day with freshly fetched rows. Reports revisions; changes nothing."""
    path = day_path(root, day)
    if not path.exists():
        raise ArchiveError(f"{day} is not archived")
    old = {(r["symbol"], r["t"]): r for r in read_file(path)}
    new = {(r["symbol"], r["t"]): r for r in fresh_rows}
    changed = [k for k in old if k in new and any(str(old[k][f]) != str(new[k][f]) for f in FIELDS)]
    return {"date": day.isoformat(), "identical": old == {k: {f: str(v[f]) for f in FIELDS} for k, v in new.items()},
            "archived_rows": len(old), "fresh_rows": len(new), "only_in_archive": len(set(old) - set(new)),
            "only_in_fresh": len(set(new) - set(old)), "changed_rows": len(changed),
            "changed_sample": [f"{s} {t}" for s, t in sorted(changed)[:5]]}


def compare_bars(archive_rows: list[dict], other_csv: Path, day: date | None = None) -> dict:
    """Archive vs a bars file in the replay format (symbol, begins_at, open, high, low, close, volume).

    Differences are reported as percent of the archive value; volumes as a ratio (other / archive)."""
    arch = {(r["symbol"], r["t"]): r for r in archive_rows}
    other = {}
    with open(other_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = _iso(_parse_t(r["begins_at"]))
            if day is not None and _parse_t(r["begins_at"]).astimezone(ET).date() != day:
                continue
            other[(r["symbol"], t)] = r
    common = sorted(set(arch) & set(other))
    diffs = {k: [] for k in ("o", "h", "l", "c")}
    vol_ratio, close_equal = [], 0
    for k in common:
        a, b = arch[k], other[k]
        for f, g in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")):
            av, bv = float(a[f]), float(b[g])
            diffs[f].append(abs(bv - av) / av * 100 if av else 0.0)
        close_equal += abs(float(a["c"]) - float(b["close"])) < 0.00005
        if float(a["v"]) > 0:
            vol_ratio.append(float(b["volume"]) / float(a["v"]))
    def stats(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return {"median": round(statistics.median(xs), 5), "p95": round(xs[int(0.95 * (len(xs) - 1))], 5), "max": round(xs[-1], 5)}
    return {"common_bars": len(common), "only_in_archive": len(set(arch) - set(other)), "only_in_other": len(set(other) - set(arch)),
            "close_exact_share": round(close_equal / len(common), 4) if common else None,
            "abs_pct_diff": {f: stats(v) for f, v in diffs.items()},
            "volume_ratio_other_over_archive": stats(vol_ratio)}


def make_http_get(env=os.environ):
    key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
    if not key or not secret or key.startswith("PASTE_"):
        raise ArchiveError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in the environment")
    base = (env.get("ALPACA_DATA_BASE_URL") or DEFAULT_BASE).rstrip("/")
    if "paper-api" in base or base.endswith("/v2"):
        raise ArchiveError("ALPACA_DATA_BASE_URL must be the data host (https://data.alpaca.markets)")
    cafile = "/root/.ccr/ca-bundle.crt"
    ctx = ssl.create_default_context(cafile=cafile if os.path.exists(cafile) else None)
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def get(path: str):
        url = base + path
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
                    time.sleep(0.35)  # stay far under 200 calls a minute
                    return json.loads(r.read(), parse_float=str, parse_int=str)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:200]
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise ArchiveError(f"HTTP {exc.code} from the data API: {body}") from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise ArchiveError(f"network error: {exc}") from None
        raise ArchiveError("unreachable")
    return get


def load_symbols(args) -> list[str]:
    syms: list[str] = []
    if args.symbols:
        syms += [s for s in args.symbols.split(",")]
    if args.symbols_file:
        syms += [ln.split("#")[0].strip() for ln in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()]
    return sorted({s.strip().upper() for s in syms if s.strip()})


def days_of(args) -> list[date]:
    if args.date:
        return [date.fromisoformat(args.date)]
    if args.from_ and args.to:
        d, end, out = date.fromisoformat(args.from_), date.fromisoformat(args.to), []
        while d <= end:
            if is_session(d):
                out.append(d)
            d += timedelta(days=1)
        return out
    raise ArchiveError("give --date, or --from and --to")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True, type=Path)
        p.add_argument("--symbols")
        p.add_argument("--symbols-file")
        p.add_argument("--date")
        if name == "fetch":
            p.add_argument("--from", dest="from_")
            p.add_argument("--to")
    p = sub.add_parser("check")
    p.add_argument("--root", required=True, type=Path)
    p = sub.add_parser("compare")
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--date", required=True)
    p.add_argument("--bars", required=True, type=Path, help="bars file in the replay format")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            rep = check(args.root)
            print(json.dumps(rep, indent=1))
            return 3 if rep["problems"] else 0
        if args.cmd == "compare":
            day = date.fromisoformat(args.date)
            print(json.dumps(compare_bars(read_file(day_path(args.root, day)), args.bars, day), indent=1))
            return 0
        syms = load_symbols(args)
        get = make_http_get()
        now = datetime.now(timezone.utc)
        out = []
        for day in days_of(args):
            if args.cmd == "verify":
                rows = fetch_day(syms or sorted({r["symbol"] for r in read_file(day_path(args.root, day))}), day, now, get)
                out.append(verify(args.root, day, rows))
                continue
            if day_path(args.root, day).exists():
                out.append({"date": day.isoformat(), "skipped": "already archived"})
                continue
            rows = fetch_day(syms, day, now, get)
            entry = write_day(args.root, day, rows, now)
            counts = per_symbol_counts(rows)
            entry["bars_per_symbol_min_max"] = [min(counts.values()), max(counts.values())]
            entry["symbols_missing"] = sorted(set(syms) - set(counts))
            out.append(entry)
        print(json.dumps(out if len(out) != 1 else out[0], indent=1))
        return 0
    except ArchiveError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
