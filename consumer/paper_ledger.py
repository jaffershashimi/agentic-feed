"""Paper-desk ledger checker: standard library only, read-only, no network, no broker.

Reads a paper fills CSV (one row per simulated event) and reports, per experiment arm: closed trades, win rate, average
win and loss, expectancy, net, worst running drawdown, and every rule breach against the limits given on the command
line. Open trades are listed but never counted as wins or losses. A malformed row is reported and skipped, never guessed.

    python3 -I consumer/paper_ledger.py fills.csv [--max-open 1] [--max-entries 2] [--option-stop 0.15] [--equity-stop 0.20]

CSV columns: event_id, arm, trade_id, kind (entry | exit | stop_set), occurred_at (ISO with offset), symbol,
instrument (equity | option | spread), side (buy | sell), qty, price, multiplier, decision_id.
A stop_set row carries the stop price in `price`; entries are long only (buy to open, sell to close).
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
STOP_GRACE = timedelta(minutes=5)  # a protective stop must be recorded within this long after the entry


def _row(raw, n):
    missing = [c for c in COLUMNS if not (raw.get(c) or "").strip()]
    if missing:
        raise ValueError(f"row {n}: missing {', '.join(missing)}")
    at = datetime.fromisoformat(raw["occurred_at"].replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError(f"row {n}: occurred_at needs a timezone offset")
    qty, price, mult = float(raw["qty"]), float(raw["price"]), float(raw["multiplier"])
    if not all(math.isfinite(x) for x in (qty, price, mult)) or qty <= 0 or price < 0 or mult <= 0:
        raise ValueError(f"row {n}: qty and multiplier must be positive and price non-negative")
    kind, side, inst = raw["kind"].strip(), raw["side"].strip(), raw["instrument"].strip()
    if kind not in ("entry", "exit", "stop_set") or side not in ("buy", "sell") or inst not in ("equity", "option", "spread"):
        raise ValueError(f"row {n}: unknown kind, side or instrument")
    if (kind, side) in (("entry", "sell"), ("exit", "buy")):
        raise ValueError(f"row {n}: the paper desk is long only (buy to open, sell to close)")
    return dict(raw, at=at, qty=qty, price=price, mult=mult, kind=kind, side=side, instrument=inst)


def load(path):
    rows, errors, seen = [], [], set()
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
            seen.add(row["event_id"])
            rows.append(row)
    rows.sort(key=lambda r: r["at"])
    return rows, errors


def analyze(rows, max_open=1, max_entries=2, option_stop=0.15, equity_stop=0.20):
    trades, violations = {}, []
    for r in rows:
        t = trades.setdefault((r["arm"], r["trade_id"]), {"arm": r["arm"], "trade_id": r["trade_id"], "symbol": r["symbol"],
                                                           "instrument": r["instrument"], "qty_in": 0.0,
                                                           "qty_out": 0.0, "cost": 0.0, "proceeds": 0.0, "opened": None, "closed": None,
                                                           "stops": [], "entry_price": None})
        if r["kind"] == "entry":
            t["qty_in"] += r["qty"]
            t["cost"] += r["qty"] * r["price"] * r["mult"]
            t["opened"] = t["opened"] or r["at"]
            t["entry_price"] = t["cost"] / (t["qty_in"] * r["mult"])
        elif r["kind"] == "exit":
            t["qty_out"] += r["qty"]
            t["proceeds"] += r["qty"] * r["price"] * r["mult"]
            if t["qty_out"] > t["qty_in"] + 1e-9:
                violations.append(f"{r['arm']}/{r['trade_id']}: sold more than was bought ({r['event_id']})")
            if abs(t["qty_out"] - t["qty_in"]) < 1e-9:
                t["closed"] = r["at"]
        else:
            t["stops"].append((r["at"], r["price"]))
    by_arm = defaultdict(list)
    for t in trades.values():
        by_arm[t["arm"]].append(t)
    report = {}
    for arm, ts in sorted(by_arm.items()):
        ts.sort(key=lambda t: t["opened"] or datetime.max.replace(tzinfo=PT))
        # Protective stop present, in time, and no looser than the limit.
        for t in ts:
            if t["opened"] is None:
                violations.append(f"{arm}/{t['trade_id']}: events without an entry")
                continue
            limit = equity_stop if t["instrument"] == "equity" else option_stop
            first = next((s for s in t["stops"] if s[0] - t["opened"] <= STOP_GRACE), None)
            if first is None:
                violations.append(f"{arm}/{t['trade_id']}: no protective stop within {int(STOP_GRACE.total_seconds() // 60)} minutes of entry")
            elif first[1] < t["entry_price"] * (1 - limit) - 1e-9:
                violations.append(f"{arm}/{t['trade_id']}: stop {first[1]} is looser than {limit:.0%} below entry {t['entry_price']:.4f}")
        # Concurrency, entries per day and stop-after-first-loss, per arm, on Pacific dates.
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
            losses = [u["closed"] for u in day_ts if u["closed"] and u["proceeds"] < u["cost"]]
            if losses:
                late = [u["trade_id"] for u in day_ts if u["opened"] > min(losses)]
                if late:
                    violations.append(f"{arm}: entries after the first loss on {day}: {', '.join(late)}")
        closed = [t for t in opened if t["closed"]]
        pnl = [t["proceeds"] - t["cost"] for t in closed]
        wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
        equity = peak = drawdown = 0.0
        for p in pnl:
            equity += p
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        r2 = lambda x: None if x is None else round(x, 2)  # noqa: E731
        report[arm] = {
            "closed_trades": len(closed), "open_trades": [t["trade_id"] for t in opened if not t["closed"]],
            "win_rate": None if not closed else round(len(wins) / len(closed), 4),
            "avg_win": r2(sum(wins) / len(wins)) if wins else None, "avg_loss": r2(sum(losses) / len(losses)) if losses else None,
            "expectancy": r2(sum(pnl) / len(pnl)) if pnl else None, "net": r2(sum(pnl)), "max_drawdown": r2(drawdown),
            "returns_pct": [round((t["proceeds"] / t["cost"] - 1) * 100, 2) if t["cost"] else None for t in closed],
        }
    return {"arms": report, "violations": violations}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--max-open", type=int, default=1)
    ap.add_argument("--max-entries", type=int, default=2)
    ap.add_argument("--option-stop", type=float, default=0.15)
    ap.add_argument("--equity-stop", type=float, default=0.20)
    a = ap.parse_args(argv)
    rows, errors = load(a.path)
    out = analyze(rows, a.max_open, a.max_entries, a.option_stop, a.equity_stop)
    out["errors"] = errors
    out["sample_note"] = "Paper fills are simulated from observed quotes; fewer than 30 closed trades per arm proves nothing."
    print(json.dumps(out, indent=1, default=str))
    return 1 if errors or out["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
