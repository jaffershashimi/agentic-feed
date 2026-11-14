"""S-042 engine: oversold pullbacks (PB1 and deviation z-score) on a point-in-time liquid US stock panel. TEST ONLY.

Implements the frozen spec (Agentic-Lab system/06-research/specs/S-042-oversold-pullbacks.md) exactly: universe by year, eligibility,
indicators, four declared variants, one exit rule, costs, random-entry control, entry-week bootstrap, survivorship stress and the
secondary portfolio view. Standard library only. Reads a panel directory made by the data pull (daily/part-*.csv.gz,
universe_by_year.json). Writes nothing but the result files you ask for.

    python3.12 -m consumer.oversold_panel PANEL_DIR OUT_DIR
"""
from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import json
import math
import random
import sys
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

VARIANTS = ("V1", "V2", "V3", "V4")
WINDOWS = {"Development": ("2017-01-03", "2021-12-31"), "Judge": ("2022-01-03", "2024-12-31"), "Final": ("2025-01-02", "2026-12-31")}
COSTS = (0.0005, 0.0010, 0.0020)
STOP, MAX_DAYS, MIN_BARS, MIN_ADV, MIN_PRICE = 0.90, 5, 275, 10_000_000.0, 5.0
MAX_POS, POS_FRAC = 20, 0.05
SEED = 7


def load_panel(panel: Path):
    h = hashlib.sha256()
    data = {}
    for f in sorted((panel / "daily").glob("part-*.csv.gz")):
        h.update(f.read_bytes())
        with gzip.open(f, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                d = data.setdefault(r["symbol"], {"date": [], "o": [], "h": [], "l": [], "c": [], "v": []})
                d["date"].append(r["date"]); d["o"].append(float(r["o"])); d["h"].append(float(r["h"]))
                d["l"].append(float(r["l"])); d["c"].append(float(r["c"])); d["v"].append(float(r["v"]))
    universe = {int(y): set(s) for y, s in json.loads((panel / "universe_by_year.json").read_text()).items()}
    return data, universe, h.hexdigest()


def sma(xs, n):
    out, s = [None] * len(xs), 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi2(closes):
    """Wilder RSI, period 2: first averages are the simple means of the first two changes, then avg = (prev + new) / 2."""
    out = [None] * len(closes)
    if len(closes) < 3:
        return out
    g = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    l = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = (g[0] + g[1]) / 2, (l[0] + l[1]) / 2
    for i in range(2, len(closes)):
        if i > 2:
            ag, al = (ag + g[i - 1]) / 2, (al + l[i - 1]) / 2
        out[i] = 50.0 if ag == 0 and al == 0 else (100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al))
    return out


def zdev_series(closes, sma25):
    """z = dev / sample std of dev over the previous 250 days (t-250 .. t-1)."""
    n = len(closes)
    dev = [None if sma25[i] is None else closes[i] / sma25[i] - 1.0 for i in range(n)]
    z = [None] * n
    win, s, ss = deque(), 0.0, 0.0
    for i in range(n):
        if dev[i] is not None and len(win) == 250:
            m = s / 250
            var = (ss - 250 * m * m) / 249
            sd = math.sqrt(var) if var > 0 else 0.0
            z[i] = dev[i] / sd if sd > 0 else None
        if dev[i] is not None:
            win.append(dev[i]); s += dev[i]; ss += dev[i] * dev[i]
            if len(win) > 250:
                x = win.popleft(); s -= x; ss -= x * x
    return z


def stress_flags(spy_close):
    """SPY 20-day realized vol above the 75th percentile of its own previous 252 values."""
    n = len(spy_close)
    lr = [None] + [math.log(spy_close[i] / spy_close[i - 1]) for i in range(1, n)]
    vol = [None] * n
    for i in range(20, n):
        w = lr[i - 19:i + 1]
        m = sum(w) / 20
        vol[i] = math.sqrt(sum((x - m) ** 2 for x in w) / 19) * math.sqrt(252)
    flags, hist, ordered = [False] * n, deque(), []
    for i in range(n):
        if vol[i] is None:
            continue
        if len(hist) == 252:
            k = int(0.75 * 251)  # index of the 75th percentile in the sorted previous 252 values
            flags[i] = vol[i] > ordered[k]
        hist.append(vol[i]); bisect.insort(ordered, vol[i])
        if len(hist) > 252:
            old = hist.popleft(); del ordered[bisect.bisect_left(ordered, old)]
    return flags


def prepare(sym_data):
    c = sym_data["c"]
    s5, s25, s200 = sma(c, 5), sma(c, 25), sma(c, 200)
    dv = [ci * vi for ci, vi in zip(c, sym_data["v"])]
    return {"sma5": s5, "sma200": s200, "rsi2": rsi2(c), "z": zdev_series(c, s25), "adv20": sma(dv, 20)}


def eligible(i, d, ind, universe, sym):
    return (i >= MIN_BARS and sym in universe.get(int(d["date"][i][:4]), ()) and (ind["adv20"][i] or 0) >= MIN_ADV and d["c"][i] >= MIN_PRICE
            and ind["sma200"][i] is not None and ind["rsi2"][i] is not None and ind["z"][i] is not None)


def signal(variant, i, ind, d, stress_on_date):
    r, z, above = ind["rsi2"][i], ind["z"][i], d["c"][i] > ind["sma200"][i]
    if variant == "V1":
        return r <= 10 and above
    if variant == "V2":
        return z <= -2.0
    if variant == "V3":
        return z <= -2.0 and stress_on_date
    return r <= 10 and z <= -2.0 and above


def trade(d, ind, i, cost):
    """Signal at the close of bar i; entry next open; exits per spec. None if the path runs past the data."""
    j = i + 1
    n = len(d["c"])
    if j >= n:
        return None
    entry = d["o"][j]
    stop = entry * STOP
    for k in range(j, min(j + MAX_DAYS, n)):
        if d["l"][k] <= stop:
            px = d["o"][k] if (k > j and d["o"][k] <= stop) else stop
            return j, k, entry, px
        if ind["sma5"][k] is not None and d["c"][k] > ind["sma5"][k]:
            return j, k, entry, d["c"][k]
        if k == j + MAX_DAYS - 1:
            return j, k, entry, d["c"][k]
    return None


def net_return(entry, exit_px, cost):
    return exit_px * (1 - cost) / (entry * (1 + cost)) - 1.0


def build_trades(data, universe, inds, stress_by_date):
    trades = {v: [] for v in VARIANTS}
    for sym, d in data.items():
        if sym == "SPY":
            continue
        ind = inds[sym]
        free = {v: 0 for v in VARIANTS}
        for i in range(MIN_BARS, len(d["c"]) - 1):
            if not eligible(i, d, ind, universe, sym):
                continue
            st = stress_by_date.get(d["date"][i], False)
            for v in VARIANTS:
                if i < free[v] or not signal(v, i, ind, d, st):
                    continue
                t = trade(d, ind, i, 0.0)
                if t is None:
                    continue
                j, k, entry, px = t
                free[v] = k
                trades[v].append({"sym": sym, "sig": d["date"][i], "entry_date": d["date"][j], "exit_date": d["date"][k], "entry": entry, "exit": px,
                                  "hold": k - j + 1, "i": i, "rsi": ind["rsi2"][i], "z": ind["z"][i]})
    return trades


def control_pool(data, universe, inds, need):
    """Trade price pairs (entry, exit) for every eligible day of the (symbol, year) pairs in `need`, no signal."""
    pool = defaultdict(list)
    for (sym, yr) in need:
        d, ind = data[sym], inds[sym]
        for i in range(MIN_BARS, len(d["c"]) - 1):
            if int(d["date"][i][:4]) != yr or not eligible(i, d, ind, universe, sym):
                continue
            t = trade(d, ind, i, 0.0)
            if t:
                pool[(sym, yr)].append((t[2], t[3]))
    return pool


def pf(rets):
    g = sum(r for r in rets if r > 0)
    lo = -sum(r for r in rets if r < 0)
    return g / lo if lo > 0 else float("inf")


def week_key(ds):
    y, m, dd = map(int, ds.split("-"))
    iso = date(y, m, dd).isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def bootstrap(values_by_week, resamples=5000, seed=SEED):
    """values_by_week: {week: (sum, n)}; returns (mean, lo, hi, share of resamples <= 0)."""
    weeks = sorted(values_by_week)
    if not weeks:
        return None
    sums, ns = [values_by_week[w][0] for w in weeks], [values_by_week[w][1] for w in weeks]
    tot, cnt = sum(sums), sum(ns)
    rnd, m = random.Random(seed), len(weeks)
    means = []
    for _ in range(resamples):
        s = c = 0
        for __ in range(m):
            k = rnd.randrange(m)
            s += sums[k]; c += ns[k]
        means.append(s / c if c else 0.0)
    means.sort()
    return {"mean": tot / cnt, "lo": means[int(0.025 * resamples)], "hi": means[int(0.975 * resamples) - 1], "p_le_zero": sum(1 for x in means if x <= 0) / resamples}


def by_week(trade_rows, vals):
    out = defaultdict(lambda: [0.0, 0])
    for t, x in zip(trade_rows, vals):
        w = out[week_key(t["entry_date"])]
        w[0] += x; w[1] += 1
    return {k: tuple(v) for k, v in out.items()}


def in_window(t, w):
    lo, hi = WINDOWS[w]
    return lo <= t["entry_date"] <= hi


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        adj[i] = run
    return adj


def stats_for(rows, cost):
    rets = [net_return(t["entry"], t["exit"], cost) for t in rows]
    if not rets:
        return {"n": 0}
    srt = sorted(rets)
    return {"n": len(rets), "mean": sum(rets) / len(rets), "median": srt[len(srt) // 2], "win": sum(1 for r in rets if r > 0) / len(rets),
            "pf": pf(rets), "worst": srt[0], "hold": sum(t["hold"] for t in rows) / len(rows)}


def stress_mean(rets, draws=200, seed=SEED):
    rnd, bad = random.Random(seed), net_return(1.0, 0.65, COSTS[0])
    if not rets:
        return None
    k = max(1, round(0.01 * len(rets)))
    tot = 0.0
    for _ in range(draws):
        idx = set(rnd.sample(range(len(rets)), k))
        tot += sum(bad if i in idx else r for i, r in enumerate(rets)) / len(rets)
    return tot / draws


def portfolio(trades_v, data, dates, spy_ret_by_date):
    """Equal weight, at most 20 positions of 5% of start-of-day equity, ranked by RSI2 then z, daily mark at the close, base cost."""
    cost = COSTS[0]
    by_entry = defaultdict(list)
    for t in trades_v:
        by_entry[t["entry_date"]].append(t)
    pos, equity, rets = [], 1.0, {}
    idx_of = {s: {dt: i for i, dt in enumerate(data[s]["date"])} for s in {t["sym"] for t in trades_v}}
    for dt in dates:
        start_equity = equity
        pnl = 0.0
        # exits and marks for existing positions, then new entries at the open (entries mark at the close the same day)
        keep = []
        for p in pos:
            d = data[p["sym"]]
            k = idx_of[p["sym"]].get(dt)
            if k is None:
                keep.append(p); continue
            if dt == p["exit_date"]:
                value = p["shares"] * p["exit"] * (1 - cost)
                pnl += value - p["last_value"]
            else:
                value = p["shares"] * d["c"][k]
                pnl += value - p["last_value"]
                p["last_value"] = value
                keep.append(p)
        pos = keep
        slots = MAX_POS - len(pos)
        cands = sorted(by_entry.get(dt, []), key=lambda t: (t["rsi"], t["z"], t["sym"]))[:max(slots, 0)]
        for t in cands:
            k = idx_of[t["sym"]][dt]
            d = data[t["sym"]]
            alloc = POS_FRAC * start_equity
            shares = alloc / (t["entry"] * (1 + cost))
            if dt == t["exit_date"]:
                value = shares * t["exit"] * (1 - cost)
                pnl += value - alloc
            else:
                value = shares * d["c"][k]
                pnl += value - alloc
                pos.append({"sym": t["sym"], "shares": shares, "exit": t["exit"], "exit_date": t["exit_date"], "last_value": value})
        equity = start_equity + pnl
        rets[dt] = equity / start_equity - 1.0
    return rets


def perf(ret_by_date, lo, hi):
    ds = [d for d in sorted(ret_by_date) if lo <= d <= hi]
    r = [ret_by_date[d] for d in ds]
    if len(r) < 20:
        return None
    m = sum(r) / len(r)
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))
    curve, peak, mdd = 1.0, 1.0, 0.0
    for x in r:
        curve *= 1 + x
        peak = max(peak, curve)
        mdd = max(mdd, 1 - curve / peak)
    return {"days": len(r), "cagr": curve ** (252 / len(r)) - 1, "sharpe": (m / sd * math.sqrt(252)) if sd else 0.0, "mdd": mdd, "total": curve - 1}


def verdict(res):
    """Pre-registered pass bar P1 to P7 for one variant's result dict. Returns (verdict, {P: bool})."""
    w = res["windows"]
    def g(win, *keys):
        x = w[win]
        for k in keys:
            x = x.get(k) if isinstance(x, dict) else None
            if x is None:
                return None
        return x
    both = ("Judge", "Final")
    p = {}
    p["P1"] = all((g(x, "cost", str(COSTS[0]), "n") or 0) >= 100 for x in both)
    p["P2"] = all((g(x, "cost", str(COSTS[0]), "pf") or 0) >= 1.2 for x in both)
    p["P3"] = (g("Final", "mean_interval", "lo") or -1) > 0
    p["P4"] = (res.get("holm_p_final_vs_control") if res.get("holm_p_final_vs_control") is not None else 1.0) < 0.05
    p["P5"] = all((g(x, "cost", str(COSTS[1]), "mean") or -1) > 0 for x in both)
    p["P6"] = all(g(x, "portfolio") and g(x, "spy") and g(x, "portfolio", "sharpe") > g(x, "spy", "sharpe") and g(x, "portfolio", "mdd") <= 0.25 for x in both)
    p["P7"] = (g("Final", "stress_mean") or -1) > 0
    if all(p.values()):
        return "PASS", p
    if p["P1"] and p["P2"] and p["P3"] and p["P5"]:
        return "INCONCLUSIVE", p
    return "REJECTED", p


def render(results):
    pct = lambda x: "n/a" if x is None else f"{100 * x:.2f}%"
    lines = [f"S-042 results. Data sha256 {results['data_sha256']}; {results['symbols']} symbols.", ""]
    for v in VARIANTS:
        r = results["variants"][v]
        vd, flags = verdict(r)
        lines += [f"### {v}: {vd} ({', '.join(k for k, ok in flags.items() if not ok) or 'all passed'}{' failed' if vd != 'PASS' else ''})", "",
                  "| Window | Trades | Mean (base cost) | 95% interval | Median | Win | PF | Worst | Mean at 0.10% | Mean at 0.20% | Control mean | Mean minus control (95%) | Stress S mean | Portfolio Sharpe / MDD | SPY Sharpe / MDD |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for wn in WINDOWS:
            x = r["windows"][wn]
            c0, c1, c2 = x["cost"][str(COSTS[0])], x["cost"][str(COSTS[1])], x["cost"][str(COSTS[2])]
            if not c0.get("n"):
                lines.append(f"| {wn} | 0 |" + " |" * 13); continue
            mi, vc = x.get("mean_interval") or {}, x.get("vs_control") or {}
            pf_, sp = x.get("portfolio"), x.get("spy")
            lines.append(f"| {wn} | {c0['n']} | {pct(c0['mean'])} | {pct(mi.get('lo'))} to {pct(mi.get('hi'))} | {pct(c0['median'])} | {c0['win']:.0%} | {c0['pf']:.2f} | {pct(c0['worst'])} | {pct(c1['mean'])} | {pct(c2['mean'])} | {pct(x.get('control_mean'))} | {pct(vc.get('mean'))} ({pct(vc.get('lo'))} to {pct(vc.get('hi'))}) | {pct(x.get('stress_mean'))} | "
                         + (f"{pf_['sharpe']:.2f} / {pct(pf_['mdd'])}" if pf_ else "n/a") + " | " + (f"{sp['sharpe']:.2f} / {pct(sp['mdd'])}" if sp else "n/a") + " |")
        lines += ["", f"Holm-adjusted p, Final mean minus control: {r.get('holm_p_final_vs_control'):.3f}", ""]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    panel, out = Path(argv[0]), Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    data, universe, digest = load_panel(panel)
    spy = data["SPY"]
    fl = stress_flags(spy["c"])
    stress_by_date = dict(zip(spy["date"], fl))
    inds = {s: prepare(d) for s, d in data.items() if s != "SPY"}
    trades = build_trades(data, universe, inds, stress_by_date)
    need = {v: {(t["sym"], int(t["sig"][:4])) for t in trades[v]} for v in VARIANTS}
    pool = control_pool(data, universe, inds, set().union(*need.values()))
    rnd = random.Random(SEED)
    results = {"data_sha256": digest, "universe_sizes": {str(y): len(s) for y, s in universe.items()}, "symbols": len(data), "variants": {}}
    spy_ret = {spy["date"][i]: spy["c"][i] / spy["c"][i - 1] - 1.0 for i in range(1, len(spy["c"]))}
    dates = [d for d in spy["date"] if d >= WINDOWS["Development"][0]]
    final_ps = {}
    for v in VARIANTS:
        ts = trades[v]
        ctrl = []
        for t in ts:
            ps = pool.get((t["sym"], int(t["sig"][:4])))
            ctrl.append(sum(net_return(*rnd.choice(ps), COSTS[0]) for _ in range(200)) / 200 if ps else None)
        res = {"trades_total": len(ts), "windows": {}}
        port = portfolio(ts, data, dates, spy_ret)
        for w in WINDOWS:
            rows = [t for t in ts if in_window(t, w)]
            cr = [c for t, c in zip(ts, ctrl) if in_window(t, w)]
            wres = {"cost": {str(c): stats_for(rows, c) for c in COSTS}}
            rets = [net_return(t["entry"], t["exit"], COSTS[0]) for t in rows]
            if rets:
                wres["mean_interval"] = bootstrap(by_week(rows, rets))
                pairs = [(t, r, c) for t, r, c in zip(rows, rets, cr) if c is not None]
                diff_rows = [p[0] for p in pairs]
                bd = bootstrap(by_week(diff_rows, [p[1] - p[2] for p in pairs])) if pairs else None
                wres["vs_control"] = bd
                wres["control_mean"] = sum(p[2] for p in pairs) / len(pairs) if pairs else None
                wres["stress_mean"] = stress_mean(rets)
                cnt = defaultdict(int)
                for t in rows:
                    cnt[t["sig"]] += 1
                wres["max_signals_one_day"] = max(cnt.values())
            lo, hi = WINDOWS[w]
            wres["portfolio"] = perf(port, lo, hi)
            wres["spy"] = perf(spy_ret, lo, hi)
            res["windows"][w] = wres
        results["variants"][v] = res
        final_ps[v] = (res["windows"]["Final"].get("vs_control") or {}).get("p_le_zero", 1.0)
    adj = dict(zip(VARIANTS, holm([final_ps[v] for v in VARIANTS])))
    for v in VARIANTS:
        results["variants"][v]["holm_p_final_vs_control"] = adj[v]
    (out / "s042_results.json").write_text(json.dumps(results, indent=1, default=str))
    (out / "s042_results.md").write_text(render(results))
    print(json.dumps({"data_sha256": digest, "trades": {v: len(trades[v]) for v in VARIANTS}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
