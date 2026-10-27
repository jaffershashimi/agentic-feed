"""Replay engine for frozen replay rules R2 (stock and option arms). Standard library only, offline, no broker,
no orders.

Reads 5-minute and daily bars from CSV files, replays the requested sessions bar by bar with no look-ahead and writes
the ledger rows (paper_ledger.py format plus a `score` column), the score parts and one record per decision point.
The same bars and settings always give the same output.

What R2 says, in short (the Drive doc "Replay Rules R2" is the source of truth):
- Decisions at each completed 15m bar ending 6:45 to 12:45 PT, only on a trigger: close above the high of the 4
  previous 15m bars (BREAK_1H_HIGH) or a session VWAP reclaim (VWAP_RECLAIM).
- Filters F1 to F7 (liquidity, price, 200-day trend, room to resistance, VWAP/ATR drift band, no entry before a pm
  earnings report, p2 caps per symbol trial), then the D3 score (context, trigger, liquidity, room, risk; 6+ enters).
- Size $500 at score 6 to 8, $750 at 9 or 10; stock fills at the next bar open with 0.05% slippage; 20% stop.
- Exits: fading momentum on 15m, 4h close under EMA20, earnings before the next open, 5-session time stop, then the
  stop and the 30% target, in that order inside one bar.

- Options (R2 sections 6 to 8, only with --option-bars): Arm A buys the call R2 picks (first expiry 21+ calendar
  days out, rough-delta candidates, IV solved from the option bar ending at the decision, delta 0.30 to 0.60, cost
  within the size); Arm B takes the same call when its IV / RV20 is 1.5 or lower, otherwise the stock. HARSH fills
  (buy the entry bar high, sell the exit bar low, $0.68 per contract per side); the 15% option stop and +30% target
  are checked on real option bars, and the underlying exit rules move the option to the first real option bar at
  or after the stock exit bar. REALISTIC prices (bar midpoint +/- 3%) go to decisions.json only.

Without --option-bars, every entry records arm A as MISSING_DATA and arm B takes the stock, which is what R2
section 6 prescribes when no option data exists. Each arm has its own p2 caps.

    python3 -I -m consumer.replay_r2 --bars bars5m.csv --daily daily.csv --days 2026-10-26 [--days ...]
        [--option-bars options5m.csv] [--chain chain.csv] [--events events.json] [--done replay_sessions.csv]
        [--out DIR] [--r2-hash c879c06e24a5]

bars5m.csv and daily.csv columns: symbol, begins_at, open, high, low, close, volume[, interpolated]. events.json:
{"earnings": [{"symbol": "X", "date": "YYYY-MM-DD", "timing": "am" | "pm"}], "macro": ["YYYY-MM-DD", ...]}; without
it the R2 macro list is used and no earnings are known. options5m.csv columns: underlying, expiration (YYYY-MM-DD),
strike, begins_at, open, high, low, close, volume[, interpolated] (calls only). chain.csv (underlying, expiration,
strike) lists every call strike; without it, the strikes and expirations in options5m.csv count as listed.
"""
import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from consumer.paper_ledger import COLUMNS, PT
from feed_pipeline import calendar

R2_HASH = "c879c06e24a5"
POLICY_HASH = "8782fd93fbd2"
R2_MACRO = ("2026-10-28", "2026-10-29")  # FOMC, GDP and PCE (R2 section 4)
OPEN, CLOSE = time(6, 30), time(13, 0)
FIRST_DECISION, LAST_DECISION = time(6, 45), time(12, 45)
EARNINGS_EXIT_BAR = time(12, 50)  # its close triggers the exit at the 12:55 bar open
EQUITY, MAX_OPEN, MAX_ENTRIES, MAX_OPEN_SIZE = 5000.0, 3, 5, 3750.0
FIVE = timedelta(minutes=5)
CRITERIA = ("context", "trigger", "liquidity", "room", "risk")
LEDGER_FIELDS = COLUMNS + ("score", "source")
PARTS_FIELDS = ("decision_id", "trade_id", "arm", "criterion", "points", "value")
EPS = 1e-9
RATE, OPTION_FEE, MIN_DTE = 0.04, 0.68, 21
ET = ZoneInfo("America/New_York")


def _pt(at):
    return at.astimezone(PT)


def _at(day, clock):
    return datetime.combine(day, clock, PT).astimezone(timezone.utc)


def load_bars(path):
    """5m bars per symbol, regular session only, interpolated bars dropped, sorted by start."""
    out = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("interpolated") or "").strip().lower() in ("true", "1", "yes"):
                continue
            start = datetime.fromisoformat(r["begins_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            if not OPEN <= _pt(start).time() < CLOSE:
                continue
            out[r["symbol"].strip()].append({"start": start, "day": _pt(start).date(), "o": float(r["open"]),
                                             "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]),
                                             "v": float(r["volume"])})
    return {s: sorted(bs, key=lambda b: b["start"]) for s, bs in out.items()}


def load_daily(path):
    out = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("interpolated") or "").strip().lower() in ("true", "1", "yes"):
                continue
            out[r["symbol"].strip()].append({"day": date.fromisoformat(r["begins_at"][:10]), "h": float(r["high"]),
                                             "c": float(r["close"]), "v": float(r["volume"])})
    return {s: sorted(ds, key=lambda d: d["day"]) for s, ds in out.items()}


def ema(values, n):
    """Standard EMA, alpha 2/(n+1), seeded with the first value; one output per input."""
    out, alpha = [], 2 / (n + 1)
    for x in values:
        out.append(x if not out else out[-1] + alpha * (x - out[-1]))
    return out


def derive(bars, minutes):
    """Session-aligned bars of `minutes` (15, 60 or 240) from 5m bars: start, end, high, low, close, volume.

    Windows start at 6:30 PT; the 1h and 4h windows are cut at 13:00 (12:30-13:00 and 10:30-13:00)."""
    groups = defaultdict(list)
    for b in bars:
        local = _pt(b["start"])
        offset = (local.hour * 60 + local.minute - (OPEN.hour * 60 + OPEN.minute)) // minutes * minutes
        groups[(b["day"], offset)].append(b)
    out = []
    for (day, offset), bs in sorted(groups.items()):
        start = _at(day, OPEN) + timedelta(minutes=offset)
        out.append({"start": start, "end": min(start + timedelta(minutes=minutes), _at(day, CLOSE)), "day": day,
                    "h": max(b["h"] for b in bs), "l": min(b["l"] for b in bs), "c": bs[-1]["c"],
                    "v": sum(b["v"] for b in bs)})
    return out


class Symbol:
    """Every indicator R2 needs for one symbol, precomputed so a lookup at time t only sees completed bars."""

    def __init__(self, bars, daily):
        self.bars, self.daily = bars, daily
        self.m15, self.h1, self.h4 = derive(bars, 15), derive(bars, 60), derive(bars, 240)
        self.ema15 = ema([b["c"] for b in self.m15], 9)
        self.ema1h = ema([b["c"] for b in self.h1], 21)
        self.ema4h = ema([b["c"] for b in self.h4], 20)
        self.daily_ema20 = ema([d["c"] for d in daily], 20)
        vwap, cum_pv, cum_v, cum_day = [], 0.0, 0.0, defaultdict(float)
        for i, b in enumerate(bars):
            if i == 0 or b["day"] != bars[i - 1]["day"]:
                cum_pv = cum_v = 0.0
            cum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
            cum_v += b["v"]
            cum_day[b["day"]] = cum_v
            vwap.append((cum_pv / cum_v if cum_v else b["c"], cum_v))
        self.vwap5 = vwap  # (session VWAP, cumulative session volume) after each 5m bar
        self.tr = [b["h"] - b["l"] if i == 0 else max(b["h"], bars[i - 1]["c"]) - min(b["l"], bars[i - 1]["c"])
                   for i, b in enumerate(bars)]
        self.end_index = {b["start"] + FIVE: i for i, b in enumerate(bars)}
        self.start_index = {b["start"]: i for i, b in enumerate(bars)}

    def last5(self, t):
        """Index of the last 5m bar completed by t, or None."""
        lo, hi = 0, len(self.bars)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.bars[mid]["start"] + FIVE <= t:
                lo = mid + 1
            else:
                hi = mid
        return lo - 1 if lo else None

    def vwap_at(self, t):
        i = self.last5(t)
        return self.vwap5[i] if i is not None and self.bars[i]["day"] == _pt(t - FIVE).date() else (None, 0.0)

    def atr(self, t):
        i = self.last5(t)
        return None if i is None or i < 13 else sum(self.tr[i - 13:i + 1]) / 14

    def completed(self, series, t):
        """Index of the last bar in a derived series that ended at or before t."""
        idx = None
        for i, b in enumerate(series):
            if b["end"] > t:
                break
            idx = i
        return idx

    def prior_daily(self, day):
        return [d for d in self.daily if d["day"] < day]


def _sma(closes, n, back=0):
    end = len(closes) - back
    return sum(closes[end - n:end]) / n if end >= n else None


def resistance(prior, price):
    """R2 F4: lowest of the prior session high and the confirmed daily swing highs of the last 60 sessions above price."""
    levels = [prior[-1]["h"]] if prior else []
    window = prior[-60:]
    for i in range(2, len(window) - 2):
        h = window[i]["h"]
        if all(h > window[j]["h"] for j in (i - 2, i - 1, i + 1, i + 2)):
            levels.append(h)
    above = [x for x in levels if x > price + EPS]
    return min(above) if above else None


def _next_sessions(day, count):
    out, d = [], day
    while len(out) < count:
        d += timedelta(days=1)
        if calendar.is_session(d):
            out.append(d)
    return out


def evaluate(sym, s, t, earnings, macro):
    """One decision point at t (end of a completed 15m bar). Returns a decision dict, or None without a trigger."""
    k = s.completed(s.m15, t)
    if k is None or s.m15[k]["end"] != t or k < 4:
        return None
    bar, day = s.m15[k], s.m15[k]["day"]
    price = bar["c"]
    vwap, cum_v = s.vwap_at(t)
    prev = s.m15[k - 1]
    prev_vwap = s.vwap_at(prev["end"])[0] if prev["day"] == day else None
    if price > max(b["h"] for b in s.m15[k - 4:k]) + EPS:
        trigger = "BREAK_1H_HIGH"
    elif vwap is not None and prev_vwap is not None and price > vwap + EPS and prev["c"] <= prev_vwap + EPS:
        trigger = "VWAP_RECLAIM"
    else:
        return None
    prior = s.prior_daily(day)
    closes = [d["c"] for d in prior]
    adv20 = sum(d["v"] for d in prior[-20:]) / 20 if len(prior) >= 20 else None
    atr = s.atr(t)
    level = resistance(prior, price)
    z = None if not atr or vwap is None else (price - vwap) / atr
    sma_now, sma_then = _sma(closes, 200), _sma(closes, 200, 20)
    failed = []
    if adv20 is None or adv20 < 1_000_000:
        failed.append("F1_LIQUIDITY")
    if price < 10:
        failed.append("F2_PRICE")
    if len(closes) < 221 or closes[-1] <= sma_now:
        failed.append("F3_TREND")
    if level is not None and (level - price) / price < 0.02 - EPS:
        failed.append("F4_ROOM")
    if z is None or not -0.5 - EPS <= z <= 2.5 + EPS:
        failed.append("F5_EXTENDED")
    if (sym, day, "pm") in earnings:
        failed.append("F6_EARNINGS")
    out = {"symbol": sym, "time": t, "day": day, "trigger": trigger, "price": price, "failed": failed,
           "values": {"adv20": adv20, "vwap": vwap, "atr": atr, "z": z, "resistance": level}}
    if failed:
        return out
    ema20_prior = s.daily_ema20[len(prior) - 1]
    i1, i4 = s.completed(s.h1, t), s.completed(s.h4, t)
    trend = bool(i1 is not None and i4 is not None and s.h1[i1]["c"] > s.ema1h[i1] and s.h4[i4]["c"] > s.ema4h[i4])
    elapsed = (t - _at(day, OPEN)).total_seconds() / 60
    rel_volume = cum_v / (adv20 * elapsed / 390)
    low = min(b["l"] for b in s.m15[k - 3:k + 1])
    ratio = None if level is None or price - low <= EPS else (level - price) / (price - low)
    window = [day] + _next_sessions(day, 2)
    if any((sym, d, w) in earnings for d in window for w in ("am", "pm")):
        risk = 0
    elif any(d.isoformat() in macro for d in window):
        risk = 1
    else:
        risk = 2
    parts = {
        "context": (int(closes[-1] > ema20_prior) + int(sma_now > sma_then),
                    f"close={closes[-1]:.4f};ema20={ema20_prior:.4f};sma200={sma_now:.4f};sma200_20ago={sma_then:.4f}"),
        "trigger": (1 if rel_volume < 1.0 else 1 + int(trend), f"{trigger};trend_1h_4h={int(trend)};rel_volume={rel_volume:.4f}"),
        "liquidity": (2 if adv20 >= 5_000_000 else 1, f"adv20={adv20:.0f}"),
        "room": (2 if ratio is None or ratio >= 2.0 else 1 if ratio >= 1.0 else 0,
                 f"R={'none' if level is None else f'{level:.4f}'};I={low:.4f};ratio={'none' if ratio is None else f'{ratio:.4f}'}"),
        "risk": (risk, f"window={window[0]}..{window[-1]}"),
    }
    out.update(parts=parts, score=sum(p for p, _v in parts.values()))
    return out


def stop_price(fill):
    return math.ceil(round(fill * 0.80 * 100, 6)) / 100  # rounded up to the cent, never looser


def exit_for(sym, s, entry_i, ref, fill, stop, earnings, price_rules=True):
    """First R2 exit for a stock position entered at the open of 5m bar entry_i. Returns (bar index, reason) or None.

    With price_rules False only the underlying rules apply (the stop and target of an option are on option bars)."""
    bars, entry_at = s.bars, s.bars[entry_i]["start"]
    entry_day = bars[entry_i]["day"]
    stop_day = (_next_sessions(entry_day, 5) or [entry_day])[-1]
    m15_end = {b["end"]: i for i, b in enumerate(s.m15)}
    h4_end = {b["end"]: i for i, b in enumerate(s.h4)}
    for j in range(entry_i, len(bars)):
        b, end = bars[j], bars[j]["start"] + FIVE
        reason = None
        k = m15_end.get(end)
        if k is not None and s.m15[k]["start"] >= entry_at:
            vwap = s.vwap_at(end)[0]
            c = s.m15[k]["c"]
            if c > ref + EPS and c < s.ema15[k] - EPS and vwap is not None and c < vwap - EPS:
                reason = "EXIT_FADING_MOMENTUM"
        h = h4_end.get(end)
        if reason is None and h is not None and s.h4[h]["end"] > entry_at and s.h4[h]["c"] < s.ema4h[h] - EPS:
            reason = "EXIT_4H_CLOSE_UNDER_EMA20"
        if reason is None and _pt(b["start"]).time() == EARNINGS_EXIT_BAR:
            nxt = _next_sessions(b["day"], 1)[0]
            if (sym, b["day"], "pm") in earnings or (sym, nxt, "am") in earnings:
                reason = "EXIT_EARNINGS_BEFORE_CLOSE"
        last_of_day = j + 1 == len(bars) or bars[j + 1]["day"] != b["day"]
        if reason is None and last_of_day and b["day"] >= stop_day:
            reason = "EXIT_TIME_STOP_5D"
        if reason is None and price_rules and b["l"] <= stop + EPS:
            reason = "EXIT_STOP_20PCT"
        if reason is None and price_rules and b["c"] >= fill * 1.30 - EPS:
            reason = "EXIT_TARGET_30PCT"
        if reason:
            return (j + 1, reason) if j + 1 < len(bars) else None
    return None


def load_option_bars(path):
    """Call bars per underlying and (expiration, strike), regular session only, interpolated bars dropped."""
    out = defaultdict(lambda: defaultdict(list))
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("interpolated") or "").strip().lower() in ("true", "1", "yes"):
                continue
            start = datetime.fromisoformat(r["begins_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            if not OPEN <= _pt(start).time() < CLOSE:
                continue
            key = (date.fromisoformat(r["expiration"][:10]), float(r["strike"]))
            out[r["underlying"].strip()][key].append({"start": start, "o": float(r["open"]), "h": float(r["high"]),
                                                      "l": float(r["low"]), "c": float(r["close"]), "v": float(r["volume"])})
    return {u: {k: sorted(bs, key=lambda b: b["start"]) for k, bs in m.items()} for u, m in out.items()}


def load_chain(path):
    out = defaultdict(lambda: defaultdict(set))
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["underlying"].strip()][date.fromisoformat(r["expiration"][:10])].add(float(r["strike"]))
    return {u: {e: sorted(s) for e, s in m.items()} for u, m in out.items()}


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(spot, strike, years, vol, rate=RATE):
    """Black-Scholes call price and delta (no dividend)."""
    if years <= 0 or vol <= 0:
        return max(spot - strike, 0.0), 1.0 if spot > strike else 0.0
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    return spot * _ncdf(d1) - strike * math.exp(-rate * years) * _ncdf(d2), _ncdf(d1)


def implied_vol(price, spot, strike, years, rate=RATE):
    """Volatility that reproduces price (bisection, 1% to 500%), or None when no volatility in that range does."""
    lo, hi = 0.01, 5.0
    if not bs_call(spot, strike, years, lo, rate)[0] - EPS <= price <= bs_call(spot, strike, years, hi, rate)[0] + EPS:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_call(spot, strike, years, mid, rate)[0] < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def years_to(t, expiry):
    """Calendar years from t to 16:00 ET on the expiry date."""
    return (datetime.combine(expiry, time(16, 0), ET) - t).total_seconds() / (365 * 86400)


def rv20(prior):
    """Sample standard deviation of the last 20 daily log returns, annualized with sqrt(252)."""
    closes = [d["c"] for d in prior[-21:]]
    if len(closes) < 21:
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)) * math.sqrt(252)


def occ(sym, expiry, strike):
    return f"{sym}{expiry:%y%m%d}C{round(strike * 1000):08d}"


def choose_option(sym, d, prior, listed, series, size):
    """R2 section 6 Arm A contract for decision d. Returns a dict with status OK, NO_FIT or MISSING_DATA."""
    t, spot = d["time"], d["price"]
    rv = rv20(prior)
    expiries = sorted(e for e in listed if e >= d["day"] + timedelta(days=MIN_DTE))
    if rv is None or not expiries:
        return {"status": "MISSING_DATA", "rv20": rv, "candidates": []}
    expiry = expiries[0]
    years = years_to(t, expiry)
    rough_vol = 1.3 * max(rv, 0.15)
    rough = [(abs(delta - 0.45), k, delta) for k in listed[expiry]
             for delta in [bs_call(spot, k, years, rough_vol)[1]] if 0.22 - EPS <= delta <= 0.68 + EPS]
    candidates, with_data = [], []
    for _gap, strike, _delta in sorted(rough)[:10]:
        bars = {b["start"]: b for b in series.get((expiry, strike), [])}
        prev, entry = bars.get(t - FIVE), bars.get(t)
        if not prev or not entry:
            candidates.append({"strike": strike, "status": "NO_BARS"})
            continue
        iv = implied_vol(prev["c"], spot, strike, years)
        delta = bs_call(spot, strike, years, iv)[1] if iv else None
        cost = entry["h"] * 100 + OPTION_FEE
        c = {"strike": strike, "iv": iv, "delta": delta, "cost": round(cost, 2), "entry_bar": entry}
        c["eligible"] = bool(delta is not None and 0.30 - EPS <= delta <= 0.60 + EPS and cost <= size + EPS)
        candidates.append(c)
        with_data.append(c)
    eligible = [c for c in with_data if c["eligible"]]
    out = {"expiry": expiry, "rv20": rv, "candidates": candidates}
    if not with_data:
        return dict(out, status="MISSING_DATA")
    if not eligible:
        return dict(out, status="NO_FIT")
    pick = min(eligible, key=lambda c: (abs(c["delta"] - 0.45), c["strike"]))
    return dict(out, status="OK", strike=pick["strike"], iv=pick["iv"], delta=pick["delta"], ivrv=pick["iv"] / rv,
                qty=int(size // pick["cost"]), fill=pick["entry_bar"]["h"], entry_bar=pick["entry_bar"],
                occ=occ(sym, expiry, pick["strike"]))


def option_stop(fill):
    return math.ceil(round(fill * 0.85 * 100, 6)) / 100  # rounded up to the cent, never looser


def option_exit(obars, entry_start, fill, stop, underlying):
    """R2 section 8 for an option: (exit bar, price, reason) or None. underlying = (stock exit bar start, reason)."""
    later = [b for b in obars if b["start"] > entry_start]
    for i, b in enumerate(later):
        if underlying and b["start"] >= underlying[0]:
            return b, b["l"], f"EXIT_MIRROR_{underlying[1]}"
        if b["l"] <= stop + EPS:
            return b, b["l"], "EXIT_STOP_15PCT"
        if b["c"] >= fill * 1.30 - EPS:
            return (later[i + 1], later[i + 1]["l"], "EXIT_TARGET_30PCT_NEXT_BAR") if i + 1 < len(later) else None
    return None


def _realistic(entry_bar, exit_bar, qty):
    buy = (entry_bar["h"] + entry_bar["l"]) / 2 * 1.03
    sell = (exit_bar["h"] + exit_bar["l"]) / 2 * 0.97
    return {"entry": round(buy, 4), "exit": round(sell, 4),
            "net": round((sell - buy) * 100 * qty - 2 * OPTION_FEE * qty, 2)}


def _row(**kw):
    return {f: kw.get(f, "") for f in LEDGER_FIELDS}


def _capped(trades, at, day, size):
    live = [x for x in trades if x[1] is None or x[1] > at]
    today = [x for x in trades if _pt(x[0]).date() == day]
    return len(live) >= MAX_OPEN or len(today) >= MAX_ENTRIES or sum(x[2] for x in live) + size > MAX_OPEN_SIZE + EPS


def replay(bars, daily, days, earnings=(), macro=R2_MACRO, done=(), r2_hash=R2_HASH, option_bars=None, chain=None):
    """Replay `days` for every symbol with both bar sets. Returns ledger rows, score parts, decisions and new keys.

    option_bars (from load_option_bars) turns on the option arms; chain (from load_chain) lists the strikes, else the
    strikes with bars count as listed."""
    earnings, macro, done = set(earnings), set(macro), set(done)
    ledger, parts, decisions, keys = [], [], [], []
    source = f"REPLAY_R2;R2_{r2_hash}"
    for sym in sorted(set(bars) & set(daily)):
        s = Symbol(bars[sym], daily[sym])
        series = (option_bars or {}).get(sym, {})
        listed = (chain or {}).get(sym)
        if listed is None:
            listed = defaultdict(list)
            for expiry, strike in sorted(series):
                listed[expiry].append(strike)
        trades = {"A": [], "B": []}  # per arm: (entry time, exit time or None, size)
        for day in sorted(days):
            key = f"R2|{day.isoformat()}|{sym}"
            if key in done or not any(b["day"] == day for b in s.bars):
                continue
            keys.append(key)
            t = _at(day, FIRST_DECISION)
            while t <= _at(day, LAST_DECISION):
                d = evaluate(sym, s, t, earnings, macro)
                t += timedelta(minutes=15)
                if d is None:
                    continue
                stamp = _pt(d["time"]).strftime("%Y%m%d-%H%M")
                d["decision_id"] = f"R2-{stamp[:8]}-{sym}-{stamp[9:]}"
                decisions.append(d)
                if d["failed"] or d["score"] < 6:
                    d["action"] = "SKIP"
                    continue
                entry_i = s.start_index.get(d["time"])
                if entry_i is None:
                    d["action"] = "SKIP_NO_ENTRY_BAR"
                    continue
                size = 750.0 if d["score"] >= 9 else 500.0
                pick = (choose_option(sym, d, s.prior_daily(day), listed, series, size) if option_bars is not None
                        else {"status": "MISSING_DATA", "candidates": []})
                d["option"] = {k: v for k, v in pick.items() if k not in ("entry_bar", "candidates")}
                d["option"]["candidates"] = [{k: v for k, v in c.items() if k != "entry_bar"} for c in pick["candidates"]]
                ref = s.bars[entry_i]["o"]
                underlying = exit_for(sym, s, entry_i, ref, ref, 0.0, earnings, price_rules=False)
                und = (s.bars[underlying[0]]["start"], underlying[1]) if underlying else None
                entered = []
                for arm_letter in ("A", "B"):
                    if _capped(trades[arm_letter], d["time"], day, size):
                        d[f"arm_{arm_letter.lower()}"] = "CAP_D4"
                        if arm_letter == "B":
                            d["failed"].append("F7_CAPS")
                        continue
                    use_option = pick["status"] == "OK" and (arm_letter == "A" or pick["ivrv"] <= 1.5 + EPS)
                    if arm_letter == "A" and not use_option:
                        d["arm_a"] = pick["status"]
                        continue
                    arm, tid = f"REPLAY-{arm_letter}-R2-{sym}", f"R2{arm_letter}-{sym}-{stamp}"
                    at = s.bars[entry_i]["start"].isoformat()
                    common = dict(mode="REPLAY", arm=arm, trade_id=tid, symbol=sym, policy_hash=POLICY_HASH,
                                  decision_id=d["decision_id"], score=str(d["score"]))
                    if use_option:
                        fill, qty = pick["fill"], pick["qty"]
                        stop = option_stop(fill)
                        tag = (f"RH_OPT_5M;BUY_BAR_HIGH;{pick['occ']};DELTA_{pick['delta']:.2f};IV_{pick['iv']:.3f};"
                               f"IVRV_{pick['ivrv']:.2f}" + (";ARMB_OPTION_PATH" if arm_letter == "B" else ""))
                        common.update(instrument="option", qty=str(qty), multiplier="100")
                        fee = f"{OPTION_FEE * qty:.2f}"
                        ledger.append(_row(**common, event_id=f"{tid}-E", kind="entry", occurred_at=at, quote_time=at,
                                           side="buy", price=f"{fill:.2f}", ref_price=f"{fill:.2f}", fees=fee,
                                           source=f"{source};{tag}"))
                        ledger.append(_row(**common, event_id=f"{tid}-S", kind="stop_set", occurred_at=at, side="sell",
                                           price=f"{stop:.2f}", source=f"{source};STOP_MINUS_15PCT_CEIL"))
                        found = option_exit(series[(pick["expiry"], pick["strike"])], d["time"], fill, stop, und)
                        exit_at = found[0]["start"] if found else None
                        if found:
                            xbar, xprice, reason = found
                            xat = xbar["start"].isoformat()
                            ledger.append(_row(**common, event_id=f"{tid}-X", kind="exit", occurred_at=xat, quote_time=xat,
                                               side="sell", price=f"{xprice:.2f}", ref_price=f"{xprice:.2f}", fees=fee,
                                               source=f"{source};RH_OPT_5M;SELL_BAR_LOW;{reason}"))
                            d[f"realistic_{arm_letter.lower()}"] = _realistic(pick["entry_bar"], xbar, qty)
                        d[f"arm_{arm_letter.lower()}"] = "OPTION"
                    else:
                        fill = round(ref * 1.0005, 4)
                        qty = math.floor(size / fill * 1e6) / 1e6
                        stop = stop_price(fill)
                        common.update(instrument="equity", qty=f"{qty:.6f}", multiplier="1")
                        ledger.append(_row(**common, event_id=f"{tid}-E", kind="entry", occurred_at=at, quote_time=at,
                                           side="buy", price=f"{fill:.4f}", ref_price=f"{ref:.4f}", fees="0",
                                           source=f"{source};{d['trigger']}"))
                        ledger.append(_row(**common, event_id=f"{tid}-S", kind="stop_set", occurred_at=at, side="sell",
                                           price=f"{stop:.2f}", source=f"{source};STOP_20PCT"))
                        found = exit_for(sym, s, entry_i, ref, fill, stop, earnings)
                        exit_at = s.bars[found[0]]["start"] if found else None
                        reason = found[1] if found else None
                        if found:
                            xref = s.bars[found[0]]["o"]
                            xat = exit_at.isoformat()
                            ledger.append(_row(**common, event_id=f"{tid}-X", kind="exit", occurred_at=xat, quote_time=xat,
                                               side="sell", price=f"{xref * 0.9995:.4f}", ref_price=f"{xref:.4f}", fees="0",
                                               source=f"{source};{reason}"))
                        d["arm_b"] = "STOCK"
                    trades[arm_letter].append((d["time"], exit_at, size))
                    entered.append(tid)
                    d[f"exit_{arm_letter.lower()}"] = reason if found else "UNRESOLVED"
                    for name in CRITERIA:
                        points, value = d["parts"][name]
                        parts.append({"decision_id": d["decision_id"], "trade_id": tid, "arm": arm, "criterion": name,
                                      "points": points, "value": value})
                if not entered:
                    d["action"] = "SKIP"
                    continue
                d.update(action="ENTER", trade_ids=entered)
                if "exit_b" in d:  # kept for readers of the stock-arm fields
                    d.update(trade_id=f"R2B-{sym}-{stamp}", exit=d["exit_b"])
    return ledger, parts, decisions, keys


def _decision_json(d):
    out = {k: v for k, v in d.items() if k not in ("time", "day", "parts")}
    out["time"] = d["time"].isoformat()
    if "parts" in d:
        out["parts"] = {k: p for k, (p, _v) in d["parts"].items()}
    if "option" in d and "expiry" in d["option"]:
        out["option"] = dict(d["option"], expiry=d["option"]["expiry"].isoformat())
    return out


def load_events(path):
    if not path:
        return set(), set(R2_MACRO)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    earnings = {(e["symbol"], date.fromisoformat(e["date"]), e["timing"]) for e in data.get("earnings", [])}
    bad = [e for e in earnings if e[2] not in ("am", "pm")]
    if bad:
        raise ValueError(f"earnings timing must be am or pm: {sorted(bad)}")
    return earnings, set(data.get("macro", R2_MACRO))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bars", required=True)
    ap.add_argument("--daily", required=True)
    ap.add_argument("--days", required=True, action="append", type=date.fromisoformat)
    ap.add_argument("--option-bars", help="call 5m bars CSV; turns on the option arms")
    ap.add_argument("--chain", help="listed call strikes CSV (underlying, expiration, strike)")
    ap.add_argument("--events")
    ap.add_argument("--done", help="replay_sessions.csv; keys already replayed under R2 are skipped")
    ap.add_argument("--out", default=".")
    ap.add_argument("--r2-hash", default=R2_HASH)
    a = ap.parse_args(argv)
    try:
        earnings, macro = load_events(a.events)
    except (ValueError, KeyError) as exc:
        print(json.dumps({"errors": [f"events: {exc}"]}))
        return 2
    done = set()
    if a.done and Path(a.done).exists():
        with open(a.done, newline="", encoding="utf-8") as fh:
            done = {r["key"] for r in csv.DictReader(fh)}
    option_bars = load_option_bars(a.option_bars) if a.option_bars else None
    chain = load_chain(a.chain) if a.chain else None
    ledger, parts, decisions, keys = replay(load_bars(a.bars), load_daily(a.daily), a.days, earnings, macro, done, a.r2_hash,
                                            option_bars, chain)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, fields, rows in (("replay_fills.csv", LEDGER_FIELDS, ledger), ("score_parts.csv", PARTS_FIELDS, parts),
                               ("replay_sessions_new.csv", ("key",), [{"key": k} for k in keys])):
        with open(out / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    (out / "decisions.json").write_text(json.dumps([_decision_json(d) for d in decisions], indent=1), encoding="utf-8")
    entered = [d for d in decisions if d.get("action") == "ENTER"]
    print(json.dumps({"sessions": len(keys), "decisions": len(decisions), "entries": len(entered),
                      "unresolved": sum(v == "UNRESOLVED" for d in entered for k, v in d.items() if k.startswith("exit_")),
                      "arm_a": dict(sorted(_count(d["arm_a"] for d in decisions if "arm_a" in d).items())),
                      "arm_b": dict(sorted(_count(d["arm_b"] for d in decisions if "arm_b" in d).items())),
                      "skips": dict(sorted(_count(f for d in decisions for f in d["failed"]).items())),
                      "files": sorted(p.name for p in out.iterdir() if p.suffix in (".csv", ".json"))}, indent=1))
    return 0


def _count(items):
    out = defaultdict(int)
    for x in items:
        out[x] += 1
    return out


if __name__ == "__main__":
    sys.exit(main())
