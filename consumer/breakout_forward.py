"""S-048 B55M forward observation on the Alpaca PAPER account (owner exception 2026-11-17: the backtest was INCONCLUSIVE, so the evidence comes from a small forward test).

The decision code is the engine's Breakout("B55M") plugin, the same code the backtest ran. Rules as frozen in Agentic-Lab system/06-research/specs/S-048-daily-breakout.md and
S-048-forward-protocol.md: a close above the prior 55-session closing high and above SMA200 while SPY is above its SMA200; 1,000 USD per entry, at most 20 positions; a GTC broker-side stop
at 90 percent of the signal close; exit at a close below the prior 20-session low or on the 40th session; orders are marketable limits near 10:00 ET (the same morning window as S-042).

  python3 -m consumer.breakout_forward run --universe FILE [--submit] [--exits-only]
  python3 -m consumer.breakout_forward report
Paper host only (the shared client enforces it). Tag: s048b55m.
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from consumer import alpaca_paper as ap
from consumer import data_gate as dg
from consumer import oversold_forward as of
from consumer import oversold_panel as op
from engine.strategies.breakout import Breakout
from engine.strategy import Context
from engine.types import Position

TAG = "s048b55m"
VARIANT = "B55M"
POSITION_CAP = 20
NOTIONAL_USD = 1000.0
STOP_FRACTION = 0.90
HOLD_SESSIONS = 40
HALT_USD = -2500.0     # owner decision 2026-11-17: no new entries while this strategy's total result (closed plus open) is at or below this; exits and stops continue


def sessions_since(data, sym, entry_date, asof):
    """Sessions from the entry date to asof inclusive (the entry day counts as 1), from the symbol's own bars."""
    return sum(1 for d in data[sym]["date"] if entry_date <= d <= asof.isoformat())


def build_context(data, universe, asof):
    inds = {s: op.prepare(d) for s, d in data.items() if s != "SPY"}
    idx = {s: {dt: i for i, dt in enumerate(d["date"])} for s, d in data.items()}
    return Context(data, idx, inds, {asof.year: set(universe)}, asof.isoformat(), False)


def entry_body(intent, asof):
    qty = math.floor(NOTIONAL_USD / intent.signal_close)
    if qty < 1:
        raise ap.GuardError(f"{intent.symbol}: one share costs more than {NOTIONAL_USD:.0f} USD")
    return {"symbol": intent.symbol, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "day", "order_class": "oto",
            "stop_loss": {"stop_price": f"{round(intent.signal_close * STOP_FRACTION, 2):.2f}"}, "client_order_id": f"{TAG}-{intent.symbol}-{asof.strftime('%y%m%d')}"}


def plan_exits(held, ctx, data, strategy, asof):
    out = []
    for h in held:
        if h["sym"] not in data or ctx.i(h["sym"]) is None:
            out.append({**h, "action": "HOLD", "reason": "NO_BAR_TODAY"})   # missing or stale data never forces an exit
            continue
        pos = Position(TAG, h["sym"], h["qty"], h["entry_date"], 0.0, 0.0, sessions_since(data, h["sym"], h["entry_date"], asof))
        reason = strategy.exit_reason(pos, ctx)
        out.append({**h, "action": "EXIT" if reason else "HOLD", "reason": reason or "INSIDE_RULES", "hold_days": pos.sessions_held})
    return out


def total_result_usd(call, st):
    """Closed result from the broker's orders plus the unrealized result of the positions this strategy holds."""
    trades, _ = of.closed_trades(call, TAG)
    mine = {h["sym"] for h in st["held"]}
    open_pl = sum(float(p.get("unrealized_pl") or 0) for p in st["raw_book"] if p.get("symbol") in mine)
    return round(sum(t["pnl"] for t in trades) + open_pl, 2)


def run(call, universe, now, state_dir, submit, fetch=None, mode="morning", entries_allowed=True):
    ap.check_not_halted(state_dir)
    asof = of.last_complete_session(now)
    data = of.trim((fetch or of.fetch_daily)(call, sorted(set(universe) | {"SPY"}), asof - timedelta(days=of.HISTORY_DAYS), now - timedelta(minutes=20)), asof)
    if "SPY" not in data or asof.isoformat() not in data["SPY"]["date"]:
        raise ap.GuardError(f"refused: SPY has no bar for {asof} (data not final or feed gap)")
    ctx = build_context(data, universe, asof)
    strategy = Breakout(VARIANT)
    intents = sorted(strategy.signals(ctx), key=lambda x: x.rank)
    st = of.broker_state(call, TAG)
    clock = call("GET", "/v2/clock")
    halted = total_result_usd(call, st) <= HALT_USD
    if halted:
        entries_allowed = False
    exits = plan_exits(st["held"], ctx, data, strategy, asof)
    leaving = {e["sym"] for e in exits if e["action"] == "EXIT"}
    busy = {p["symbol"] for p in st["raw_book"]} | {o.get("symbol") for o in st["open_orders"]}
    pending = {o.get("symbol") for o in st["open_orders"] if (o.get("client_order_id") or "").startswith(TAG + "-") and o.get("side") == "buy"}
    slots = max(0, POSITION_CAP - (len({h["sym"] for h in st["held"]} - leaving) + len(pending)))
    ca = dg.upcoming_actions(call, [i.symbol for i in intents], asof) if intents else {"actions": {}, "error": None}
    chosen, skipped = [], []
    for it in intents:
        if it.symbol in ca["actions"]:
            skipped.append({"sym": it.symbol, "why": "CORPORATE_ACTION_SOON"})
        elif it.symbol in busy:
            skipped.append({"sym": it.symbol, "why": "ALREADY_HELD_OR_PENDING"})
        elif len(chosen) < slots:
            try:
                chosen.append(entry_body(it, asof))
            except ap.GuardError as exc:
                skipped.append({"sym": it.symbol, "why": str(exc)})
        else:
            skipped.append({"sym": it.symbol, "why": "NO_FREE_SLOT"})
    out = {"asof": asof.isoformat(), "variant": VARIANT, "signals": len(intents), "slots": slots, "market_open": bool(clock.get("is_open")), "submit": bool(submit), "mode": mode,
           "exits": [{k: e[k] for k in ("sym", "action", "reason") if k in e} for e in exits], "entries": chosen, "skipped": skipped, "orders": [], "corporate_actions_error": ca["error"], "halted": halted}
    if not submit:
        out["note"] = "dry run: nothing was sent"
        return out
    if not (out["market_open"] and of.in_morning_window(now)):
        out["note"] = "outside the 09:50-11:30 ET window or the market is closed, nothing sent"
        return out
    qs = of.quotes(call, [e["sym"] for e in exits if e["action"] == "EXIT"] + [b["symbol"] for b in chosen])
    for e in exits:
        if e["action"] != "EXIT":
            continue
        for o in st["open_orders"]:
            if o.get("symbol") == e["sym"] and o.get("side") == "sell":
                call("DELETE", f"/v2/orders/{o['id']}")
            for leg in (o.get("legs") or []):
                if leg.get("symbol") == e["sym"] and leg.get("side") == "sell":
                    call("DELETE", f"/v2/orders/{leg['id']}")
        body, why = of.morning_limit({"symbol": e["sym"], "qty": str(e["qty"]), "side": "sell", "type": "market", "time_in_force": "day",
                                      "client_order_id": f"{TAG}x-{e['sym']}-{asof.strftime('%y%m%d')}"}, qs.get(e["sym"]), "sell")
        if body is None:
            out["skipped"].append({"sym": e["sym"], "why": "EXIT_" + why})
            continue
        resp = call("POST", "/v2/orders", body)
        out["orders"].append({"sym": e["sym"], "side": "sell", "id": resp.get("id"), "status": resp.get("status")})
    out["stops"] = of.ensure_stops(call, st, now, True, exclude=leaving, tag=TAG)
    for body in chosen:
        if not entries_allowed:
            out["skipped"].append({"sym": body["symbol"], "why": "ENTRY_BLOCKED_BY_HALT" if halted else "ENTRY_BLOCKED_BY_GATE"})
            continue
        limit, why = of.morning_limit(body, qs.get(body["symbol"]), "buy")
        if limit is None:
            out["skipped"].append({"sym": body["symbol"], "why": "ENTRY_" + why})
            continue
        resp = call("POST", "/v2/orders", limit)
        out["orders"].append({"sym": body["symbol"], "side": "buy", "id": resp.get("id"), "status": resp.get("status")})
    return out


def report(call):
    trades, entries = of.closed_trades(call, TAG)
    n = len(trades)
    return {"closed_trades": n, "mean_ret_pct": round(sum(t["ret_pct"] for t in trades) / n, 3) if n else None, "total_pnl": round(sum(t["pnl"] for t in trades), 2),
            "stops": sum(1 for t in trades if t["how"] == "STOP"), "open_positions": len({e["symbol"] for e in entries} & {p["symbol"] for p in call("GET", "/v2/positions")})}


def main(argv=None, call=None, now=None, fetch=None):
    p = argparse.ArgumentParser(description="S-048 B55M forward observation on the Alpaca paper account.")
    p.add_argument("--state-dir", default=os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    pr = sub.add_parser("protect", help="stop guard: re-place any missing broker-side stop (dry run unless --submit)")
    pr.add_argument("--submit", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--universe", required=True)
    r.add_argument("--submit", action="store_true")
    r.add_argument("--exits-only", action="store_true")
    a = p.parse_args(argv)
    try:
        call = call or ap.make_client()
        now = now or datetime.now(timezone.utc)
        if a.cmd == "protect":
            res = of.ensure_stops(call, of.broker_state(call, TAG), now, a.submit, tag=TAG)
        else:
            res = report(call) if a.cmd == "report" else run(call, of.load_universe(Path(a.universe)), now, Path(a.state_dir), a.submit, fetch, "morning", entries_allowed=not a.exits_only)
    except ap.GuardError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(res, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
