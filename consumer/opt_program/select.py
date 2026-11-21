"""Choose expiry and strikes for a spec, then apply the liquidity filter. Pure functions over the contract list and quotes, so they are tested without a broker."""
from __future__ import annotations

from datetime import date, timedelta

from feed_pipeline import calendar as cal

from . import policy as P
from . import structures as S
from .specs import has_short_legs


def add_sessions(day: date, n: int) -> date:
    d, k = day, 0
    while k < n:
        d += timedelta(days=1)
        if cal.is_session(d):
            k += 1
    return d


def monday_of_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def planned_exit(expiry: date, today: date, time_exit_dte: int, max_hold_sessions: int) -> date:
    d = expiry - timedelta(days=time_exit_dte)
    while not cal.is_session(d):
        d -= timedelta(days=1)
    return min(d, add_sessions(today, max_hold_sessions))


def choose_expiry(expiries, today: date, spec: dict):
    short = has_short_legs(spec["structure"])
    ex = spec["exit"]
    ok = []
    for e in sorted(set(expiries)):
        dte = (e - today).days
        if not (spec["dte_min"] <= dte <= spec["dte_max"]) or e in P.BLOCKED_EXPIRIES:
            continue
        pe = planned_exit(e, today, ex["time_exit_dte"], ex["max_hold_sessions"])
        if short and pe >= monday_of_week(e):
            continue
        ok.append(e)
    return min(ok, key=lambda e: (abs((e - today).days - spec["dte_target"]), e)) if ok else None


def make_leg(c: dict, side: str, ratio: int, q: dict) -> S.Leg:
    oi = c.get("open_interest")
    return S.Leg(symbol=c["symbol"], side=side, ratio=ratio, cp="C" if c["type"] == "call" else "P", strike=float(c["strike_price"]), expiry=date.fromisoformat(c["expiration_date"]),
                 bid=q["bid"], ask=q["ask"], delta=q.get("delta"), oi=int(oi) if oi not in (None, "") else None)


def _quoted(contracts, quotes):
    out = {}
    for c in contracts:
        q = quotes.get(c["symbol"])
        if q and q["bid"] > 0 and q["ask"] >= q["bid"]:
            out[float(c["strike_price"])] = (c, q)
    return out


def _by_delta(book: dict, target: float):
    cand = [(abs(abs(q["delta"]) - target), k) for k, (c, q) in book.items() if q.get("delta") is not None]
    return min(cand)[1] if cand else None


def candidates(spec: dict, spot: float, expiry: date, contracts: list, quotes: dict) -> list:
    """Possible structures for the spec, widest first. Each is (kind, legs). The engine takes the first that fits the risk rules, stepping toward the narrowest."""
    kind, lg = spec["structure"], spec["legs"]
    calls = _quoted([c for c in contracts if c["type"] == "call" and date.fromisoformat(c["expiration_date"]) == expiry], quotes)
    puts = _quoted([c for c in contracts if c["type"] == "put" and date.fromisoformat(c["expiration_date"]) == expiry], quotes)
    out = []
    if kind in ("long-call", "long-put"):
        book = calls if kind == "long-call" else puts
        k = _by_delta(book, lg["long_delta"])
        if k is not None:
            c, q = book[k]
            out.append((kind, [make_leg(c, "buy", 1, q)]))
    elif kind in ("debit-vertical-call", "debit-vertical-put", "credit-vertical-call", "credit-vertical-put"):
        book = calls if kind.endswith("call") else puts
        debit, call_side = kind.startswith("debit"), kind.endswith("call")
        k0 = _by_delta(book, lg["long_delta"] if debit else lg["short_delta"])
        if k0 is not None:
            # call spreads put the other leg at a higher strike, put spreads at a lower one; debit: buy k0 and sell the other, credit: sell k0 and buy the other
            others = sorted((k for k in book if (k > k0 if call_side else k < k0) and abs(k - k0) <= lg["width"] + 1e-9), key=lambda k: -abs(k - k0))
            for k1 in others:
                (c0, q0), (c1, q1) = book[k0], book[k1]
                legs = [make_leg(c0, "buy" if debit else "sell", 1, q0), make_leg(c1, "sell" if debit else "buy", 1, q1)]
                out.append((kind, legs))
    elif kind == "iron-condor":
        kp, kc = _by_delta(puts, lg["short_delta"]), _by_delta(calls, lg["short_delta"])
        if kp is not None and kc is not None and kp < kc:
            wp = sorted((k for k in puts if k < kp and kp - k <= lg["width"] + 1e-9), key=lambda k: -(kp - k))
            wc = sorted((k for k in calls if k > kc and k - kc <= lg["width"] + 1e-9), key=lambda k: -(k - kc))
            for a, b in zip(wp, wc):  # same-size wings, widest first
                if abs((kp - a) - (b - kc)) > 1e-9:
                    continue
                legs = [make_leg(puts[a][0], "buy", 1, puts[a][1]), make_leg(puts[kp][0], "sell", 1, puts[kp][1]),
                        make_leg(calls[kc][0], "sell", 1, calls[kc][1]), make_leg(calls[b][0], "buy", 1, calls[b][1])]
                out.append((kind, legs))
    elif kind in ("long-butterfly-call", "long-butterfly-put"):
        book = calls if kind.endswith("call") else puts
        if book:
            body = min(book, key=lambda k: abs(k - spot))
            for w in sorted({abs(k - body) for k in book if k != body and abs(k - body) <= lg["wing_width"] + 1e-9}, reverse=True):
                if (body - w) in book and (body + w) in book:
                    legs = [make_leg(book[body - w][0], "buy", 1, book[body - w][1]), make_leg(book[body][0], "sell", 2, book[body][1]), make_leg(book[body + w][0], "buy", 1, book[body + w][1])]
                    out.append((kind, legs))
    return out


def liquidity_ok(legs) -> tuple:
    """(ok, reason). Every leg: two-sided, a sold leg bids enough, width within 5 percent of mid (floor 0.05, ceiling 0.25), open interest enough when reported."""
    for lg in legs:
        if not (lg.bid > 0 and lg.ask >= lg.bid):
            return False, "NO_TWO_SIDED_QUOTE"
        if lg.side == "sell" and lg.bid < P.LEG_MIN_BID_SOLD:
            return False, "SOLD_LEG_BID_TOO_LOW"
        width = lg.ask - lg.bid
        limit = min(P.LEG_WIDTH_CEILING_USD, max(P.LEG_WIDTH_FLOOR_USD, P.LEG_WIDTH_FRACTION_OF_MID * lg.mid))
        if width > limit + 1e-9:
            return False, "LEG_SPREAD_TOO_WIDE"
        if lg.oi is not None and lg.oi < P.LEG_MIN_OPEN_INTEREST:
            return False, "OPEN_INTEREST_TOO_LOW"
    return True, ""


def pick(kind_legs: list, today: date):
    """Apply the risk-size rule across the stepped candidates. Returns (kind, legs, econ, info) or (None, None, None, reason).
    Rule: the widest version at or under MAX_LOSS_USD; if none fits, the narrowest version, allowed only up to MAX_LOSS_FALLBACK_USD."""
    evaluated = []
    for kind, legs in kind_legs:
        try:
            e = S.economics(kind, legs)
        except S.StructureError as exc:
            evaluated.append((None, kind, legs, str(exc)))
            continue
        evaluated.append((e, kind, legs, ""))
    good = [x for x in evaluated if x[0] is not None]
    if not good:
        return None, None, None, "NO_VALID_STRUCTURE"
    smallest = min(x[0]["max_loss_usd"] for x in good)
    fitting = [x for x in good if x[0]["max_loss_usd"] <= P.MAX_LOSS_USD]
    chosen = fitting[0] if fitting else min(good, key=lambda x: x[0]["max_loss_usd"])
    e, kind, legs, _ = chosen
    if not fitting and e["max_loss_usd"] > P.MAX_LOSS_FALLBACK_USD:
        return None, None, None, "TOO_BIG_EVEN_SMALLEST"
    info = {"smallest_version_risk_usd": round(smallest, 2), "used_fallback_cap": (not fitting)}
    ok, why = liquidity_ok(legs)
    if not ok:
        return None, None, None, why
    if e["friction_usd"] > P.STRUCTURE_FRICTION_MAX_FRACTION * e["max_loss_usd"] + 1e-9:
        return None, None, None, "STRUCTURE_FRICTION_TOO_HIGH"
    return kind, legs, e, info
