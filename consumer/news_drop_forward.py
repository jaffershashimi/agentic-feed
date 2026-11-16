"""S-044 forward data collection: each day after the close, find big single-stock drops, classify them NEWS / ANALYST / NO_NEWS, and log them. Read only.

This is the learning loop for the news-drop idea. It writes one JSON line per event to a private log so the pre-registered S-044 rule can be re-tested on data
collected live (and so the 5 and 10 session outcomes can be filled in later by `outcomes`). It places no orders. Orders for S-044, if the study licenses any,
go through a separate guarded step added only after the owner-visible verdict.

Event rule (same as the frozen spec): close/prior close - 1 <= -8%, prior close >= 10 dollars, 20-day average dollar volume >= 20 million, SPY return on the day > -2%.
  python3 -m consumer.news_drop_forward scan --universe U.txt --log events.jsonl
  python3 -m consumer.news_drop_forward outcomes --log events.jsonl
  python3 -m consumer.news_drop_forward run --universe U.txt [--submit]      # S-044F paper observation of the NO_NEWS rebound (BROKER_PAPER)
  python3 -m consumer.news_drop_forward report                               # closed S-044F trades from the broker's own orders

S-044F protocol (frozen in Agentic-Lab system/06-research/specs/S-044-forward-protocol.md): NO_NEWS drops only, long, market buy queued for the next open, OTO stop 12 percent
below the signal close, 5,000 USD notional cap and the tier-2 risk limits of consumer/paper_risk.py, at most 6 open, highest 20-day dollar volume first, exit decided at the close of
the 10th session (entry day is session 1) and sold at the next open. Orders only while the market is CLOSED, paper host only, HALT file respected.
"""
import argparse
import json
import math
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import alpaca_paper as ap
from consumer import news_drop_study as nds
from consumer import news_events as ne
from consumer import oversold_forward as of
from consumer import oversold_panel as op
from consumer import paper_risk as pr

ET = ZoneInfo("America/New_York")
DROP = -0.08
TAG = "s044n"
STOP_FRACTION = 0.88
NOTIONAL_CAP = 5000.0
HOLD_SESSIONS = 10
MAX_OPEN = pr.MAX_OPEN_TRADES
MIN_PRICE = 10.0
MIN_ADV = 2e7


def find_drops(data, asof):
    """Drop events on the as-of session from bars (already trimmed to asof). SPY must be present."""
    spy = data.get("SPY")
    if not spy or spy["date"][-1] != asof.isoformat() or len(spy["c"]) < 2:
        raise ap.GuardError(f"refused: SPY has no bar for {asof}")
    spy_ret = spy["c"][-1] / spy["c"][-2] - 1.0
    out = []
    if spy_ret <= -0.02:
        return out, spy_ret
    for sym, d in data.items():
        if sym == "SPY" or len(d["c"]) < 30 or d["date"][-1] != asof.isoformat():
            continue
        r = d["c"][-1] / d["c"][-2] - 1.0
        dv = [c * v for c, v in zip(d["c"][-21:-1], d["v"][-21:-1])]
        adv = sum(dv) / len(dv) if dv else 0.0
        if r <= DROP and d["c"][-2] >= MIN_PRICE and adv >= MIN_ADV:
            out.append({"sym": sym, "date": asof.isoformat(), "prev": d["date"][-2], "ret": r, "close": d["c"][-1], "adv20": adv})
    return sorted(out, key=lambda e: e["ret"]), spy_ret


def news_for(call, events, asof):
    by = {}
    for e in events:
        a = datetime.fromisoformat(e["prev"]).replace(hour=16, tzinfo=ET).astimezone(timezone.utc)
        b = datetime.fromisoformat(e["date"]).replace(hour=16, tzinfo=ET).astimezone(timezone.utc)
        by[e["sym"]] = list(ne.fetch_news(call, a.strftime("%Y-%m-%dT%H:%M:%SZ"), b.strftime("%Y-%m-%dT%H:%M:%SZ"), [e["sym"]], sleep=0.2))
    return by


def scan(call, universe, now, fetch=None, news=None):
    asof = of.last_complete_session(now)
    start = asof - timedelta(days=60)
    data = of.trim((fetch or of.fetch_daily)(call, sorted(set(universe) | {"SPY"}), start, now - timedelta(minutes=20)), asof)
    events, spy_ret = find_drops(data, asof)
    items = (news or news_for)(call, events, asof)
    for e in events:
        e["group"], e["sub"] = nds.classify(e["sym"], items.get(e["sym"], []))
        e["headline"] = next((n["headline"] for n in sorted(items.get(e["sym"], []), key=lambda n: n["created_at"]) if ne.is_informative(n)), None)
    return {"asof": asof.isoformat(), "spy_ret": spy_ret, "events": events}


def append_new(log, result):
    log = Path(log)
    have = {(json.loads(x)["sym"], json.loads(x)["date"]) for x in log.read_text().splitlines() if x.strip()} if log.exists() else set()
    n = 0
    with open(log, "a", encoding="utf-8") as fh:
        for e in result["events"]:
            if (e["sym"], e["date"]) not in have:
                fh.write(json.dumps({**e, "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
                n += 1
    return n


def outcomes(call, log, now, fetch=None):
    """Fill in market-adjusted 5 and 10 session returns (open of t+1 to close of t+k, minus SPY) for logged events that are old enough."""
    rows = [json.loads(x) for x in Path(log).read_text().splitlines() if x.strip()] if Path(log).exists() else []
    need = [r for r in rows if "o1_10" not in r]
    if not need:
        return {"updated": 0, "events": len(rows)}
    first = min(r["date"] for r in need)
    start = datetime.fromisoformat(first).date() - timedelta(days=3)
    syms = sorted({r["sym"] for r in need} | {"SPY"})
    data = (fetch or of.fetch_daily)(call, syms, start, now - timedelta(minutes=20))
    spy = data["SPY"]
    upd = 0
    for r in rows:
        if "o1_10" in r or r["sym"] not in data:
            continue
        d = data[r["sym"]]
        if r["date"] not in d["date"] or r["date"] not in spy["date"]:
            continue
        i, si = d["date"].index(r["date"]), spy["date"].index(r["date"])
        if i + 10 >= len(d["c"]) or si + 10 >= len(spy["c"]):
            continue
        for k in (5, 10):
            r[f"o1_{k}"] = (d["c"][i + k] / d["o"][i + 1] - 1.0) - (spy["c"][si + k] / spy["o"][si + 1] - 1.0)
        upd += 1
    if upd:
        Path(log).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return {"updated": upd, "events": len(rows)}


FUND_WORDS = ("etf", "etn", "fund", "trust", "proshares", "direxion", "ultra", "bull", "bear", "2x", "3x", "-1x", "leveraged", "inverse", "daily", "index", "shares s&p")


def is_operating_company(call, sym):
    """The S-044 idea is about companies, not funds: a leveraged ETF down 8% is not a stock-specific news drop. Lookup failures count as NOT a company (skip)."""
    try:
        name = (call("GET", f"/v2/assets/{sym}") or {}).get("name") or ""
    except Exception:
        return False
    low = name.lower()
    return bool(name) and not any(w in low for w in FUND_WORDS)


def entry_body(e, asof):
    """Market buy for the next open with an OTO stop. Shares from the tier-2 limits, further capped at NOTIONAL_CAP."""
    qty = min(pr.stock_shares(e["close"], STOP_FRACTION), math.floor(NOTIONAL_CAP / e["close"]))
    if qty < 1:
        raise pr.RiskError(f"{e['sym']}: one share exceeds the notional cap")
    return {"symbol": e["sym"], "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "day", "order_class": "oto",
            "stop_loss": {"stop_price": f"{round(e['close'] * STOP_FRACTION, 2):.2f}"}, "client_order_id": f"{TAG}-{e['sym']}-{asof.strftime('%y%m%d')}"}


def plan_exits(held, data, asof):
    out = []
    for p in held:
        d = data.get(p["sym"])
        if not d or d["date"][-1] != asof.isoformat() or p["entry_date"] not in d["date"]:
            out.append({**p, "action": "HOLD", "reason": "NO_FRESH_DATA"})
            continue
        hold = len(d["c"]) - d["date"].index(p["entry_date"])
        out.append({**p, "action": "EXIT" if hold >= HOLD_SESSIONS else "HOLD", "reason": "TIME_10_SESSIONS" if hold >= HOLD_SESSIONS else "INSIDE_RULES", "hold_days": hold})
    return out


def run(call, universe, now, state_dir, submit, fetch=None, news=None):
    ap.check_not_halted(state_dir)
    sig = scan(call, universe, now, fetch, news)
    asof = datetime.fromisoformat(sig["asof"]).date()
    st = of.broker_state(call, TAG)
    held_syms = [h["sym"] for h in st["held"]]
    start = asof - timedelta(days=40)
    data = of.trim((fetch or of.fetch_daily)(call, sorted(set(held_syms) | {"SPY"}), start, now - timedelta(minutes=20)), asof) if held_syms else {}
    exits = plan_exits(st["held"], data, asof)
    leaving = {x["sym"] for x in exits if x["action"] == "EXIT"}
    pending = {o.get("symbol") for o in st["open_orders"] if (o.get("client_order_id") or "").startswith(TAG + "-") and o.get("side") == "buy"}
    busy = {p["symbol"] for p in st["raw_book"]} | {o.get("symbol") for o in st["open_orders"]}
    avg = {p["symbol"]: float(p.get("avg_entry_price") or 0) for p in st["raw_book"]}
    open_risks = [h["qty"] * avg.get(h["sym"], 0.0) * (1 - STOP_FRACTION) for h in st["held"] if h["sym"] not in leaving] + [pr.MAX_LOSS_PER_TRADE / 4] * len(pending)
    clock = call("GET", "/v2/clock")
    chosen, skipped = [], []
    cands = sorted((e for e in sig["events"] if e["group"] == "NO_NEWS"), key=lambda e: -e["adv20"])
    for e in cands:
        try:
            if e["sym"] in busy:
                raise pr.RiskError("ALREADY_HELD_OR_PENDING")
            if not is_operating_company(call, e["sym"]):
                raise pr.RiskError("NOT_AN_OPERATING_COMPANY")
            body = entry_body(e, asof)
            risk = int(body["qty"]) * e["close"] * (1 - STOP_FRACTION)
            pr.check_new_trade(risk, open_risks, 0.0)
            open_risks.append(risk)
            chosen.append((e, body))
        except pr.RiskError as exc:
            skipped.append({"sym": e["sym"], "why": str(exc)})
    out = {"asof": sig["asof"], "drops": len(sig["events"]), "no_news": len(cands), "news_logged_only": sum(1 for e in sig["events"] if e["group"] != "NO_NEWS"),
           "market_open": bool(clock.get("is_open")), "submit": bool(submit), "exits": [{k: x[k] for k in ("sym", "action", "reason") if k in x} for x in exits],
           "entries": [b for _, b in chosen], "skipped": skipped, "orders": []}
    if not submit:
        out["note"] = "dry run: nothing was sent"
        return out
    if out["market_open"]:
        out["note"] = "market is open: nothing sent (orders are only queued while the market is closed)"
        return out
    for x in exits:
        if x["action"] != "EXIT":
            continue
        for o in st["open_orders"]:
            if o.get("symbol") == x["sym"] and o.get("side") == "sell":
                call("DELETE", f"/v2/orders/{o['id']}")
            for leg in (o.get("legs") or []):
                if leg.get("symbol") == x["sym"] and leg.get("side") == "sell":
                    call("DELETE", f"/v2/orders/{leg['id']}")
        resp = call("POST", "/v2/orders", {"symbol": x["sym"], "qty": str(x["qty"]), "side": "sell", "type": "market", "time_in_force": "day", "client_order_id": f"{TAG}x-{x['sym']}-{asof.strftime('%y%m%d')}"})
        ap.log_event(state_dir, {"action": "s044n-exit", "sym": x["sym"], "order_id": resp.get("id")})
        out["orders"].append({"sym": x["sym"], "side": "sell", "id": resp.get("id")})
    for e, body in chosen:
        resp = call("POST", "/v2/orders", body)
        ap.log_event(state_dir, {"action": "s044n-entry", "sym": e["sym"], "drop": e["ret"], "order_id": resp.get("id")})
        out["orders"].append({"sym": e["sym"], "side": "buy", "id": resp.get("id")})
    return out


def main(argv=None, call=None, now=None, fetch=None, news=None):
    p = argparse.ArgumentParser(description="S-044 forward data collection (read only).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--universe", required=True)
    s.add_argument("--log", required=True)
    o = sub.add_parser("outcomes")
    o.add_argument("--log", required=True)
    r = sub.add_parser("run")
    r.add_argument("--universe", required=True)
    r.add_argument("--submit", action="store_true")
    r.add_argument("--log", default=None)
    r.add_argument("--state-dir", default=os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper"))
    sub.add_parser("report")
    a = p.parse_args(argv)
    try:
        call = call or ap.make_client()
        now = now or datetime.now(timezone.utc)
        if a.cmd == "scan":
            res = scan(call, of.load_universe(Path(a.universe)), now, fetch, news)
            res["logged_new"] = append_new(a.log, res)
        elif a.cmd == "run":
            res = run(call, of.load_universe(Path(a.universe)), now, Path(a.state_dir), a.submit, fetch, news)
        elif a.cmd == "report":
            res = of.report(call, TAG, STOP_FRACTION)
        else:
            res = outcomes(call, a.log, now, fetch)
    except ap.GuardError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
