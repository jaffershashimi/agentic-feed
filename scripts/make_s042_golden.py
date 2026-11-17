"""Build the S-042 golden test vectors: synthetic daily bars plus the signal answers our engine gives. Synthetic data only, so it is safe to publish.

An independent implementation (for example one written by another AI from the written spec alone) can be checked against this file: same bars in, same answers out.

    python3 scripts/make_s042_golden.py tests/fixtures/s042_golden_vectors.json
"""
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from consumer import oversold_panel as op  # noqa: E402

N_BARS, EVAL_LAST = 330, 20
STOCKS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "LOWV", "PENNY"]


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def series(seed, start_px, daily_vol, vol_burst=None, drops=(), n=N_BARS):
    rnd, px, bars = random.Random(seed), start_px, []
    for k in range(n):
        vol = daily_vol * (3.0 if (vol_burst and k >= n - vol_burst) else 1.0)
        r = rnd.gauss(0.0003, vol)
        for kd, dr in drops:
            if k == n - kd:
                r = dr
        o = px
        c = px * math.exp(r)
        h, l = max(o, c) * (1 + abs(rnd.gauss(0, 0.004))), min(o, c) * (1 - abs(rnd.gauss(0, 0.004)))
        bars.append((o, h, l, c))
        px = c
    return bars


def build():
    dates = weekdays(date(2025, 1, 6), N_BARS)
    data = {}
    spec = {"SPY": (900, 400.0, 0.006, 11, ()), "AAA": (1, 60.0, 0.015, None, ((9, -0.07), (8, -0.06))), "BBB": (2, 85.0, 0.014, None, ((5, -0.09), (4, -0.05))),
            "CCC": (3, 45.0, 0.016, None, ((3, -0.12),)), "DDD": (4, 120.0, 0.012, None, ((16, -0.08), (15, -0.07))), "EEE": (5, 30.0, 0.02, None, ((12, -0.08), (11, -0.04))),
            "FFF": (6, 70.0, 0.013, None, ()), "LOWV": (7, 55.0, 0.015, None, ((6, -0.13),)), "PENNY": (8, 3.0, 0.03, None, ((6, -0.15),))}
    for sym, (seed, px0, vol, burst, drops) in spec.items():
        bars = series(seed, px0, vol, burst, drops)
        rnd = random.Random(seed + 100)
        base_vol = 60_000_000 / px0 if sym != "LOWV" else 40_000 / 1.0
        d = {"date": dates, "o": [], "h": [], "l": [], "c": [], "v": []}
        for o, h, l, c in bars:
            d["o"].append(round(o, 4)); d["h"].append(round(h, 4)); d["l"].append(round(l, 4)); d["c"].append(round(c, 4))
            d["v"].append(int(base_vol * (0.8 + 0.4 * rnd.random())))
        data[sym] = d
    return dates, data


def answers(dates, data, universe):
    stress = op.stress_flags(data["SPY"]["c"])
    inds = {s: op.prepare(d) for s, d in data.items() if s != "SPY"}
    exp = {}
    for i in range(len(dates) - EVAL_LAST, len(dates)):
        row = {"stress": bool(stress[i]), "eligible": [], "indicators": {}, "signals": {"V1": [], "V2": [], "V3": [], "V4": []}}
        for s, ind in inds.items():
            d = data[s]
            if not op.eligible(i, d, ind, universe, s):
                continue
            row["eligible"].append(s)
            row["indicators"][s] = {"rsi2": round(ind["rsi2"][i], 6), "zdev": round(ind["z"][i], 6), "sma200": round(ind["sma200"][i], 6), "adv20": round(ind["adv20"][i], 2)}
            for v in ("V1", "V2", "V3", "V4"):
                if op.signal(v, i, ind, d, bool(stress[i])):
                    row["signals"][v].append(s)
        exp[dates[i]] = row
    return exp


def main(argv):
    dates, data = build()
    universe = {2025: set(STOCKS), 2026: set(STOCKS)}
    out = {"about": "Synthetic daily bars (no real market data). Spec: S-042 oversold pullbacks. 'eligible' and 'signals' are the answers of our engine at the close of each evaluation date.",
           "constants": {"min_prior_bars": op.MIN_BARS, "min_adv20_usd": op.MIN_ADV, "min_price": op.MIN_PRICE},
           "universe": {str(y): sorted(s) for y, s in universe.items()}, "bars": data, "eval_dates": dates[-EVAL_LAST:], "expected": answers(dates, data, universe)}
    if argv:
        Path(argv[0]).write_text(json.dumps(out, separators=(",", ":")))
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
