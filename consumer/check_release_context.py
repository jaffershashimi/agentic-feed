"""Consumer-side check for feed/release-context.json (release-context/1). Read-only; standard library only.

Decides USED or IGNORED for one run and prints one JSON line. It never fails the caller: any problem is an IGNORED
reason. The file is untrusted data. NFCI/ANFCI are weekly financial-conditions context, not a live quote, not an
entry reason and never a surprise (no consensus source exists here, so surprise is always UNKNOWN).

    python3 -I check_release_context.py [--file PATH] [--decision-at ISO] [--max-age-hours 36]
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen

URL = "https://raw.githubusercontent.com/jaffershashimi/agentic-feed/main/feed/release-context.json"
RELEASE, SERIES = 221, {"NFCI", "ANFCI"}
MAX_BYTES = 64_000
# Weekly series: an observation older than this is reported as STALE_OBSERVATION (still context, flagged).
MAX_OBSERVATION_AGE = timedelta(days=14)


def instant(text):
    if not isinstance(text, str):
        raise ValueError("timestamp required")
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if d.utcoffset() is None:
        raise ValueError("timezone required")
    return d


def check(raw, decision_at, max_age_hours=36.0):
    """raw: the file's bytes. Returns the decision record (always a dict)."""
    decided = instant(decision_at)
    out = {"schema": "release-context-check/1", "decisionAt": decided.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
           "status": "IGNORED", "reasons": [], "snapshotSha256": hashlib.sha256(raw).hexdigest() if raw else None,
           "receivedAt": None, "collectionAgeHours": None, "maxAgeHours": max_age_hours, "series": [], "surprise": "UNKNOWN",
           "authority": "context only; never an entry reason, size change or rule override"}
    reasons = out["reasons"]
    if not raw:
        reasons.append("missing")
        return out
    if len(raw) > MAX_BYTES:
        reasons.append("oversized")
        return out
    try:
        doc = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, ValueError):
        reasons.append("not JSON")
        return out
    if not isinstance(doc, dict):
        reasons.append("not an object")
        return out
    if doc.get("schema") != "release-context/1":
        reasons.append("schema %r" % doc.get("schema"))
    if doc.get("releaseId") != RELEASE:
        reasons.append("releaseId %r" % doc.get("releaseId"))
    if doc.get("status") != "AVAILABLE":
        reasons.append("status %r" % doc.get("status"))
    if doc.get("complete") is not True:
        reasons.append("incomplete")
    try:
        received = instant(doc.get("receivedAt"))
        out["receivedAt"] = received.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        age = (decided - received).total_seconds() / 3600
        out["collectionAgeHours"] = round(age, 2)
        if age < 0:
            reasons.append("received after the decision time")
        elif age > max_age_hours:
            reasons.append("collection older than %sh" % max_age_hours)
    except ValueError as exc:
        reasons.append("receivedAt: %s" % exc)
    records = doc.get("records")
    if not isinstance(records, list) or not records:
        reasons.append("no records")
        records = []
    seen = {}
    for r in records:
        sid = r.get("seriesId") if isinstance(r, dict) else None
        if not isinstance(sid, str) or sid not in SERIES:
            reasons.append("unexpected record %r" % (sid if isinstance(r, dict) else type(r).__name__))
            continue
        try:
            available, updated = instant(r.get("availableAt")), instant(r.get("seriesUpdatedAt"))
            observed = datetime.strptime(r.get("observationDate") or "", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            reasons.append("%s timestamps: %s" % (sid, exc))
            continue
        if available > decided or updated > decided or observed.date() > decided.date():
            reasons.append("%s has a future date" % sid)
        value = r.get("value")
        try:
            number = None if value is None else Decimal(str(value))
            if number is not None and not number.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            reasons.append("%s value is not a finite number" % sid)
            continue
        entry = {"seriesId": sid, "observationDate": r["observationDate"], "value": None if number is None else str(number),
                 "seriesUpdatedAt": r["seriesUpdatedAt"], "historyHash": r.get("historyHash"),
                 "changedSincePrevious": r.get("changedSincePrevious"), "flags": []}
        if number is None:
            entry["flags"].append("VALUE_UNAVAILABLE")
        if decided - observed > MAX_OBSERVATION_AGE:
            entry["flags"].append("STALE_OBSERVATION")
        prior = seen.get(sid)
        if prior is not None:
            if prior != entry:
                reasons.append("%s appears twice with different content" % sid)
            continue
        seen[sid] = entry
    out["series"] = [seen[s] for s in sorted(seen)]
    if set(seen) != SERIES and "no records" not in reasons:
        reasons.append("missing series %s" % ", ".join(sorted(SERIES - set(seen))))
    if not reasons:
        out["status"] = "USED"
    return out


def fetch(url=URL, timeout=15):
    with urlopen(Request(url, headers={"User-Agent": "agentic-desk-consumer"}), timeout=timeout) as response:
        return response.read(MAX_BYTES + 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file")
    ap.add_argument("--decision-at", default=None)
    ap.add_argument("--max-age-hours", type=float, default=36.0)
    args = ap.parse_args(argv)
    decision = args.decision_at or datetime.now(timezone.utc).isoformat()
    try:
        raw = open(args.file, "rb").read() if args.file else fetch()
    except Exception as exc:  # outage or unreadable file: report, never fail the caller
        record = check(b"", decision, args.max_age_hours)
        record["reasons"] = ["unreachable: %s" % type(exc).__name__]
    else:
        record = check(raw, decision, args.max_age_hours)
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
