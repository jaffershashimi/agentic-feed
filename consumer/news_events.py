"""News event tools for the headline strategies S-043 (geopolitical shock) and S-044 (single-stock news drop). Research and paper only.

Pieces
  * fetch_news: paged, deduplicated reader of the Alpaca (Benzinga) news endpoint, data host only.
  * text rules frozen in the specs: is_shock, is_template, is_informative, subgroup.
  * shock_events: causal event detector (3rd shock headline inside 90 minutes, 24 hour refractory period).
Nothing here places an order. Headline text is stored privately by the callers (Alpaca data terms), never committed.
"""
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

ACTION = ("strike", "strikes", "airstrike", "bomb", "bombs", "bombing", "missile", "missiles", "attack", "attacks", "invade", "invasion", "war",
          "military action", "retaliat", "explosion", "drone", "nuclear", "terror", "assassinat", "troops")
PLACE = ("iran", "israel", "russia", "ukraine", "china", "taiwan", "north korea", "hamas", "hezbollah", "syria", "iraq", "yemen", "houthi",
         "saudi", "gaza", "lebanon", "pakistan", "india",
         # demonyms: implementation detail so "Iranian missile" counts as Iran (added with the whole-token rule, before any statistic)
         "iranian", "israeli", "russian", "ukrainian", "chinese", "taiwanese", "syrian", "iraqi", "yemeni", "lebanese", "pakistani", "indian")
TEMPLATE_START = ("stocks moving", "shares are trading", "futures", "mid-day movers", "earnings scheduled", "economic data scheduled")
NOISE = ("shares are trading", "stocks moving", "stock is moving", "stocks hit", "mid-day movers", "movers", "gap down", "trading lower",
         "52-week low", "earnings scheduled", "options", "eps estimate")
ANALYST = ("price target",)
GROUPS = (("GUIDANCE", ("guidance", "outlook", "forecast", "cuts")), ("EARNINGS", ("earnings", "results", "quarter", "revenue", "eps")),
          ("LEGAL", ("lawsuit", "sec ", "doj", "investigation", "subpoena", "probe", "fraud", "class action")),
          ("REGULATORY_FDA", ("fda", "trial", "phase", "approval", "complete response", "recall")), ("MANAGEMENT", ("ceo", "cfo", "resign", "steps down")))


RETRY_BASE = 1.0  # seconds; tests set it to 0
STEMS = ("retaliat", "assassinat", "terror")


def _word_hit(text, words):
    """Whole-token match (so 'warning' is not 'war' and 'indiana' is not 'india'); stems like 'retaliat' match as prefixes."""
    for w in words:
        w = w.strip()
        pat = r"(?<![a-z])" + re.escape(w) + (r"" if w in STEMS else r"(?![a-z])")
        if re.search(pat, text):
            return True
    return False


def text_of(n):
    return ((n.get("headline") or "") + " " + (n.get("summary") or "")).lower()


def is_template(n):
    return (n.get("headline") or "").lower().strip().startswith(TEMPLATE_START)


def is_shock(n):
    if is_template(n):
        return False
    t = text_of(n)
    return _word_hit(t, ACTION) and _word_hit(t, PLACE)


def is_informative(n):
    if len(n.get("symbols") or []) > 3:
        return False
    t = text_of(n)
    return not any(k in t for k in NOISE) and not any(k in t for k in ANALYST)


def is_analyst(n):
    return any(k in text_of(n) for k in ANALYST) and len(n.get("symbols") or []) <= 3


def subgroup(n):
    t = text_of(n)
    for name, words in GROUPS:
        if _word_hit(t, words):
            return name
    return "OTHER"


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def shock_events(headlines, window_minutes=90, need=3, refractory_hours=24):
    """headlines: dicts with created_at, headline, summary, symbols. Returns [{t0, headlines:[...]}] sorted by time.
    An event fires at the creation time of the `need`-th shock headline within a rolling window, no sooner than `refractory_hours` after the last event."""
    shocks = sorted((n for n in headlines if is_shock(n)), key=lambda n: (n["created_at"], n.get("id", 0)))
    seen, uniq = set(), []
    for n in shocks:
        k = (n.get("id"), n["created_at"], n["headline"])
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    events, last = [], None
    win = timedelta(minutes=window_minutes)
    for i, n in enumerate(uniq):
        t = parse_ts(n["created_at"])
        if last is not None and t - last < timedelta(hours=refractory_hours):
            continue
        inside = [m for m in uniq[:i + 1] if t - parse_ts(m["created_at"]) <= win]
        if len(inside) >= need:
            events.append({"t0": t.isoformat(), "headlines": [m["headline"] for m in inside][:6]})
            last = t
    return events


def fetch_news(call, start, end, symbols=None, sleep=0.3, max_pages=100000, include_content=False):
    """Yield deduplicated news items between start and end (ISO strings), ascending, following next_page_token."""
    tok, seen, pages = None, set(), 0
    while pages < max_pages:
        q = {"start": start, "end": end, "limit": "50", "sort": "asc", "include_content": "true" if include_content else "false"}
        if symbols:
            q["symbols"] = ",".join(symbols) if not isinstance(symbols, str) else symbols
        if tok:
            q["page_token"] = tok
        j = None
        for attempt in range(5):
            try:
                j = call("GET", "/v1beta1/news?" + urllib.parse.urlencode(q), None, "data")
                break
            except Exception as exc:  # retry transient failures, give up after five tries
                if attempt == 4 or "HTTP 4" in str(exc) and "429" not in str(exc):
                    raise
                time.sleep(RETRY_BASE * 2 ** attempt)
        pages += 1
        for n in j.get("news", []):
            if n["id"] not in seen:
                seen.add(n["id"])
                yield {k: n.get(k) for k in ("id", "created_at", "headline", "summary", "symbols", "source", "author")}
        tok = j.get("next_page_token")
        if not tok:
            return
        if sleep:
            time.sleep(sleep)


def dump_jsonl(items, path):
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it) + "\n")
            n += 1
    return n


def load_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def cluster_bootstrap(values, keys, resamples=5000, seed=7):
    """Mean of `values` with a 95 percent interval from resampling whole clusters (for example calendar dates), so many events on one day count as one draw.
    Returns {n, clusters, mean, lo, hi, p_le_zero, p_ge_zero} or None when empty."""
    import random
    if not values:
        return None
    groups = {}
    for v, k in zip(values, keys):
        g = groups.setdefault(k, [0.0, 0])
        g[0] += v
        g[1] += 1
    sums, cnts = [g[0] for g in groups.values()], [g[1] for g in groups.values()]
    rnd, m = random.Random(seed), len(sums)
    means = []
    for _ in range(resamples):
        s = c = 0
        for __ in range(m):
            j = rnd.randrange(m)
            s += sums[j]
            c += cnts[j]
        means.append(s / c)
    means.sort()
    tot, n = sum(sums), sum(cnts)
    return {"n": n, "clusters": m, "mean": tot / n, "lo": means[int(0.025 * resamples)], "hi": means[int(0.975 * resamples) - 1],
            "p_le_zero": sum(1 for x in means if x <= 0) / resamples, "p_ge_zero": sum(1 for x in means if x >= 0) / resamples}
