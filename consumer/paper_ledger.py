"""Paper-desk ledger checker: standard library only, read-only, no network, no broker.

Reads a fills CSV (one row per simulated event) and reports, per mode (PAPER or REPLAY) and experiment arm:
closed, accepted, rejected and unmeasured trades, then win rate, average win and loss, expectancy, net after fees and
the running drawdown in closing order, computed from accepted trades only. A trade is rejected when its lifecycle is
malformed (inconsistent identity, events without or before an entry, overselling, a fill better than the quote it
cites); it is unmeasured when an entry or exit fee is unknown (blank is unknown, never 0). Rule breaches (late, loose
or widened stops, stale quotes, concurrency, entries per day, entries after the first loss) are listed for every trade
and do not by themselves remove a valid trade from the statistics. Open trades are never counted as wins or losses.
A malformed row is reported and skipped, never guessed.

    python3 -I consumer/paper_ledger.py fills.csv [--max-open 1] [--max-entries 2] [--option-stop 0.15]
        [--equity-stop 0.20] [--max-quote-age-minutes 20]

CSV columns: event_id, mode (PAPER | REPLAY), arm, trade_id, kind (entry | exit | stop_set), occurred_at and
quote_time (ISO with offset; quote_time is the market time of the quote used, for a delayed feed the delayed time),
symbol, instrument (equity | option | spread), side (buy | sell), qty, price, ref_price (the bid for a sell or the ask
for a buy at quote_time; blank allowed on stop_set), multiplier, fees (USD for this event; blank = unknown; ignored on
stop_set), policy_hash, decision_id. Long only: buy to open, sell to close; a stop_set row carries the stop in price.
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
COLUMNS = ("event_id", "mode", "arm", "trade_id", "kind", "occurred_at", "quote_time", "symbol", "instrument", "side", "qty",
           "price", "ref_price", "multiplier", "fees", "policy_hash", "decision_id")
REQUIRED = ("event_id", "mode", "arm", "trade_id", "kind", "occurred_at", "symbol", "instrument", "side", "qty", "price",
            "multiplier", "policy_hash", "decision_id")
STOP_GRACE = timedelta(minutes=5)  # a protective stop must be recorded at or after the entry and within this long
EPS = 1e-9


def _time(text, n, name):
    at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError(f"row {n}: {name} needs a timezone offset")
    return at


def _number(text, n, name, positive=False):
    x = float(text)
    if not math.isfinite(x) or x < 0 or (positive and x == 0):
        raise ValueError(f"row {n}: {name} must be a finite {'positive' if positive else 'non-negative'} number")
    return x


def _row(raw, n):
    missing = [c for c in REQUIRED if not (raw.get(c) or "").strip()]
    if missing:
        raise ValueError(f"row {n}: missing {', '.join(missing)}")
    kind, side, inst, mode = (raw[c].strip() for c in ("kind", "side", "instrument", "mode"))
    if kind not in ("entry", "exit", "stop_set") or side not in ("buy", "sell") or inst not in ("equity", "option", "spread"):
        raise ValueError(f"row {n}: unknown kind, side or instrument")
    if mode not in ("PAPER", "REPLAY"):
        raise ValueError(f"row {n}: mode must be PAPER or REPLAY")
    if (kind, side) in (("entry", "sell"), ("exit", "buy"), ("stop_set", "buy")):
        raise ValueError(f"row {n}: long only (buy to open, sell to close and to stop)")
    row = dict(raw, kind=kind, side=side, instrument=inst, mode=mode, at=_time(raw["occurred_at"], n, "occurred_at"),
               qty=_number(raw["qty"], n, "qty", True), price=_number(raw["price"], n, "price"),
               mult=_number(raw["multiplier"], n, "multiplier", True), quote_at=None, ref=None, fee=None)
    if kind != "stop_set":
        if not (raw.get("quote_time") or "").strip() or not (raw.get("ref_price") or "").strip():
            raise ValueError(f"row {n}: a fill needs quote_time and ref_price")
        row["quote_at"] = _time(raw["quote_time"], n, "quote_time")
        row["ref"] = _number(raw["ref_price"], n, "ref_price", True)
        if row["quote_at"] > row["at"]:
            raise ValueError(f"row {n}: quote_time is after occurred_at")
        fee = (raw.get("fees") or "").strip()
        row["fee"] = _number(fee, n, "fees") if fee else None
    return row


def load(path):
    rows, errors, seen = [], [], set()
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        absent = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if absent:
            return [], [f"header: missing columns {', '.join(absent)}"]
        for n, raw in enumerate(reader, start=2):
            try:
                row = _row(raw, n)
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(str(exc))
                continue
            if row["event_id"] in seen:
                errors.append(f"row {n}: duplicate event_id {row['event_id']}")
                continue
            seen.add(row["event_id"])
            rows.append(row)
    rows.sort(key=lambda r: r["at"])
    return rows, errors


def _check_limits(max_open, max_entries, option_stop, equity_stop, max_quote_age):
    for name, v in (("max_open", max_open), ("max_entries", max_entries)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ValueError(f"{name} must be an integer of at least 1")
    for name, v in (("option_stop", option_stop), ("equity_stop", equity_stop)):
        if not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 1:
            raise ValueError(f"{name} must be a finite fraction in (0, 1]")
    if not isinstance(max_quote_age, (int, float)) or not math.isfinite(max_quote_age) or max_quote_age <= 0:
        raise ValueError("max_quote_age_minutes must be a finite number above 0")


def _lifecycle(t):
    """Problems that make a trade's lifecycle malformed; any one rejects the trade from the statistics."""
    rows, bad = t["rows"], []
    identity = {(r["symbol"], r["instrument"], r["mult"], r["policy_hash"]) for r in rows}
    if len(identity) > 1:
        bad.append("inconsistent symbol, instrument, multiplier or policy_hash within the trade")
    entries = [r for r in rows if r["kind"] == "entry"]
    if not entries:
        return bad + ["events without an entry"]
    opened = entries[0]["at"]
    if any(r["at"] < opened for r in rows if r["kind"] == "exit"):
        bad.append("exit before the first entry")
    held = 0.0
    for r in rows:
        held += r["qty"] if r["kind"] == "entry" else -r["qty"] if r["kind"] == "exit" else 0
        if held < -EPS:
            bad.append(f"sold more than was bought ({r['event_id']})")
            break
    for r in rows:
        if r["kind"] == "entry" and r["price"] < r["ref"] - EPS:
            bad.append(f"entry {r['event_id']} filled below the ask it cites")
        if r["kind"] == "exit" and r["price"] > r["ref"] + EPS:
            bad.append(f"exit {r['event_id']} filled above the bid it cites")
    return bad


def analyze(rows, max_open=1, max_entries=2, option_stop=0.15, equity_stop=0.20, max_quote_age_minutes=20.0):
    _check_limits(max_open, max_entries, option_stop, equity_stop, max_quote_age_minutes)
    max_age = timedelta(minutes=max_quote_age_minutes)
    trades = {}
    for r in rows:
        trades.setdefault((r["mode"], r["arm"], r["trade_id"]), {"mode": r["mode"], "arm": r["arm"], "trade_id": r["trade_id"], "rows": []})["rows"].append(r)
    violations, rejected = [], []
    for t in trades.values():
        bad = _lifecycle(t)
        rs = t["rows"]
        entries, exits = [r for r in rs if r["kind"] == "entry"], [r for r in rs if r["kind"] == "exit"]
        t["opened"] = entries[0]["at"] if entries else None
        qty_in, qty_out = sum(r["qty"] for r in entries), sum(r["qty"] for r in exits)
        t["closed"] = exits[-1]["at"] if exits and abs(qty_in - qty_out) < EPS else None
        t["cost"] = sum(r["qty"] * r["price"] * r["mult"] for r in entries)
        t["proceeds"] = sum(r["qty"] * r["price"] * r["mult"] for r in exits)
        fees = [r["fee"] for r in entries + exits]
        t["fees"] = None if any(f is None for f in fees) else sum(fees)
        t["valid"] = not bad
        tag = f"{t['mode']}/{t['arm']}/{t['trade_id']}"
        if bad:
            rejected.append({"trade": tag, "reasons": bad})
        if not entries:
            continue
        # Protective stop: recorded at or after the first entry, within the grace, no looser than the limit, never widened.
        limit = equity_stop if rs[0]["instrument"] == "equity" else option_stop
        entry_price = t["cost"] / (qty_in * entries[0]["mult"])
        stops = [r for r in rs if r["kind"] == "stop_set" and r["at"] >= t["opened"]]
        first = next((s for s in stops if s["at"] - t["opened"] <= STOP_GRACE), None)
        if first is None:
            violations.append(f"{tag}: no protective stop recorded within {int(STOP_GRACE.total_seconds() // 60)} minutes after entry")
        elif first["price"] < entry_price * (1 - limit) - EPS:
            violations.append(f"{tag}: stop {first['price']} is looser than {limit:.0%} below entry {entry_price:.4f}")
        for a, b in zip(stops, stops[1:]):
            if b["price"] < a["price"] - EPS:
                violations.append(f"{tag}: stop widened from {a['price']} to {b['price']} ({b['event_id']})")
        if any(r["kind"] == "stop_set" and r["at"] < t["opened"] for r in rs):
            violations.append(f"{tag}: stop recorded before the entry does not count as protection")
        for r in entries + exits:
            if r["at"] - r["quote_at"] > max_age:
                violations.append(f"{tag}: {r['event_id']} used a quote {int((r['at'] - r['quote_at']).total_seconds() // 60)} minutes old")
    report = {}
    groups = defaultdict(list)
    for t in trades.values():
        groups[(t["mode"], t["arm"])].append(t)
    for (mode, arm), ts in sorted(groups.items()):
        opened = sorted((t for t in ts if t["opened"]), key=lambda t: t["opened"])
        for t in opened:
            overlapping = [u for u in opened if u["opened"] <= t["opened"] and (u["closed"] is None or u["closed"] > t["opened"])]
            if len(overlapping) > max_open:
                violations.append(f"{mode}/{arm}/{t['trade_id']}: {len(overlapping)} positions open at once (limit {max_open})")
        per_day = defaultdict(list)
        for t in opened:
            per_day[t["opened"].astimezone(PT).date()].append(t)
        for day, day_ts in sorted(per_day.items()):
            if len(day_ts) > max_entries:
                violations.append(f"{mode}/{arm}: {len(day_ts)} entries on {day} (limit {max_entries})")
            loss_times = [u["closed"] for u in day_ts if u["closed"] and u["proceeds"] - u["cost"] - (u["fees"] or 0) < 0]
            if loss_times:
                late = [u["trade_id"] for u in day_ts if u["opened"] > min(loss_times)]
                if late:
                    violations.append(f"{mode}/{arm}: entries after the first loss on {day}: {', '.join(late)}")
        closed = [t for t in opened if t["closed"]]
        accepted = sorted((t for t in closed if t["valid"] and t["fees"] is not None), key=lambda t: t["closed"])
        pnl = [t["proceeds"] - t["cost"] - t["fees"] for t in accepted]
        wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
        equity = peak = drawdown = 0.0
        for p in pnl:  # realized in closing order
            equity += p
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        r2 = lambda x: None if x is None else round(x, 2)  # noqa: E731
        report.setdefault(mode, {})[arm] = {
            "closed_trades": len(closed), "accepted_trades": len(accepted),
            "rejected_trades": [t["trade_id"] for t in ts if not t["valid"]],
            "unmeasured_fees_unknown": [t["trade_id"] for t in closed if t["valid"] and t["fees"] is None],
            "open_trades": [t["trade_id"] for t in opened if not t["closed"]],
            "win_rate": None if not accepted else round(len(wins) / len(accepted), 4),
            "avg_win": r2(sum(wins) / len(wins)) if wins else None, "avg_loss": r2(sum(losses) / len(losses)) if losses else None,
            "expectancy": r2(sum(pnl) / len(pnl)) if pnl else None, "net_after_fees": r2(sum(pnl)), "max_drawdown": r2(drawdown),
            "returns_pct": [round(((t["proceeds"] - t["fees"]) / t["cost"] - 1) * 100, 2) if t["cost"] else None for t in accepted],
        }
    return {"modes": report, "violations": violations, "rejected": rejected}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--max-open", type=int, default=1)
    ap.add_argument("--max-entries", type=int, default=2)
    ap.add_argument("--option-stop", type=float, default=0.15)
    ap.add_argument("--equity-stop", type=float, default=0.20)
    ap.add_argument("--max-quote-age-minutes", type=float, default=20.0)
    a = ap.parse_args(argv)
    try:
        _check_limits(a.max_open, a.max_entries, a.option_stop, a.equity_stop, a.max_quote_age_minutes)
    except ValueError as exc:
        print(json.dumps({"errors": [f"settings: {exc}"]}))
        return 2
    rows, errors = load(a.path)
    out = analyze(rows, a.max_open, a.max_entries, a.option_stop, a.equity_stop, a.max_quote_age_minutes)
    out["errors"] = errors
    out["sample_note"] = ("Fills are simulated from observed quotes (PAPER) or replayed history (REPLAY), never mixed. Statistics use accepted "
                          "trades only; a small sample proves little, and overlapping or resampled trades are not independent evidence.")
    print(json.dumps(out, indent=1, default=str))
    return 1 if errors or out["violations"] or out["rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
