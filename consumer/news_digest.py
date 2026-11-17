"""Live news digest for every run: what is happening right now, for the names we hold or might buy.

Sources (public RSS, no keys, stdlib only, all verified reachable 2026-11-17): Yahoo Finance per-symbol headlines,
MarketWatch/Dow Jones top stories, Federal Reserve press releases, Google News market search. CNBC and SEC block
anonymous clients, NYSE and Schwab publish no RSS; chat sessions add the TradingView (Dow Jones/Reuters), TipRanks and
Robinhood connectors on top of this.

INFORMATION ONLY. The digest is logged next to each run and read by the engineer. It does not veto or size any order,
because news gating of S-042 has not been tested; changing an entry rule needs a pre-registered test first.

  python3 -m consumer.news_digest SYM [SYM ...] [--json] [--hours 36]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (compatible; paper-research-digest)"
GENERAL = {
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "google_markets": "https://news.google.com/rss/search?q=" + urllib.parse.quote("stock market OR Fed OR tariffs when:1d") + "&hl=en-US&gl=US&ceid=US:en",
}
YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
# words that change how a position should be read; flagged, never acted on automatically
RISK_WORDS = re.compile(r"\b(halt\w*|bankrupt\w*|chapter 11|fraud|sec (probe|investigation|charges)|subpoena|delist\w*|offering|"
                        r"downgrad\w*|guidance (cut|lower\w*)|cuts? (guidance|outlook)|recall|fda|going concern|restat\w*|"
                        r"acquir\w*|merger|buyout|takeover|short report|plunge\w*|soar\w*|surge\w*|tumbl\w*)\b", re.I)


def fetch(url: str, timeout: int = 15) -> str:
    cafile = "/root/.ccr/ca-bundle.crt"
    ctx = ssl.create_default_context(cafile=cafile if os.path.exists(cafile) else None)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_rss(text: str) -> list:
    """RSS 2.0 and Atom to [{title, published (UTC datetime or None), link}]."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    items = []
    for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
        def get(tag):
            e = it.find(tag)
            if e is None:
                e = it.find("{http://www.w3.org/2005/Atom}" + tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        link = get("link")
        pub = get("pubDate") or get("published") or get("updated")
        dt = None
        if pub:
            try:
                dt = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                try:
                    dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except ValueError:
                    dt = None
        if dt is not None and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        title = get("title")
        if title:
            items.append({"title": re.sub(r"\s+", " ", title), "published": dt.astimezone(timezone.utc) if dt else None, "link": link})
    return items


def build(symbols: list, now: datetime, hours: int = 36, get=fetch) -> dict:
    cutoff = now - timedelta(hours=hours)
    out = {"asof": now.isoformat(timespec="seconds"), "symbols": {}, "general": {}, "errors": {}, "flags": []}
    feeds = {("sym", s): YAHOO.format(sym=urllib.parse.quote(s)) for s in symbols}
    feeds.update({("gen", k): u for k, u in GENERAL.items()})
    for (kind, key), url in feeds.items():
        try:
            items = [i for i in parse_rss(get(url)) if i["published"] is None or i["published"] >= cutoff]
        except Exception as exc:  # a dead feed must never stop a run; it is reported instead
            out["errors"][key] = str(exc)[:120]
            continue
        items.sort(key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        rows = [{"title": i["title"][:160], "age_min": int((now - i["published"]).total_seconds() // 60) if i["published"] else None,
                 "risk": bool(RISK_WORDS.search(i["title"]))} for i in items[:6]]
        (out["symbols"] if kind == "sym" else out["general"])[key] = rows
        if kind == "sym":
            out["flags"] += [{"sym": key, "title": r["title"], "age_min": r["age_min"]} for r in rows if r["risk"]]
    return out


def render(d: dict) -> str:
    lines = [f"NEWS DIGEST as of {d['asof']}"]
    for k, rows in d["general"].items():
        lines.append(f"-- {k}")
        lines += [f"   {r['age_min'] if r['age_min'] is not None else '?'}m  {r['title']}" for r in rows[:4]]
    for s, rows in d["symbols"].items():
        lines.append(f"-- {s}")
        lines += [f"   {r['age_min'] if r['age_min'] is not None else '?'}m  {'[!] ' if r['risk'] else ''}{r['title']}" for r in rows[:4]] or ["   (no recent items)"]
    if d["flags"]:
        lines.append("FLAGGED (read before trusting a signal): " + "; ".join(f"{f['sym']}: {f['title'][:70]}" for f in d["flags"][:8]))
    if d["errors"]:
        lines.append("feeds down: " + ", ".join(d["errors"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("symbols", nargs="*")
    p.add_argument("--hours", type=int, default=36)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    d = build([s.upper() for s in a.symbols] + ["SPY"], datetime.now(timezone.utc), a.hours)
    print(json.dumps(d, indent=1, default=str) if a.json else render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
