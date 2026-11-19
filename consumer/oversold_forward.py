"""S-042 forward observation (stress variant V3) on the Alpaca PAPER account. TEST ONLY, BROKER_PAPER class.

Runs once a day after the close (from the existing 2:15 PM review; no schedule of its own). It reads recent daily bars for a fixed
universe, computes the frozen S-042 V3 signal at the last completed session, manages exits for positions it opened, and queues
orders for the NEXT open. State lives on the broker: every entry carries client_order_id `s042v3-SYMBOL-YYMMDD`.

Protocol (frozen in Agentic-Lab system/06-research/specs/S-042-forward-protocol.md):
  * Signal at the close of day t: eligible stock, z-score of the 25-day deviation <= -2 and SPY 20-day realized vol above its 75th
    percentile of the previous 252 values (the exact functions of consumer/oversold_panel.py).
  * Entry: market buy, day order, queued for the next open, whole shares up to 1,000 USD, OTO stop at 90 percent of the signal close.
  * At most 10 open positions of this strategy; ranking when more signals: lowest RSI2, then most negative z, then symbol.
  * Exit decided at a close: close above SMA5, or the 5th session since entry (entry day counts as day 1). The stop is cancelled and a market
    sell is queued for the next open.
  * Paper host only, HALT kill switch, dry run unless --submit, and orders are only sent while the market is CLOSED (so queued orders
    fill at the next open, as the executable-exit analysis assumed). Stocks only, no shorting.
Safety nets added later (they only ever REMOVE candidates or add information):
  * data gate (consumer/data_gate.py): a candidate whose bars fail a quality check (unadjusted split look, gaps, bad prints) is quarantined.
  * corporate actions: a candidate with a split, merger or spin-off ex date in the next 7 days is skipped (fail-soft if the endpoint errors).
  * cost model (consumer/cost_model.py): each candidate carries an expected one-side cost estimate; report adds realised entry gap in bps.
Commands: signals (read only), run [--submit], report (closed trades and totals from the broker's own orders).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer import alpaca_paper as ap
from consumer import cost_model as cm
from consumer import data_gate as dg
from consumer import oversold_panel as op
from consumer.alpaca_paper import GuardError
from feed_pipeline.calendar import is_session, session_close

TAG = "s042v3"
POSITION_CAP = 20            # owner decision 2026-11-17: was 10, to reach the 100-trade gate faster on the paper account
NOTIONAL_USD = 3000.0        # owner decision 2026-11-17: was 1,000 USD per entry (paper account of about 100,000 USD)
STOP_FRACTION = 0.90
HISTORY_DAYS = 430
HOLD_DAYS = op.MAX_DAYS
# Owner approved 2026-11-17: execution may use any session. Evidence (engine backtest, 350 sampled trades) favours about
# 10:00 ET: a marketable limit placed in the regular session, because opening spreads cost about 32 bps and 10:00 ET about 9.
MORNING_START, MORNING_END = (9, 50), (11, 30)   # ET window in which morning mode may send orders
LIMIT_SLIP = 0.003            # marketable limit sits 30 bps through the quote
MAX_QUOTE_SPREAD = 0.01       # skip a name whose quoted spread is wider than 1 percent


def load_universe(path: Path) -> list:
    syms = sorted({ln.split("#")[0].strip().upper() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.split("#")[0].strip()})
    if not syms:
        raise GuardError("refused: the universe file is empty")
    return syms


def last_complete_session(now: datetime) -> date:
    """Latest session whose close is at least 16 minutes in the past (the SIP delay on the free plan plus a minute)."""
    d = now.astimezone(timezone.utc).date()
    for _ in range(10):
        if is_session(d) and session_close(d) + timedelta(minutes=16) <= now:
            return d
        d -= timedelta(days=1)
    raise GuardError("no completed session found in the last 10 days")


def fetch_daily(call, symbols, start: date, end: datetime) -> dict:
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), 25):
        tok = None
        while True:
            q = {"symbols": ",".join(syms[i:i + 25]), "timeframe": "1Day", "start": start.isoformat(), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "adjustment": "all", "feed": "sip", "limit": "10000", "sort": "asc"}
            if tok:
                q["page_token"] = tok
            j = call("GET", "/v2/stocks/bars?" + urllib.parse.urlencode(q), None, "data")
            for s, bs in (j.get("bars") or {}).items():
                d = out.setdefault(s, {"date": [], "o": [], "h": [], "l": [], "c": [], "v": []})
                for b in bs:
                    d["date"].append(b["t"][:10]); d["o"].append(float(b["o"])); d["h"].append(float(b["h"]))
                    d["l"].append(float(b["l"])); d["c"].append(float(b["c"])); d["v"].append(float(b["v"]))
            tok = j.get("next_page_token")
            if not tok:
                break
    return out


def trim(data: dict, asof: date) -> dict:
    """Drop bars after the as-of session (a partial bar of the current day must never be used)."""
    out = {}
    cut = asof.isoformat()
    for s, d in data.items():
        k = len(d["date"])
        while k > 0 and d["date"][k - 1] > cut:
            k -= 1
        out[s] = {key: vals[:k] for key, vals in d.items()}
    return out


def compute_candidates(data: dict, universe: list, asof: date) -> dict:
    """Frozen S-042 V2 and V3 signal on the last bar. Returns {'stress', 'candidates', 'stale', 'short_history'}."""
    spy = data.get("SPY")
    if not spy or spy["date"][-1] != asof.isoformat():
        raise GuardError(f"refused: SPY has no bar for {asof} (data not final or feed gap)")
    stress = op.stress_flags(spy["c"])[-1]
    uni = {asof.year: set(universe)}
    cands, stale, short = [], [], []
    for sym in universe:
        d = data.get(sym)
        if not d:
            continue
        if d["date"][-1] != asof.isoformat():
            stale.append(sym)
            continue
        if len(d["c"]) <= op.MIN_BARS:
            short.append(sym)
            continue
        ind = op.prepare(d)
        i = len(d["c"]) - 1
        d2 = dict(d)
        if not op.eligible(i, d2, ind, uni, sym):
            continue
        if not op.signal("V2", i, ind, d, stress):
            continue
        cand = {"sym": sym, "close": d["c"][i], "rsi2": ind["rsi2"][i], "z": ind["z"][i], "adv20": ind["adv20"][i], "v3": bool(stress)}
        cand["est_cost_bps"] = round(cm.expected_cost_bps(cand["close"], NOTIONAL_USD, cand["adv20"], cm.daily_vol(d["c"])), 2)
        cands.append(cand)
    cands.sort(key=lambda c: (c["rsi2"], c["z"], c["sym"]))
    # data-quality gate: a bad bar looks exactly like a perfect oversold signal, so check the series of every candidate
    quarantine = {}
    for c in cands:
        issues = dg.check_series(data[c["sym"]], asof)
        if issues:
            quarantine[c["sym"]] = issues
    kept = [c for c in cands if c["sym"] not in quarantine]
    return {"asof": asof.isoformat(), "stress": bool(stress), "v2_signals": len(kept), "candidates": [c for c in kept if c["v3"]],
            "stale": stale, "short_history": short, "quarantined": quarantine}


def plan_exits(held: list, data: dict, asof: date) -> list:
    """held: [{sym, qty, entry_date}]. Exit when the last close is above SMA5 or the 5th session since entry has been reached."""
    out = []
    for p in held:
        d = data.get(p["sym"])
        if not d or d["date"][-1] != asof.isoformat():
            out.append({**p, "action": "HOLD", "reason": "NO_FRESH_DATA"})
            continue
        n = len(d["c"])
        try:
            idx = d["date"].index(p["entry_date"])
        except ValueError:
            out.append({**p, "action": "HOLD", "reason": "ENTRY_DATE_NOT_IN_BARS"})
            continue
        hold = n - idx
        s5 = op.sma(d["c"], 5)[-1]
        if s5 is not None and d["c"][-1] > s5:
            out.append({**p, "action": "EXIT", "reason": "CLOSE_ABOVE_SMA5", "hold_days": hold})
        elif hold >= HOLD_DAYS:
            out.append({**p, "action": "EXIT", "reason": "TIME_5_SESSIONS", "hold_days": hold})
        else:
            out.append({**p, "action": "HOLD", "reason": "INSIDE_RULES", "hold_days": hold})
    return out


def entry_body(c: dict, asof: date) -> dict:
    qty = math.floor(NOTIONAL_USD / c["close"])
    if qty < 1:
        raise GuardError(f"{c['sym']}: one share costs more than {NOTIONAL_USD:.0f} USD")
    return {"symbol": c["sym"], "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "day", "order_class": "oto",
            "stop_loss": {"stop_price": f"{round(c['close'] * STOP_FRACTION, 2):.2f}"},
            "client_order_id": f"{TAG}-{c['sym']}-{asof.strftime('%y%m%d')}"}


def broker_state(call, tag: str = TAG) -> dict:
    positions = call("GET", "/v2/positions")
    opens = call("GET", "/v2/orders?status=open&limit=200&nested=true")
    closed = call("GET", "/v2/orders?status=closed&limit=500&nested=true&direction=desc")
    tagged = {}
    for o in closed:
        cid = o.get("client_order_id") or ""
        if cid.startswith(tag + "-") and float(o.get("filled_qty") or 0) > 0 and o.get("side") == "buy" and o["symbol"] not in tagged:
            tagged[o["symbol"]] = (o.get("filled_at") or "")[:10]
    held = []
    for p in positions:
        s = p.get("symbol")
        if s in tagged:
            # the entry day is the ET session date of the fill; filled_at is UTC, and market-open fills are the same calendar date
            held.append({"sym": s, "qty": int(float(p["qty"])), "entry_date": tagged[s]})
    return {"raw_book": positions, "open_orders": opens, "held": held}


def quotes(call, symbols: list) -> dict:
    """Latest bid and ask per symbol from the IEX feed (the free plan blocks recent SIP). Missing or crossed quotes are omitted."""
    out = {}
    if not symbols:
        return out
    raw = call("GET", "/v2/stocks/quotes/latest?feed=iex&symbols=" + ",".join(sorted(symbols)), host="data")
    for sym, q in (raw.get("quotes") or {}).items():
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if bid > 0 and ask >= bid:
            out[sym] = (bid, ask)
    return out


def in_morning_window(now: datetime) -> bool:
    et = now.astimezone(ZoneInfo("America/New_York"))
    return et.weekday() < 5 and MORNING_START <= (et.hour, et.minute) <= MORNING_END


def morning_limit(body: dict, quote, side: str):
    """Turn an order body into a marketable limit at the current quote, or return (None, reason) if the quote is unusable."""
    if not quote:
        return None, "NO_QUOTE"
    bid, ask = quote
    if (ask - bid) / ((ask + bid) / 2) > MAX_QUOTE_SPREAD:
        return None, "QUOTE_TOO_WIDE"
    px = ask * (1 + LIMIT_SLIP) if side == "buy" else bid * (1 - LIMIT_SLIP)
    out = {**body, "type": "limit", "limit_price": f"{round(px, 2):.2f}"}
    if body.get("order_class") == "oto":
        out["time_in_force"] = "gtc"   # a day entry gives a day stop that expires at 4 PM and leaves the position unprotected overnight (found 2026-11-17)
    return out, None


STOP_STATUSES = ("new", "accepted", "held", "pending_new")


def covered_qty(open_orders: list) -> dict:
    """symbol -> quantity covered by live sell stops (including stop legs nested in parent orders)."""
    cover = {}
    for o in open_orders:
        for x in [o] + (o.get("legs") or []):
            if x.get("side") == "sell" and x.get("type") in ("stop", "stop_limit", "trailing_stop") and x.get("status") in STOP_STATUSES:
                cover[x["symbol"]] = cover.get(x["symbol"], 0.0) + float(x.get("qty") or 0)
    return cover


def stop_reference(closed: list, sym: str, tag: str = TAG):
    """Stop price recorded on the newest filled tagged entry for `sym` (the stop leg keeps its price after it expires). None if there is none."""
    for o in closed:
        if (o.get("client_order_id") or "").startswith(tag + "-") and o.get("side") == "buy" and o.get("symbol") == sym and float(o.get("filled_qty") or 0) > 0:
            for leg in o.get("legs") or []:
                if leg.get("side") == "sell" and leg.get("stop_price"):
                    return float(leg["stop_price"])
    return None


def ensure_stops(call, st: dict, now: datetime, submit: bool, exclude=frozenset(), tag: str = TAG) -> dict:
    """Stop guard: every held S-042 position must have a live broker-side stop for its full quantity. A missing one is re-placed as a GTC stop at the stop price
    the entry recorded (the protocol's 10 percent below the signal close). Protective only: it never adds risk, so a gate block does not stop it."""
    cover = covered_qty(st["open_orders"])
    gaps = [(h["sym"], h["qty"] - int(cover.get(h["sym"], 0))) for h in st["held"] if h["sym"] not in exclude and h["qty"] - cover.get(h["sym"], 0) > 0]
    out = {"missing": len(gaps), "placed": [], "skipped": []}
    if not gaps or not submit:
        return out
    closed = call("GET", "/v2/orders?status=closed&limit=500&nested=true&direction=desc")
    for sym, qty in gaps:
        ref = stop_reference(closed, sym, tag)
        if ref is None:
            out["skipped"].append({"sym": sym, "why": "NO_STOP_REFERENCE"})
            continue
        body = {"symbol": sym, "qty": str(qty), "side": "sell", "type": "stop", "time_in_force": "gtc", "stop_price": f"{ref:.2f}",
                "client_order_id": f"{tag}s-{sym}-{now.strftime('%y%m%d')}"}
        try:
            resp = call("POST", "/v2/orders", body)
            out["placed"].append({"sym": sym, "id": resp.get("id"), "status": resp.get("status")})
        except ap.GuardError as exc:
            out["skipped"].append({"sym": sym, "why": str(exc)[:80]})
    return out


TERMINAL_STATUSES = ("canceled", "filled", "expired", "rejected", "done_for_day", "replaced")


def cancel_and_wait(call, ids, timeout: float = 10.0, sleep=time.sleep, clock=time.monotonic) -> None:
    """Cancel orders and wait until the broker confirms. A cancel returns at once, but the shares stay reserved until it is processed, so an exit sent
    right away can be refused (found 2026-11-19: the first live exit cancelled the stop, was refused, and left the position without a stop)."""
    for oid in ids:
        call("DELETE", f"/v2/orders/{oid}")
    deadline = clock() + timeout
    for oid in ids:
        while True:
            try:
                status = (call("GET", f"/v2/orders/{oid}") or {}).get("status")
            except GuardError:
                break  # no longer readable: treat as gone
            if status in TERMINAL_STATUSES or clock() >= deadline:
                break
            sleep(0.5)


def symbol_sell_ids(st: dict, sym: str) -> list:
    """Ids of the open sell orders for `sym` (the stop), including stop legs nested in a parent order, without repeats."""
    ids = []
    for o in st["open_orders"]:
        if o.get("symbol") == sym and o.get("side") == "sell":
            ids.append(o["id"])
        for leg in (o.get("legs") or []):
            if leg.get("symbol") == sym and leg.get("side") == "sell":
                ids.append(leg["id"])
    return list(dict.fromkeys(ids))


def send_exit(call, st: dict, sym: str, body: dict, leaving, now: datetime, out: dict, state_dir=None, tag: str = TAG, sleep=time.sleep):
    """Cancel the position's stop, wait for the broker to confirm, send the exit. If the exit is refused, put the stop back at once and record the refusal
    (a position must never be left without its stop). Returns the broker response or None."""
    cancel_and_wait(call, symbol_sell_ids(st, sym), sleep=sleep)
    try:
        return call("POST", "/v2/orders", body)
    except GuardError as exc:
        if state_dir is not None:
            ap.log_event(state_dir, {"action": "exit-refused", "sym": sym, "error": str(exc)[:300]})
        out["skipped"].append({"sym": sym, "why": "ORDER_REFUSED", "detail": str(exc).replace(sym, "<sym>")[:160]})
        out["stop_restore"] = ensure_stops(call, broker_state(call, tag), now, True, exclude=set(leaving) - {sym}, tag=tag)
        return None


def run(call, universe: list, now: datetime, state_dir: Path, submit: bool, fetch=None, mode: str = "close", entries_allowed: bool = True) -> dict:
    """entries_allowed=False is the pre-trade gate's partial block: exits still go out (getting out must always be possible), new entries are skipped and named."""
    ap.check_not_halted(state_dir)
    asof = last_complete_session(now)
    start = asof - timedelta(days=HISTORY_DAYS)
    data = trim((fetch or fetch_daily)(call, sorted(set(universe) | {"SPY"}), start, now - timedelta(minutes=20)), asof)
    sig = compute_candidates(data, universe, asof)
    ca = dg.upcoming_actions(call, [c["sym"] for c in sig["candidates"]], asof) if sig["candidates"] else {"actions": {}, "error": None}
    st = broker_state(call)
    clock = call("GET", "/v2/clock")
    exits = plan_exits(st["held"], data, asof)
    leaving = {e["sym"] for e in exits if e["action"] == "EXIT"}
    busy = {p["symbol"] for p in st["raw_book"]} | {o.get("symbol") for o in st["open_orders"]}
    pending_entries = {o.get("symbol") for o in st["open_orders"] if (o.get("client_order_id") or "").startswith(TAG + "-") and o.get("side") == "buy"}
    open_count = len({h["sym"] for h in st["held"]} - leaving) + len(pending_entries)
    slots = max(0, POSITION_CAP - open_count)
    chosen, skipped = [], []
    for c in sig["candidates"]:
        if c["sym"] in ca["actions"]:
            skipped.append({"sym": c["sym"], "why": "CORPORATE_ACTION_SOON"})
        elif c["sym"] in busy:
            skipped.append({"sym": c["sym"], "why": "ALREADY_HELD_OR_PENDING"})
        elif len(chosen) < slots:
            try:
                chosen.append((c, entry_body(c, asof)))
            except GuardError as exc:
                skipped.append({"sym": c["sym"], "why": str(exc)})
        else:
            skipped.append({"sym": c["sym"], "why": "NO_FREE_SLOT"})
    market_open = bool(clock.get("is_open"))
    out = {"asof": sig["asof"], "stress": sig["stress"], "v2_signals": sig["v2_signals"], "v3_signals": len(sig["candidates"]), "slots": slots,
           "market_open": market_open, "submit": bool(submit), "exits": [{k: e[k] for k in ("sym", "action", "reason") if k in e} for e in exits],
           "entries": [b for _, b in chosen], "skipped": skipped, "stale_symbols": len(sig["stale"]), "orders": [],
           "quarantined": sig["quarantined"], "corporate_actions_error": ca["error"],
           "est_cost_bps": {c["sym"]: c["est_cost_bps"] for c, _ in chosen}}
    if not submit:
        out["note"] = "dry run: nothing was sent"
        return out
    morning = mode == "morning"
    out["mode"] = mode
    if morning and not (market_open and in_morning_window(now)):
        out["note"] = "morning mode: outside the 09:50-11:30 ET window or the market is closed, nothing sent"
        return out
    if not morning and market_open:
        out["note"] = "market is open: nothing sent (orders are only queued while the market is closed)"
        return out
    qs = quotes(call, [e["sym"] for e in exits if e["action"] == "EXIT"] + [c["sym"] for c, _ in chosen]) if morning else {}
    for e in exits:
        if e["action"] != "EXIT":
            continue
        body = {"symbol": e["sym"], "qty": str(e["qty"]), "side": "sell", "type": "market", "time_in_force": "day",
                "client_order_id": f"{TAG}x-{e['sym']}-{asof.strftime('%y%m%d')}"}
        if morning:
            body, why = morning_limit(body, qs.get(e["sym"]), "sell")
            if body is None:
                out["skipped"].append({"sym": e["sym"], "why": "EXIT_" + why})  # held with its stop untouched, retried on the next run
                continue
        resp = send_exit(call, st, e["sym"], body, leaving, now, out, state_dir)
        if resp is None:
            continue
        ap.log_event(state_dir, {"action": "s042-exit", "sym": e["sym"], "reason": e["reason"], "order_id": resp.get("id")})
        out["orders"].append({"sym": e["sym"], "side": "sell", "id": resp.get("id"), "status": resp.get("status")})
    out["stops"] = ensure_stops(call, st, now, True, exclude=leaving)
    for c, body in chosen:
        if not entries_allowed:
            out["skipped"].append({"sym": c["sym"], "why": "ENTRY_BLOCKED_BY_GATE"})
            continue
        if morning:
            body, why = morning_limit(body, qs.get(c["sym"]), "buy")
            if body is None:
                out["skipped"].append({"sym": c["sym"], "why": "ENTRY_" + why})
                continue
        try:
            resp = call("POST", "/v2/orders", body)
        except GuardError as exc:
            out["skipped"].append({"sym": c["sym"], "why": "ORDER_REFUSED", "detail": str(exc).replace(c["sym"], "<sym>")[:160]})
            continue
        ap.log_event(state_dir, {"action": "s042-entry", "sym": c["sym"], "z": c["z"], "rsi2": c["rsi2"], "order_id": resp.get("id")})
        out["orders"].append({"sym": c["sym"], "side": "buy", "id": resp.get("id"), "status": resp.get("status")})
    return out


def closed_trades(call, tag: str = TAG, stop_fraction: float = STOP_FRACTION) -> tuple:
    """(trades, entry_orders). Every closed trade of this strategy from the broker's own orders: tagged entry, then its filled stop leg or its tagged exit sell."""
    closed = call("GET", "/v2/orders?status=closed&limit=500&nested=true&direction=asc")
    entries = [o for o in closed if (o.get("client_order_id") or "").startswith(tag + "-") and o.get("side") == "buy" and float(o.get("filled_qty") or 0) > 0]
    exits = [o for o in closed if (o.get("client_order_id") or "").startswith((tag + "x-", tag + "s-")) and o.get("side") == "sell" and float(o.get("filled_qty") or 0) > 0]
    used, trades = set(), []
    for e in sorted(entries, key=lambda o: o.get("filled_at") or ""):
        qty, px = float(e["filled_qty"]), float(e["filled_avg_price"])
        leg = next((l for l in (e.get("legs") or []) if l.get("side") == "sell" and float(l.get("filled_qty") or 0) > 0), None)
        how, ex = (("STOP", leg) if leg else (None, None))
        if ex is None:
            ex = next((o for o in exits if o["id"] not in used and o["symbol"] == e["symbol"] and (o.get("filled_at") or "") > (e.get("filled_at") or "")), None)
            how = "RULE_EXIT"
            if ex is not None:
                used.add(ex["id"])
                if (ex.get("client_order_id") or "").startswith(tag + "s-"):
                    how = "STOP"
        if ex is None:
            continue
        xp = float(ex["filled_avg_price"])
        gap = None
        stop_leg = next((l for l in (e.get("legs") or []) if l.get("side") == "sell" and l.get("stop_price")), None)
        if stop_leg:  # the stop sits at 90 percent of the signal close, so the signal close is recoverable from the order itself
            gap = round(cm.realized_slippage_bps("buy", float(stop_leg["stop_price"]) / stop_fraction, px), 1)
        trades.append({"sym": e["symbol"], "entry": px, "exit": xp, "qty": qty, "how": how, "pnl": round((xp - px) * qty, 2), "ret_pct": round(100 * (xp / px - 1), 3),
                       "entry_gap_bps": gap, "mode": "morning_limit" if e.get("type") == "limit" else "market_at_open",
                       "entry_date": (e.get("filled_at") or "")[:10], "exit_date": (ex.get("filled_at") or "")[:10]})
    return trades, entries


def report(call, tag: str = TAG, stop_fraction: float = STOP_FRACTION) -> dict:
    """Closed trades of this strategy (see closed_trades) with totals."""
    trades, entries = closed_trades(call, tag, stop_fraction)
    n = len(trades)
    held = [p for p in call("GET", "/v2/positions") if any((e["symbol"] == p.get("symbol")) for e in entries)]
    return {"closed_trades": n, "total_pnl": round(sum(t["pnl"] for t in trades), 2), "mean_ret_pct": round(sum(t["ret_pct"] for t in trades) / n, 3) if n else None,
            "stops": sum(1 for t in trades if t["how"] == "STOP"), "open_positions": len(held), "trades": trades[-10:],
            "entry_gap_vs_signal_close_bps": cm.summarize([t["entry_gap_bps"] for t in trades if t["entry_gap_bps"] is not None])}


def main(argv=None, call=None, now=None, fetch=None) -> int:
    p = argparse.ArgumentParser(description="S-042 stress-variant forward observation on the Alpaca paper account.")
    p.add_argument("--state-dir", default=os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    pr = sub.add_parser("protect", help="stop guard: re-place any missing broker-side stop (dry run unless --submit)")
    pr.add_argument("--submit", action="store_true")
    for name in ("signals", "run"):
        s = sub.add_parser(name)
        s.add_argument("--universe", required=True)
        if name == "run":
            s.add_argument("--submit", action="store_true")
            s.add_argument("--exits-only", action="store_true", help="send exits but no new entries (used when the pre-trade gate blocks entries)")
            s.add_argument("--mode", choices=("close", "morning"), default="close",
                           help="close: queue market orders for the next open (old). morning: marketable limits at about 10:00 ET (owner-approved).")
    args = p.parse_args(argv)
    try:
        call = call or ap.make_client()
        now = now or datetime.now(timezone.utc)
        if args.cmd == "protect":
            print(json.dumps(ensure_stops(call, broker_state(call), now, args.submit), indent=1, sort_keys=True))
            return 0
        if args.cmd == "report":
            print(json.dumps(report(call), indent=1, sort_keys=True))
            return 0
        universe = load_universe(Path(args.universe))
        if args.cmd == "signals":
            asof = last_complete_session(now)
            data = trim((fetch or fetch_daily)(call, sorted(set(universe) | {"SPY"}), asof - timedelta(days=HISTORY_DAYS), now - timedelta(minutes=20)), asof)
            sig = compute_candidates(data, universe, asof)
            result = {k: sig[k] for k in ("asof", "stress", "v2_signals", "quarantined")} | {"v3_signals": len(sig["candidates"]), "top": sig["candidates"][:15], "stale_symbols": len(sig["stale"])}
        else:
            result = run(call, universe, now, Path(args.state_dir), args.submit, fetch, args.mode, entries_allowed=not args.exits_only)
    except GuardError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(result, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
