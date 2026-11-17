"""Command line for the engine.

  python3 -m engine.runner backtest PANEL_DIR OUT_DIR [--entry-gap-bps N] [--slip-bps N] [--dump-trades]
  python3 -m engine.runner health [--json]        # read only; exit 1 on any FAIL
  python3 -m engine.runner gate PLAN.json [--out GATE.json]   # pre-trade gate on the dry-run plan; prints the decision; exit 0 whatever it decides
  python3 -m engine.runner pack [--plan PLAN.json] [--gate GATE.json] [--sent RESULT.json] [--summary-file FILE]   # the desk pack, read only
  python3 -m engine.runner limits                 # the limits register
"""
import argparse
import json
import sys
from pathlib import Path

from consumer import oversold_panel as op
from engine import backtest
from engine.portfolio import Portfolio
from engine.strategies.oversold_v3 import OversoldV3


def cmd_backtest(a):
    data, universe, digest = op.load_panel(Path(a.panel))
    prepared = backtest.prepare_market(data, universe)
    out = {"data_sha256": digest, "scenarios": {}}
    # Execution scenarios, in bps per side. Evidence for the numbers (2026-11-17): quoted spread medians of the first 10 live S-042 names were 64 bps at 09:30 to 09:35 ET
    # (half-spread 32) and 19 bps at 11:00 (half-spread 9); in 350 sampled past trades the price at 10:00 ET was +0.5 bps against a buyer and +5.8 bps in favour of a seller
    # relative to the 09:30 open. The opening-auction case is a hypothesis: an opg order trades at the single opening cross, so it should pay almost no spread.
    scenarios = (("as_assumed_by_old_backtests_5bps", dict(slip=5.0, slip_out=5.0, entry_gap=0.0, exit_gap=0.0)),
                 ("market_orders_at_the_open_32bps", dict(slip=32.0, slip_out=32.0, entry_gap=0.0, exit_gap=0.0)),
                 ("mid_morning_execution_10am_9bps", dict(slip=9.0, slip_out=9.0, entry_gap=0.5, exit_gap=-5.8)),
                 ("opening_auction_orders_hypothesis_5bps", dict(slip=5.0, slip_out=5.0, entry_gap=0.0, exit_gap=0.0)))
    for label, k in scenarios:
        res = backtest.run(OversoldV3(), Portfolio(max_positions=10), data, universe, entry_gap=k["entry_gap"] / 1e4, slip=k["slip"] / 1e4, slip_out=k["slip_out"] / 1e4, exit_gap=k["exit_gap"] / 1e4, prepared=prepared)
        out["scenarios"][label] = backtest.summarize(res, label)
        print(label, json.dumps({kk: out["scenarios"][label][kk] for kk in ("trades", "total_pnl", "mean_ret_pct", "win_rate", "stop_share", "max_drawdown_monthly", "windows")}), flush=True)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "engine_backtest.json").write_text(json.dumps(out, indent=1))
    if a.dump_trades:
        res = backtest.run(OversoldV3(), Portfolio(max_positions=10), data, universe, entry_gap=0.0, slip=a.slip_bps / 1e4, prepared=prepared)
        (Path(a.out) / "trades.json").write_text(json.dumps([t.__dict__ for t in res["trades"]]))
    return 0


def cmd_health(a):
    from consumer import alpaca_paper as ap
    from engine import health
    rows = health.check_all(ap.make_client())
    print(json.dumps(rows, indent=1) if a.json else health.render(rows, safe=a.safe))
    return 1 if any(r["status"] == health.FAIL for r in rows) else 0


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def cmd_gate(a):
    import os
    from consumer import alpaca_paper as ap
    from engine import gate, health
    plan = _load(a.plan)
    try:
        rows = health.check_all(ap.make_client())
    except Exception:
        rows = None   # fail closed: the gate treats missing health as a block on new entries
    result = gate.decide(plan, rows, halted=gate.halted(os.environ.get("ALPACA_PAPER_STATE", ".alpaca_paper")), tag=a.tag)
    print(gate.render(result))
    if a.out:
        Path(a.out).write_text(json.dumps(result))
    return 0


def cmd_pack(a):
    from consumer import alpaca_paper as ap
    from engine import deskpack
    pack = deskpack.build(ap.make_client(), plan=_load(a.plan) if a.plan else None, gate=_load(a.gate) if a.gate else None, sent=_load(a.sent) if a.sent else None)
    text = deskpack.render(pack)
    print(text)
    if a.summary_file:
        with open(a.summary_file, "a") as f:
            f.write(text + "\n")
    return 0


def cmd_limits(a):
    from engine import limits
    for r in limits.as_rows():
        print(f"{'OWNER' if r['owner_set'] else 'PROPOSED':<9} {r['id']:<32} {r['value']!s:<10} {r['unit']}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="engine.runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("panel")
    b.add_argument("out")
    b.add_argument("--entry-gap-bps", type=float, default=0.0)
    b.add_argument("--slip-bps", type=float, default=5.0)
    b.add_argument("--dump-trades", action="store_true", help="also write the as-implemented trade list (private; derived from Alpaca data)")
    h = sub.add_parser("health")
    h.add_argument("--json", action="store_true")
    h.add_argument("--safe", action="store_true", help="withhold stock names from the output (for logs kept on GitHub)")
    g = sub.add_parser("gate")
    g.add_argument("plan")
    g.add_argument("--out")
    g.add_argument("--tag", default="s042v3", help="which strategy's plan this is: s042v3 (default) or s048b55m")
    k = sub.add_parser("pack")
    k.add_argument("--plan")
    k.add_argument("--gate")
    k.add_argument("--sent")
    k.add_argument("--summary-file")
    sub.add_parser("limits")
    a = p.parse_args(argv)
    return {"backtest": cmd_backtest, "health": cmd_health, "gate": cmd_gate, "pack": cmd_pack, "limits": cmd_limits}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
