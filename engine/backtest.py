"""Day-by-day backtest of Strategy + Portfolio + SimBroker. The loop mirrors the live schedule: decide after the close, fill at the next open, stops live at the broker."""
import json
from collections import defaultdict

from consumer import oversold_panel as op
from engine.portfolio import Portfolio
from engine.simbroker import SimBroker
from engine.strategy import Context


def prepare_market(data, universe_by_year):
    spy = data["SPY"]
    stress = dict(zip(spy["date"], op.stress_flags(spy["c"])))
    inds = {s: op.prepare(d) for s, d in data.items() if s != "SPY"}
    idx = {s: {dt: i for i, dt in enumerate(d["date"])} for s, d in data.items()}
    return stress, inds, idx


def run(strategy, portfolio, data, universe_by_year, start="2017-01-03", end=None, slip=0.0005, entry_gap=0.0, prepared=None, slip_out=None, exit_gap=0.0):
    stress, inds, idx = prepared or prepare_market(data, universe_by_year)
    broker = SimBroker(slip, entry_gap, slip_out, exit_gap)
    dates = [d for d in data["SPY"]["date"] if d >= start and (end is None or d <= end)]
    pending = []  # (intent, qty) to buy at the next open
    daily = {}
    for dt in dates:
        # 1. the open: queued sells then queued buys fill
        for pos in list(broker.positions):
            if pos.pending_exit:
                i = idx[pos.symbol].get(dt)
                if i is not None:
                    broker.sell_at_open(pos, dt, data[pos.symbol]["o"][i], pos.pending_exit)
        for intent, qty in pending:
            i = idx[intent.symbol].get(dt)
            if i is not None:
                broker.buy(intent, qty, dt, data[intent.symbol]["o"][i])
        pending = []

        # 2. the session: stops
        def bar_of(sym, dt=dt):
            i = idx[sym].get(dt)
            d = data[sym]
            return None if i is None else (d["o"][i], d["h"][i], d["l"][i], d["c"][i])
        n_before = len(broker.trades)
        broker.check_stops(dt, bar_of)

        # 3. the close: decide exits and entries for the next open
        ctx = Context(data, idx, inds, universe_by_year, dt, stress.get(dt, False))
        for pos in broker.positions:
            if pos.entry_date < dt:
                pos.sessions_held += 1
            reason = strategy.exit_reason(pos, ctx)
            if reason:
                pos.pending_exit = reason
        leaving = {p.symbol for p in broker.positions if p.pending_exit}
        orders, _ = portfolio.select(strategy.signals(ctx), broker.positions, leaving)
        pending = orders
        daily[dt] = sum(t.pnl for t in broker.trades[n_before:])
    return {"trades": broker.trades, "daily_pnl": daily, "open_positions": len(broker.positions)}


def summarize(res, label=""):
    tr = res["trades"]
    by_year, by_month = defaultdict(float), defaultdict(float)
    for t in tr:
        by_year[t.exit_date[:4]] += t.pnl
        by_month[t.exit_date[:7]] += t.pnl
    cum = peak = dd = 0.0
    for m in sorted(by_month):
        cum += by_month[m]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    n = len(tr)
    win = {}
    for w, (lo, hi) in op.WINDOWS.items():
        sel = [t for t in tr if lo <= t.entry_date <= hi]
        win[w] = {"n": len(sel), "mean_ret_pct": round(100 * sum(t.ret for t in sel) / len(sel), 3) if sel else None, "total_pnl": round(sum(t.pnl for t in sel))}
    return {"windows": win, "label": label, "trades": n, "total_pnl": round(sum(t.pnl for t in tr)), "mean_pnl_per_trade": round(sum(t.pnl for t in tr) / n, 2) if n else None,
            "mean_ret_pct": round(100 * sum(t.ret for t in tr) / n, 3) if n else None, "win_rate": round(sum(1 for t in tr if t.pnl > 0) / n, 3) if n else None,
            "stop_share": round(sum(1 for t in tr if t.how == "STOP") / n, 3) if n else None, "by_year": {y: round(v) for y, v in sorted(by_year.items())},
            "worst_month": round(min(by_month.values())) if by_month else None, "months_positive": round(sum(1 for v in by_month.values() if v > 0) / len(by_month), 3) if by_month else None,
            "max_drawdown_monthly": round(dd)}
