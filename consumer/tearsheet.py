"""Dependency-free SVG performance tear sheet for a daily return series (research diagnostics, never an order path).

Panels: headline numbers, equity curve with optional shaded test windows and a benchmark, underwater drawdown, calendar-year bars.
Standard library only, deterministic output, no network. Inspired by the layout of open-source tear sheets (QuantStats, pyfolio) but
written from scratch so it runs in CI with nothing installed.

Run from code:  render(title, subtitle, dates, returns, benchmark=None, windows=None, notes=None) -> str
"""
import math
from collections import OrderedDict

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
C = dict(bg1="#0b1020", bg2="#131c38", card="#18223f", line="#2c3a66", text="#e8ecf7", dim="#9aa6c4", up="#3DDC97", down="#FF6B6B",
         line1="#6EA8FE", line2="#9aa6c4", shade=["#6EA8FE", "#B48CFF", "#F2C94C"])


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def equity(returns, start=1.0):
    out, v = [], start
    for r in returns:
        v *= 1.0 + r
        out.append(v)
    return out


def drawdown(eq):
    peak, out = 0.0, []
    for v in eq:
        peak = max(peak, v)
        out.append(v / peak - 1.0 if peak > 0 else 0.0)
    return out


def yearly(dates, returns):
    by = OrderedDict()
    for d, r in zip(dates, returns):
        y = d[:4]
        by[y] = (1.0 + by.get(y, 0.0)) * (1.0 + r) - 1.0
    return by


def stats(returns, periods=252):
    n = len(returns)
    if n < 2:
        raise ValueError("need at least two returns")
    eq = equity(returns)
    mean = sum(returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
    years = n / periods
    cagr = eq[-1] ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    mdd = min(drawdown(eq))
    dn = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / n)
    return {"total": eq[-1] - 1.0, "cagr": cagr, "vol": sd * math.sqrt(periods), "sharpe": (mean / sd * math.sqrt(periods)) if sd > 0 else 0.0,
            "sortino": (mean / dn * math.sqrt(periods)) if dn > 0 else 0.0, "max_dd": mdd, "calmar": (cagr / abs(mdd)) if mdd < 0 else 0.0,
            "hit": (sum(1 for r in returns if r > 0) / max(sum(1 for r in returns if r != 0), 1)), "days": n}


def _pct(x, d=1):
    return f"{x * 100:+.{d}f}%"


def _scale(vals, lo, hi, y0, y1):
    span = (hi - lo) or 1.0
    return [y1 - (v - lo) / span * (y1 - y0) for v in vals]


def _path(xs, ys):
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))


def render(title, subtitle, dates, returns, benchmark=None, windows=None, notes=None):
    if len(dates) != len(returns):
        raise ValueError("dates and returns differ in length")
    st = stats(returns)
    eq = equity(returns)
    dd = drawdown(eq)
    w, h = 1200, 930
    px0, px1 = 70, 1130
    n = len(returns)
    xs = [px0 + i / (n - 1) * (px1 - px0) for i in range(n)]
    parts = []
    parts.append(f'<text x="40" y="52" font-size="26" font-weight="700" fill="{C["text"]}">{esc(title)}</text>')
    parts.append(f'<text x="40" y="78" font-size="14" fill="{C["dim"]}">{esc(subtitle)}</text>')
    tiles = [("Total return", _pct(st["total"])), ("CAGR", _pct(st["cagr"])), ("Volatility", f"{st['vol'] * 100:.1f}%"), ("Sharpe", f"{st['sharpe']:.2f}"),
             ("Sortino", f"{st['sortino']:.2f}"), ("Max drawdown", _pct(st["max_dd"])), ("Calmar", f"{st['calmar']:.2f}"), ("Up days", f"{st['hit'] * 100:.0f}%")]
    tw = (px1 - 40 + 0) / len(tiles)
    for i, (k, v) in enumerate(tiles):
        x = 40 + i * (1090 / len(tiles))
        parts.append(f'<rect x="{x:.1f}" y="100" width="{1090 / len(tiles) - 10:.1f}" height="64" rx="12" fill="{C["card"]}" stroke="{C["line"]}"/>')
        parts.append(f'<text x="{x + 14:.1f}" y="124" font-size="12" fill="{C["dim"]}">{esc(k)}</text>')
        col = C["text"]
        parts.append(f'<text x="{x + 14:.1f}" y="152" font-size="22" font-weight="700" fill="{col}">{esc(v)}</text>')
    # equity panel
    top, bot = 200, 520
    parts.append(f'<text x="40" y="190" font-size="14" font-weight="600" fill="{C["text"]}">Growth of 1.00 (log scale)</text>')
    series = [eq] + ([equity(benchmark)] if benchmark and len(benchmark) == n else [])
    flat = [math.log(v) for s in series for v in s]
    lo, hi = min(flat), max(flat)
    pad = (hi - lo) * 0.06 or 0.1
    lo, hi = lo - pad, hi + pad
    for k in range(5):
        gy = top + (bot - top) * k / 4
        val = math.exp(hi - (hi - lo) * k / 4)
        parts.append(f'<line x1="{px0}" y1="{gy:.1f}" x2="{px1}" y2="{gy:.1f}" stroke="#ffffff" stroke-opacity="0.07"/>')
        parts.append(f'<text x="{px0 - 8}" y="{gy + 4:.1f}" font-size="11" fill="{C["dim"]}" text-anchor="end">{val:.2f}</text>')
    if windows:
        for j, (name, (a, b)) in enumerate(windows.items()):
            idx = [i for i, d in enumerate(dates) if a <= d <= b]
            if not idx:
                continue
            x0, x1 = xs[idx[0]], xs[idx[-1]]
            col = C["shade"][j % len(C["shade"])]
            parts.append(f'<rect x="{x0:.1f}" y="{top}" width="{max(x1 - x0, 1):.1f}" height="{bot - top}" fill="{col}" fill-opacity="0.07"/>')
            parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{top + 16}" font-size="11" fill="{col}" text-anchor="middle" font-weight="600">{esc(name)}</text>')
    for si, s in enumerate(series):
        ys = _scale([math.log(v) for v in s], lo, hi, top, bot)
        col = C["line1"] if si == 0 else C["line2"]
        parts.append(f'<path d="{_path(xs, ys)}" fill="none" stroke="{col}" stroke-width="{2.2 if si == 0 else 1.4}" stroke-linejoin="round"/>')
    parts.append(f'<text x="{px1}" y="190" font-size="12" fill="{C["line1"]}" text-anchor="end">strategy</text>')
    if len(series) > 1:
        parts.append(f'<text x="{px1 - 76}" y="190" font-size="12" fill="{C["line2"]}" text-anchor="end">benchmark</text>')
    years = sorted({d[:4] for d in dates})
    for y in years:
        i = next(i for i, d in enumerate(dates) if d[:4] == y)
        parts.append(f'<text x="{xs[i]:.1f}" y="{bot + 20}" font-size="11" fill="{C["dim"]}">{y}</text>')
    # drawdown panel
    t2, b2 = 580, 700
    parts.append(f'<text x="40" y="568" font-size="14" font-weight="600" fill="{C["text"]}">Drawdown from peak</text>')
    mdd = min(dd) or -0.01
    ys = _scale(dd, mdd * 1.05, 0.0, t2, b2)
    area = f"M{xs[0]:.1f},{b2 - (b2 - t2) * 0:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = f"M{xs[0]:.1f},{t2} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + f" L{xs[-1]:.1f},{t2} Z"
    parts.append(f'<path d="{area}" fill="{C["down"]}" fill-opacity="0.28" stroke="{C["down"]}" stroke-width="1"/>')
    parts.append(f'<text x="{px0 - 8}" y="{t2 + 4}" font-size="11" fill="{C["dim"]}" text-anchor="end">0%</text>')
    parts.append(f'<text x="{px0 - 8}" y="{b2 + 4}" font-size="11" fill="{C["dim"]}" text-anchor="end">{mdd * 100:.0f}%</text>')
    # yearly bars
    yr = yearly(dates, returns)
    t3, b3 = 750, 840
    parts.append(f'<text x="40" y="738" font-size="14" font-weight="600" fill="{C["text"]}">Calendar-year return (first and last years are partial)</text>')
    m = max(abs(v) for v in yr.values()) or 0.01
    zero = (t3 + b3) / 2
    bw = (px1 - px0) / len(yr)
    parts.append(f'<line x1="{px0}" y1="{zero}" x2="{px1}" y2="{zero}" stroke="#ffffff" stroke-opacity="0.2"/>')
    for i, (y, v) in enumerate(yr.items()):
        hh = abs(v) / m * (b3 - t3) / 2
        x = px0 + i * bw + bw * 0.2
        yy = zero - hh if v >= 0 else zero
        parts.append(f'<rect x="{x:.1f}" y="{yy:.1f}" width="{bw * 0.6:.1f}" height="{max(hh, 1):.1f}" rx="3" fill="{C["up"] if v >= 0 else C["down"]}"/>')
        ly = yy - 6 if v >= 0 else yy + hh + 14
        parts.append(f'<text x="{x + bw * 0.3:.1f}" y="{ly:.1f}" font-size="11" fill="{C["text"]}" text-anchor="middle">{v * 100:+.1f}%</text>')
        parts.append(f'<text x="{x + bw * 0.3:.1f}" y="{b3 + 22}" font-size="11" fill="{C["dim"]}" text-anchor="middle">{y}</text>')
    foot = notes or []
    for i, line in enumerate(foot[:2]):
        parts.append(f'<text x="40" y="{h - 36 + i * 16}" font-size="11" fill="{C["dim"]}">{esc(line)}</text>')
    body = "\n".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{esc(title)}" font-family="{FONT}">\n'
            f'<title>{esc(title)}</title>\n<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C["bg1"]}"/>'
            f'<stop offset="1" stop-color="{C["bg2"]}"/></linearGradient></defs>\n<rect width="{w}" height="{h}" rx="18" fill="url(#bg)"/>\n{body}\n</svg>\n')
