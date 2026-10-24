"""Paper-desk ledger checker v2: standard library only, read-only, no network, no broker.

Reads a paper fills CSV (one row per simulated event) and reports, per lane: measured closed trades, win rate, average
win and loss, expectancy, net after recorded costs, and worst drawdown in closing-time order, plus every rule breach.
A malformed row is reported and skipped, never guessed. A trade with any lifecycle problem (no entry, mixed instrument
identity, stop recorded before the entry, missing or invalid costs, stale or missing quote evidence, a widened stop,
unbalanced spread legs) is listed under "excluded" and never counted in the performance figures. Open trades are
listed but never counted as wins or losses.

    python3 -I consumer/paper_ledger.py fills.csv [--max-open 1] [--max-entries 2] [--option-stop 0.15]
        [--equity-stop 0.20] [--spread-stop 0.30] [--max-quote-age-option 60] [--max-quote-age-equity 60]

Required columns: event_id, arm, trade_id, kind (entry | exit | stop_set), occurred_at (ISO with offset), symbol,
instrument (equity | option), side (buy | sell), qty, price, multiplier, decision_id.
Optional columns (v2): leg (empty for a single instrument; "long" or "short" for a spread leg; "net" for a spread's
stop row), fees (USD for that event; required on entry and exit rows for a trade to count as measured),
quote_time (when the quoted price was observed, ISO with offset), received_at (when the run received it),
quote_delay (realtime | delayed | unknown), quote_source.

Rules the checker enforces:
- Long only for single instruments: buy to open, sell to close. Shorts are disabled. A short option is accepted only
  as the "short" leg of a spread whose "long" leg is in the same trade (sell to open, buy to close).
- Multipliers: option legs 100, equity 1. Every row of a trade (or of one spread leg) has the same symbol,
  instrument and multiplier.
- Stops: every stop_set is recorded at or after the entry, the first within the grace period, none looser than the
  limit, and none lower than an earlier stop (no widening).
- Quotes: every entry and exit row needs quote_time and received_at, quote_time <= occurred_at, received_at >=
  quote_time, quote_delay realtime, and an age (occurred_at - quote_time) within the limit. Otherwise it is not a fill.
"""
import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
COLUMNS = ("event_id", "arm", "trade_id", "kind", "occurred_at", "symbol", "instrument", "side", "qty", "price", "multiplier", "decision_id")
OPTIONAL = ("leg", "fees", "quote_time", "received_at", "quote_delay", "quote_source")
ALL_COLUMNS = COLUMNS + OPTIONAL
STOP_GRACE = timedelta(minutes=5)  # a protective stop must be recorded within this long after the entry
MULTIPLIER = {"option": 100.0, "equity": 1.0}


def _ts(value, n, name):
    at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError(f"row {n}: {name} needs a timezone offset")
    return at


def _row(raw, n):
    missing = [c for c in COLUMNS if not (raw.get(c) or "").strip()]
    if missing:
        raise ValueError(f"row {n}: missing {', '.join(missing)}")
    at = _ts(raw["occurred_at"], n, "occurred_at")
    qty, price, mult = float(raw["qty"]), float(raw["price"]), float(raw["multiplier"])
    if not all(math.isfinite(x) for x in (qty, price, mult)) or qty <= 0 or price < 0 or mult <= 0:
        raise ValueError(f"row {n}: qty and multiplier must be positive and price non-negative")
    kind, side, inst = raw["kind"].strip(), raw["side"].strip(), raw["instrument"].strip()
    leg = (raw.get("leg") or "").strip()
    if kind not in ("entry", "exit", "stop_set") or side not in ("buy", "sell") or inst not in MULTIPLIER:
        raise ValueError(f"row {n}: unknown kind, side or instrument")
    if leg not in ("", "long", "short", "net"):
        raise ValueError(f"row {n}: leg must be empty, long, short or net")
    if leg in ("long", "short") and inst != "option":
        raise ValueError(f"row {n}: spread legs must be options")
    if leg == "net" and kind != "stop_set":
        raise ValueError(f"row {n}: leg net is only for a spread's stop_set row")
    if mult != MULTIPLIER[inst]:
        raise ValueError(f"row {n}: multiplier {mult:g} is wrong for {inst} (expected {MULTIPLIER[inst]:g})")
    opening = "sell" if leg == "short" else "buy"
    closing = "buy" if leg == "short" else "sell"
    if (kind == "entry" and side != opening) or (kind == "exit" and side != closing):
        raise ValueError(f"row {n}: shorts are disabled; only long positions or the short leg of a spread are allowed")
    fees_raw = (raw.get("fees") or "").strip()
    fees = None
    if fees_raw:
        fees = float(fees_raw)
        if not math.isfinite(fees) or fees < 0:
            raise ValueError(f"row {n}: fees must be a non-negative number")
    out = dict(raw, at=at, qty=qty, price=price, mult=mult, kind=kind, side=side, instrument=inst, leg=leg, fees=fees)
    for name in ("quote_time", "received_at"):
        value = (raw.get(name) or "").strip()
        out[name] = _ts(value, n, name) if value else None
    out["quote_delay"] = (raw.get("quote_delay") or "").strip().lower()
    return out


def load(path):
    rows, errors, seen, seen_content = [], [], set(), set()
    with open(path, newline="", encoding="utf-8") as fh:
        for n, raw in enumerate(csv.DictReader(fh), start=2):
            try:
                row = _row(raw, n)
            except (ValueError, TypeError) as exc:
                errors.append(str(exc))
                continue
            if row["event_id"] in seen:
                errors.append(f"row {n}: duplicate event_id {row['event_id']}")
                continue
            content = tuple((raw.get(c) or "").strip() for c in ALL_COLUMNS if c not in ("event_id", "decision_id"))
            if content in seen_content:
                errors.append(f"row {n}: duplicate event content (same trade, kind, time, price and quantity as an earlier row)")
                continue
            seen.add(row["event_id"])
            seen_content.add(content)
            rows.append(row)
    rows.sort(key=lambda r: r["at"])
    return rows, errors


def _quote_problem(r, max_age):
    if r["quote_time"] is None or r["received_at"] is None:
        return "missing quote_time or received_at"
    if r["quote_delay"] != "realtime":
        return f"quote_delay is {r['quote_delay'] or 'missing'}, not realtime"
    if r["quote_time"] > r["at"]:
        return "quote_time is after the fill (look-ahead)"
    if r["received_at"] < r["quote_time"]:
        return "received_at is before quote_time"
    age = (r["at"] - r["quote_time"]).total_seconds()
    if age > max_age:
        return f"quote is {age:.0f}s old at the fill (limit {max_age:.0f}s)"
    return None


def _new_trade(r):
    return {"arm": r["arm"], "trade_id": r["trade_id"], "legs": {}, "stops": [], "problems": [], "opened": None,
            "closed": None, "fees": 0.0, "fees_known": True}


def analyze(rows, max_open=1, max_entries=2, option_stop=0.15, equity_stop=0.20, spread_stop=0.30,
            max_quote_age_option=60.0, max_quote_age_equity=60.0):
    trades, violations = {}, []
    for r in rows:
        t = trades.setdefault((r["arm"], r["trade_id"]), _new_trade(r))
        if r["kind"] == "stop_set":
            t["stops"].append((r["at"], r["price"], r["leg"], r["event_id"]))
            continue
        legkey = r["leg"] or "single"
        leg = t["legs"].setdefault(legkey, {"symbol": r["symbol"], "instrument": r["instrument"], "mult": r["mult"],
                                            "qty_in": 0.0, "qty_out": 0.0, "value_in": 0.0, "value_out": 0.0, "first_in": None})
        if (leg["symbol"], leg["instrument"], leg["mult"]) != (r["symbol"], r["instrument"], r["mult"]):
            t["problems"].append(f"instrument identity changed within the trade at {r['event_id']} "
                                 f"({leg['symbol']}/{leg['instrument']} vs {r['symbol']}/{r['instrument']})")
        limit = max_quote_age_option if r["instrument"] == "option" else max_quote_age_equity
        qp = _quote_problem(r, limit)
        if qp:
            t["problems"].append(f"{r['event_id']}: {qp}")
        if r["fees"] is None:
            t["fees_known"] = False
        else:
            t["fees"] += r["fees"]
        value = r["qty"] * r["price"] * r["mult"]
        if r["kind"] == "entry":
            if leg["qty_out"] > 0:
                t["problems"].append(f"{r['event_id']}: re-entry after a partial or full exit in the same trade id")
            leg["qty_in"] += r["qty"]
            leg["value_in"] += value
            leg["first_in"] = leg["first_in"] or r["at"]
            t["opened"] = t["opened"] or r["at"]
        else:
            leg["qty_out"] += r["qty"]
            leg["value_out"] += value
            if leg["qty_in"] == 0:
                t["problems"].append(f"{r['event_id']}: exit before any entry")
            if leg["qty_out"] > leg["qty_in"] + 1e-9:
                violations.append(f"{r['arm']}/{r['trade_id']}: sold more than was bought ({r['event_id']})")
                t["problems"].append(f"{r['event_id']}: exit quantity exceeds entry quantity")
            if all(abs(lg["qty_out"] - lg["qty_in"]) < 1e-9 and lg["qty_in"] > 0 for lg in t["legs"].values()):
                t["closed"] = r["at"]
    by_arm = defaultdict(list)
    for t in trades.values():
        by_arm[t["arm"]].append(t)
    report = {}
    for arm, ts in sorted(by_arm.items()):
        ts.sort(key=lambda t: t["opened"] or datetime.max.replace(tzinfo=PT))
        for t in ts:
            tag = f"{arm}/{t['trade_id']}"
            legs = t["legs"]
            if t["opened"] is None:
                t["problems"].append("events without an entry")
            spread = "long" in legs or "short" in legs
            if spread:
                if set(legs) != {"long", "short"}:
                    t["problems"].append("a spread needs exactly one long leg and one short leg, and no single-instrument rows")
                elif abs(legs["long"]["qty_in"] - legs["short"]["qty_in"]) > 1e-9:
                    t["problems"].append("spread legs have different quantities")
                elif legs["long"]["symbol"] == legs["short"]["symbol"]:
                    t["problems"].append("spread legs must be different contracts")
            t["spread"] = spread
            # Entry basis: net debit for a spread (long cost minus short credit), cost for a single instrument.
            if spread and {"long", "short"} <= set(legs):
                t["cost"] = legs["long"]["value_in"] - legs["short"]["value_in"]
                t["proceeds"] = legs["long"]["value_out"] - legs["short"]["value_out"]
                qty = legs["long"]["qty_in"] or 1.0
                t["entry_price"] = t["cost"] / (qty * 100.0)
                if t["cost"] <= 0:
                    t["problems"].append("a debit spread must cost more than zero")
            elif "single" in legs:
                lg = legs["single"]
                t["cost"], t["proceeds"] = lg["value_in"], lg["value_out"]
                t["entry_price"] = lg["value_in"] / (lg["qty_in"] * lg["mult"]) if lg["qty_in"] else None
            else:
                t["cost"] = t["proceeds"] = 0.0
                t["entry_price"] = None
            # Stops: never before the entry, the first within the grace period, none looser than the limit, no widening.
            if t["opened"] is not None and t["entry_price"]:
                inst = "spread" if spread else legs.get("single", {}).get("instrument", "option")
                limit = spread_stop if inst == "spread" else (equity_stop if inst == "equity" else option_stop)
                stops = sorted(t["stops"])
                early = [s for s in stops if s[0] < t["opened"]]
                for s in early:
                    t["problems"].append(f"stop {s[3]} is recorded before the entry")
                valid = [s for s in stops if s[0] >= t["opened"]]
                if spread and any(s[2] != "net" for s in valid):
                    t["problems"].append("a spread's stop_set rows must use leg net")
                first = next((s for s in valid if s[0] - t["opened"] <= STOP_GRACE), None)
                if first is None:
                    violations.append(f"{tag}: no protective stop within {int(STOP_GRACE.total_seconds() // 60)} minutes of entry")
                    t["problems"].append("no protective stop in time")
                floor = t["entry_price"] * (1 - limit) - 1e-9
                prev = None
                for s in valid:
                    if s[1] < floor:
                        violations.append(f"{tag}: stop {s[1]} is looser than {limit:.0%} below entry {t['entry_price']:.4f}")
                        t["problems"].append(f"stop {s[3]} looser than the limit")
                    if prev is not None and s[1] < prev - 1e-9:
                        violations.append(f"{tag}: stop widened from {prev} to {s[1]} ({s[3]})")
                        t["problems"].append(f"stop widened at {s[3]}")
                    prev = s[1] if prev is None else max(prev, s[1])
            if t["closed"] and not t["fees_known"]:
                t["problems"].append("fees missing on an entry or exit row (cost unknown, not measured)")
        # Concurrency, entries per day and stop-after-first-loss, per lane, on Pacific dates (all trades with an entry).
        opened = [t for t in ts if t["opened"]]
        for t in opened:
            overlapping = [u for u in opened if u["opened"] <= t["opened"] and (u["closed"] is None or u["closed"] > t["opened"])]
            if len(overlapping) > max_open:
                violations.append(f"{arm}/{t['trade_id']}: {len(overlapping)} positions open at once (limit {max_open})")
        per_day = defaultdict(list)
        for t in opened:
            per_day[t["opened"].astimezone(PT).date()].append(t)
        for day, day_ts in sorted(per_day.items()):
            if len(day_ts) > max_entries:
                violations.append(f"{arm}: {len(day_ts)} entries on {day} (limit {max_entries})")
            losses = [u["closed"] for u in day_ts if u["closed"] and u["proceeds"] - u["fees"] < u["cost"]]
            if losses:
                late = [u["trade_id"] for u in day_ts if u["opened"] > min(losses)]
                if late:
                    violations.append(f"{arm}: entries after the first loss on {day}: {', '.join(late)}")
        excluded = {t["trade_id"]: sorted(set(t["problems"])) for t in ts if t["problems"]}
        measured = sorted((t for t in opened if t["closed"] and not t["problems"]), key=lambda t: t["closed"])
        pnl = [t["proceeds"] - t["cost"] - t["fees"] for t in measured]
        wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
        equity = peak = drawdown = 0.0
        for p in pnl:  # closing-time order
            equity += p
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        r2 = lambda x: None if x is None else round(x, 2)  # noqa: E731
        report[arm] = {
            "closed_trades": len(measured), "open_trades": [t["trade_id"] for t in opened if not t["closed"] and not t["problems"]],
            "excluded": excluded,
            "win_rate": None if not measured else round(len(wins) / len(measured), 4),
            "avg_win": r2(sum(wins) / len(wins)) if wins else None, "avg_loss": r2(sum(losses) / len(losses)) if losses else None,
            "expectancy": r2(sum(pnl) / len(pnl)) if pnl else None, "net": r2(sum(pnl)), "fees": r2(sum(t["fees"] for t in measured)),
            "max_drawdown": r2(drawdown),
            "returns_pct": [round((p / t["cost"]) * 100, 2) if t["cost"] else None for p, t in zip(pnl, measured)],
        }
    return {"arms": report, "violations": violations}


def _bounded(kind, lo, hi):
    def parse(text):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text}")
        if not (isinstance(value, int) or math.isfinite(value)) or not lo <= value <= hi:
            raise argparse.ArgumentTypeError(f"{text} is outside {lo}..{hi}")
        return value
    return parse


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--max-open", type=_bounded(int, 1, 50), default=1)
    ap.add_argument("--max-entries", type=_bounded(int, 1, 50), default=2)
    ap.add_argument("--option-stop", type=_bounded(float, 0.01, 0.99), default=0.15)
    ap.add_argument("--equity-stop", type=_bounded(float, 0.01, 0.99), default=0.20)
    ap.add_argument("--spread-stop", type=_bounded(float, 0.01, 0.99), default=0.30)
    ap.add_argument("--max-quote-age-option", type=_bounded(float, 1, 3600), default=60.0)
    ap.add_argument("--max-quote-age-equity", type=_bounded(float, 1, 3600), default=60.0)
    a = ap.parse_args(argv)
    rows, errors = load(a.path)
    out = analyze(rows, a.max_open, a.max_entries, a.option_stop, a.equity_stop, a.spread_stop,
                  a.max_quote_age_option, a.max_quote_age_equity)
    out["errors"] = errors
    out["sample_note"] = ("Paper fills are simulated from observed quotes, not executions. Fewer than 30 measured closed trades per "
                          "lane proves nothing, and no count of paper trades validates a live rule by itself.")
    print(json.dumps(out, indent=1, default=str))
    excluded = any(arm["excluded"] for arm in out["arms"].values())
    return 1 if errors or out["violations"] or excluded else 0


if __name__ == "__main__":
    sys.exit(main())
