"""S-029 sector ETF cross-sectional momentum lab (TEST ONLY, REPLAY-DAILY). Standard library only, no orders.

Implements the frozen spec Agentic-Lab system/06-research/specs/S-029-etf-sector-momentum.md exactly: month-end decisions, rank the nine sector SPDRs by the 252-day return that skips
the last 21 days (12-1), hold the top 3 equal weight, trade at the next adjusted open, 0.05 percent per side; variants V1 (absolute filter), V2 (top 4), V3 (6-1); control C0 (all
sectors equal weight); benchmark SPY. Reuses the S-037 lab's loader, statistics and circular block bootstrap. Never writes the paper ledger; never pooled with any other class.

    python3 -m consumer.etf_momentum_lab fetch OUT.csv          # Yahoo chart API, dividend-adjusted, sectors plus the S-037 ten and SPY
    python3 -m consumer.etf_momentum_lab run IN.csv OUT.json [--commit SHA]
"""
import csv
import hashlib
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from consumer import etf_trend_lab as tl

SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
U1 = tl.UNIVERSE
ALL = tuple(dict.fromkeys(SECTORS + U1))
COST = 0.0005
SKIP = 21
VARIANTS = {"V0": dict(lookback=252, top=3, filt=False), "V1": dict(lookback=252, top=3, filt=True), "V2": dict(lookback=252, top=4, filt=False), "V3": dict(lookback=126, top=3, filt=False)}
WIN_A = (date(2000, 1, 3), date(2009, 12, 31))
WIN_B_START = date(2010, 1, 4)


def fetch(out_path):
    rows = []
    for sym in ALL:
        # period1 and period2 give true daily bars; range=max silently returns monthly bars for long histories
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2={int(datetime.now(timezone.utc).timestamp())}&interval=1d&events=div%2Csplit"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        j = json.loads(urllib.request.urlopen(req, timeout=60).read())
        r = j["chart"]["result"][0]
        ts, q, adj = r["timestamp"], r["indicators"]["quote"][0], r["indicators"]["adjclose"][0]["adjclose"]
        for k, t in enumerate(ts):
            if None in (q["open"][k], q["close"][k], adj[k]) or not q["close"][k]:
                continue
            f = adj[k] / q["close"][k]                     # scale open, high, low, close by adjusted close over close (total return)
            d = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
            rows.append((sym, d, q["open"][k] * f, q["high"][k] * f, q["low"][k] * f, adj[k], q["volume"][k] or 0))
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "begins_at", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    return len(rows)


def momentum_targets(d, i, universe, lookback, top, filt):
    """Weights decided at the close of day i from data up to i only."""
    el = [s for s in universe if s in d["universe"] and tl.eligible(d, s, i)]
    scored = []
    for s in el:
        c = d["close"][s]
        a, b = c[i - lookback], c[i - SKIP]
        if a and b:
            scored.append((b / a - 1, s))
    if not scored:
        return {}
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = [s for _, s in scored[:top]]
    w = 1.0 / top
    out = {}
    for s in chosen:
        if filt:
            c = d["close"][s]
            if not (c[i - lookback] and c[i] / c[i - lookback] - 1 > 0):
                continue
        out[s] = w
    return out


def equal_targets(d, i, universe):
    el = [s for s in universe if s in d["universe"] and tl.eligible(d, s, i)]
    return {s: 1.0 / len(el) for s in el} if el else {}


def simulate(d, start, end, target_fn, cost=COST):
    """Daily returns and per-rebalance turnover. Month-end decision, next-day adjusted open, unheld weight is cash."""
    days = d["days"]
    i0 = next(i for i, x in enumerate(days) if x >= start)
    i1 = max(i for i, x in enumerate(days) if x <= end)
    rebal = {i0} | {i for i in tl.month_end_indices(days) if i0 < i < i1}
    shares, cash, prev, rets, turns, pending = {}, 1.0, 1.0, [], [], None
    for i in range(i0, i1 + 1):
        if pending is not None:
            eq_open = cash + sum(sh * d["open"][s][i] for s, sh in shares.items())
            tgt = {s: w * eq_open for s, w in pending.items()}
            cur = {s: sh * d["open"][s][i] for s, sh in shares.items()}
            traded = sum(abs(tgt.get(s, 0.0) - cur.get(s, 0.0)) for s in set(tgt) | set(cur))
            eq_after = eq_open - cost * traded
            shares = {s: w * eq_after / d["open"][s][i] for s, w in pending.items()}
            cash = eq_after - sum(w * eq_after for w in pending.values())
            turns.append(traded / eq_open)
            pending = None
        eq = cash + sum(sh * d["close"][s][i] for s, sh in shares.items())
        rets.append(eq / prev - 1 if (i > i0 or shares) else 0.0)
        prev = eq
        if i in rebal and i < i1:
            pending = target_fn(d, i)
    return rets, turns


def run_universe(d, universe, windows, label):
    out = {}
    for wname, (st, en) in windows.items():
        days = d["days"]
        i0 = next(i for i, x in enumerate(days) if x >= st)
        i1 = max(i for i, x in enumerate(days) if x <= en)
        yrs = (days[i1] - days[i0]).days / 365.25
        sl = days[i0:i1 + 1]
        series, rows = {}, {}
        first = {"done": False}

        def spy_fn(dd, i, _f=first):
            if _f["done"]:
                return None
            _f["done"] = True
            return {"SPY": 1.0}
        runs = {"SPY": (spy_fn, COST), "C0": (lambda dd, i: equal_targets(dd, i, universe), COST), "C0_c10": (lambda dd, i: equal_targets(dd, i, universe), 0.001),
                "C0_c20": (lambda dd, i: equal_targets(dd, i, universe), 0.002)}
        for vn, kw in VARIANTS.items():
            fn = (lambda dd, i, kw=kw: momentum_targets(dd, i, universe, kw["lookback"], kw["top"], kw["filt"]))
            runs[vn] = (fn, COST)
            if vn == "V0":
                runs["V0_c10"], runs["V0_c20"] = (fn, 0.001), (fn, 0.002)
        for name, (fn, cost) in runs.items():
            if name == "SPY":
                first["done"] = False
            r, t = simulate(d, st, en, fn, cost)
            series[name] = r
            rows[name] = tl.stats(r, t, yrs)
            rows[name]["worst_year"] = tl.worst_year(r, sl[-len(r):])
        rows["boot_V0_minus_C0"] = tl.boot_sharpe_diff(series["V0"], series["C0"])
        rows["boot_V0_minus_SPY"] = tl.boot_sharpe_diff(series["V0"], series["SPY"])
        out[wname] = rows
    return out


def verdict(res):
    """Frozen pass bar P1 to P5 on V0, primary universe. res has windows A, B and FULL."""
    a, b, f = res["A"], res["B"], res["FULL"]
    p = {"P1": a["V0"]["sharpe"] > a["SPY"]["sharpe"] and b["V0"]["sharpe"] > b["SPY"]["sharpe"],
         "P2": a["V0"]["max_drawdown"] <= a["SPY"]["max_drawdown"] and b["V0"]["max_drawdown"] <= b["SPY"]["max_drawdown"],
         "P3": a["V0_c10"]["sharpe"] > a["SPY"]["sharpe"] and b["V0_c10"]["sharpe"] > b["SPY"]["sharpe"],
         "P4": f["boot_V0_minus_C0"][0] > 0,
         "P5": b["V0"]["cagr"] >= b["SPY"]["cagr"] - 0.02}
    if all(p.values()):
        v = "PASS"
    elif p["P1"] and p["P2"] and p["P3"] and p["P5"]:
        v = "INCONCLUSIVE"
    else:
        v = "REJECTED"
    return v, p


def run(path, commit="unknown"):
    d = tl.load(path, ALL)
    last = d["days"][-1]
    windows = {"A": WIN_A, "B": (WIN_B_START, last), "FULL": (WIN_A[0], last)}
    out = {"spec": "S-029", "data_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "code_commit": commit, "last_day": last.isoformat(), "cost_per_side": COST,
           "primary_universe": list(SECTORS), "results": run_universe(d, SECTORS, windows, "U2"), "secondary_U1_reported_only": run_universe(d, U1, windows, "U1")}
    out["verdict"], out["pass_bar"] = verdict(out["results"])
    return out


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) >= 2 and a[0] == "fetch":
        print(json.dumps({"rows": fetch(a[1])}))
        return 0
    if len(a) >= 3 and a[0] == "run":
        commit = a[a.index("--commit") + 1] if "--commit" in a else "unknown"
        res = run(a[1], commit)
        Path(a[2]).write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(json.dumps({"verdict": res["verdict"], "pass_bar": res["pass_bar"]}, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
