"""S-042 robustness report: how much of the edge survives honest multiple-testing math.

Research only. Reads the private daily panel, never writes orders, never touches an account.

What it computes
  1. For the four declared variants V1..V4: annualised Sharpe, Probabilistic Sharpe (PSR), Deflated Sharpe (DSR) at the number of
     variants tried (4) and at the number of ideas the lab has registered (42), minimum track record, and a stationary-bootstrap
     interval for the Sharpe.
  2. A parameter plateau grid on V3: z threshold x stop x max hold days (36 configs). Reports how many configs are positive in each
     window, the Sharpe spread, DSR of the declared config at N=36 and the Probability of Backtest Overfitting (CSCV) across the grid.

Method references: Bailey and Lopez de Prado (2012, 2014) for PSR/DSR, Bailey, Borwein, Lopez de Prado and Zhu (2015) for PBO,
Politis and Romano (1994) for the stationary bootstrap. See consumer/robust_stats.py.
Run:  python3 -m consumer.oversold_robust PANEL_DIR OUT_DIR
"""
import itertools
import json
import sys
from pathlib import Path

from consumer import oversold_panel as op
from consumer import robust_stats as rs

Z_GRID = (-1.5, -2.0, -2.5, -3.0)
STOP_GRID = (0.85, 0.90, 0.95)
HOLD_GRID = (3, 5, 8)
BASE = (-2.0, 0.90, 5)
CANDIDATE_Z = max(Z_GRID)  # loosest threshold: a trade at a tighter threshold is a subset of these
REGISTERED_IDEAS = 42
PBO_BLOCKS = 16


def window_stats(trades, cost=op.COSTS[0]):
    out = {}
    for w, (lo, hi) in op.WINDOWS.items():
        r = [op.net_return(t["entry"], t["exit"], cost) for t in trades if lo <= t["entry_date"] <= hi]
        out[w] = {"n": len(r), "mean_pct": round(100 * sum(r) / len(r), 3) if r else None, "pf": round(op.pf(r), 2) if r else None}
    return out


def _series(port, dates):
    return [port[d] for d in dates if d in port]


def _inf_to_none(x):
    return None if x == float("inf") else round(x)


def declared_report(returns, jf_returns, stats):
    """A variant that never traded has no variance: report it as untestable instead of failing the whole run."""
    if len(set(returns)) < 2 or len(set(jf_returns)) < 2:
        return {"days": len(returns), "untestable": "no variance (variant produced no trades in the panel)", "windows": stats}
    mtr = rs.min_track_record(returns)
    return {
        "days": len(returns), "sharpe_ann": round(rs.sharpe(returns, 252), 3), "psr_vs_zero": round(rs.psr(returns, 0.0), 3),
        "dsr_n4": round(rs.dsr(returns, 4), 3), "dsr_n42": round(rs.dsr(returns, REGISTERED_IDEAS), 3), "min_track_record_days": _inf_to_none(mtr),
        "boot_sharpe_ann": {k: round(v, 3) for k, v in rs.stationary_bootstrap_sharpe(returns, 10, 1000, 7, 252).items()},
        "judge_final_sharpe_ann": round(rs.sharpe(jf_returns, 252), 3), "judge_final_dsr_n4": round(rs.dsr(jf_returns, 4), 3),
        "judge_final_dsr_n42": round(rs.dsr(jf_returns, REGISTERED_IDEAS), 3), "windows": stats}


def run(panel, out):
    data, universe, digest = op.load_panel(Path(panel))
    spy = data["SPY"]
    stress = dict(zip(spy["date"], op.stress_flags(spy["c"])))
    spy_ret = {spy["date"][i]: spy["c"][i] / spy["c"][i - 1] - 1.0 for i in range(1, len(spy["c"]))}
    inds = {s: op.prepare(d) for s, d in data.items() if s != "SPY"}
    dates = [d for d in spy["date"] if d >= op.WINDOWS["Development"][0]]
    judge_start = op.WINDOWS["Judge"][0]
    jf_dates = [d for d in dates if d >= judge_start]
    res = {"data_sha256": digest, "declared": {}, "grid": {}}

    base = op.build_trades(data, universe, inds, stress)
    declared = {}
    for v in op.VARIANTS:
        port = op.portfolio(base[v], data, dates, spy_ret)
        declared[v] = (_series(port, dates), _series(port, jf_dates), window_stats(base[v]))
        res["declared"][v] = declared_report(*declared[v])
    t = min(len(x[0]) for x in declared.values())
    res["declared_pbo"] = rs.pbo_cscv([x[0][:t] for x in declared.values()], PBO_BLOCKS)

    grid = list(itertools.product(Z_GRID, STOP_GRID, HOLD_GRID))
    cand = {}
    for sym, d in data.items():
        if sym == "SPY":
            continue
        ind = inds[sym]
        cand[sym] = [(i, ind["z"][i], ind["rsi2"][i]) for i in range(op.MIN_BARS, len(d["c"]) - 1)
                     if stress.get(d["date"][i], False) and op.eligible(i, d, ind, universe, sym) and ind["z"][i] <= CANDIDATE_Z]
    keep = (op.STOP, op.MAX_DAYS)
    mat, names = [], []
    try:
        for thr, stop, hold in grid:
            op.STOP, op.MAX_DAYS = stop, hold
            trs = []
            for sym, lst in cand.items():
                d, ind, free = data[sym], inds[sym], 0
                for i, z, rsi in lst:
                    if i < free or z > thr:
                        continue
                    tr = op.trade(d, ind, i, 0.0)
                    if tr is None:
                        continue
                    j, k, entry, px = tr
                    free = k
                    trs.append({"entry_date": d["date"][j], "exit_date": d["date"][k], "entry": entry, "exit": px, "sym": sym, "sig": d["date"][i], "z": z, "rsi": rsi})
            r = _series(op.portfolio(trs, data, dates, spy_ret), dates)
            key = f"z{thr}_stop{stop}_hold{hold}"
            res["grid"][key] = {"trades": len(trs), "windows": window_stats(trs), "sharpe_ann": round(rs.sharpe(r, 252), 3) if len(set(r)) > 1 else 0.0}
            mat.append(r)
            names.append(key)
    finally:
        op.STOP, op.MAX_DAYS = keep
    n = min(len(x) for x in mat)
    res["grid_pbo"] = rs.pbo_cscv([x[:n] for x in mat], PBO_BLOCKS, max_combinations=4000)
    base_key = f"z{BASE[0]}_stop{BASE[1]}_hold{BASE[2]}"
    res["grid_base_dsr_n36"] = round(rs.dsr(mat[names.index(base_key)], len(names)), 3)
    sh = sorted(res["grid"][k]["sharpe_ann"] for k in names)

    def pos(w):
        return sum(1 for k in names if (res["grid"][k]["windows"][w]["mean_pct"] or 0) > 0)

    res["grid_summary"] = {"configs": len(names), "positive_development": pos("Development"), "positive_judge": pos("Judge"), "positive_final": pos("Final"),
                           "sharpe_min": sh[0], "sharpe_median": sh[len(sh) // 2], "sharpe_max": sh[-1], "base_sharpe": res["grid"][base_key]["sharpe_ann"]}
    return res


def render(res):
    lines = ["# S-042 robustness (TEST ONLY)", "", f"Panel digest `{res['data_sha256'][:12]}`. Research only, no orders.", "",
             "| Variant | Sharpe | PSR>0 | DSR N=4 | DSR N=42 | Judge+Final Sharpe | Judge+Final DSR N=42 |", "|---|---|---|---|---|---|---|"]
    for v, x in res["declared"].items():
        if "untestable" in x:
            lines.append(f"| {v} | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        lines.append(f"| {v} | {x['sharpe_ann']} | {x['psr_vs_zero']} | {x['dsr_n4']} | {x['dsr_n42']} | {x['judge_final_sharpe_ann']} | {x['judge_final_dsr_n42']} |")
    g = res["grid_summary"]
    lines += ["", f"Declared-variant PBO (4 variants): {res['declared_pbo']['pbo']:.2f}. Grid PBO ({g['configs']} configs): {res['grid_pbo']['pbo']:.2f}. "
              f"Declared config DSR at N={g['configs']}: {res['grid_base_dsr_n36']}.", "",
              f"Plateau: positive configs by window Development {g['positive_development']}/{g['configs']}, Judge {g['positive_judge']}/{g['configs']}, "
              f"Final {g['positive_final']}/{g['configs']}. Sharpe range {g['sharpe_min']} to {g['sharpe_max']}, median {g['sharpe_median']}, declared {g['base_sharpe']}.", ""]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    res = run(argv[0], out)
    (out / "s042_robust.json").write_text(json.dumps(res, indent=1))
    (out / "s042_robust.md").write_text(render(res))
    print(json.dumps(res["grid_summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
