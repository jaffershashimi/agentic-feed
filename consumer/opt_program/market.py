"""Read-only market data for the options engine (Alpaca data host and the contract list). Everything goes through the injected call(method, path, body, host)."""
from __future__ import annotations

import math
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from consumer import alpaca_options as ao

from . import structures as S


class Market:
    def __init__(self, call):
        self.call = call

    def spot(self, root: str) -> float:
        return float(self.call("GET", f"/v2/stocks/{root}/trades/latest?feed=iex", host="data")["trade"]["p"])

    def daily_closes(self, root: str, today: date, n: int = 80) -> list:
        """Closes of completed sessions only (SIP daily bars older than today)."""
        q = {"timeframe": "1Day", "start": (today - timedelta(days=n * 2)).isoformat(), "end": (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "adjustment": "all", "feed": "sip", "limit": str(n + 20)}
        bars = self.call("GET", f"/v2/stocks/{root}/bars?" + urllib.parse.urlencode(q), host="data").get("bars") or []
        return [float(b["c"]) for b in bars if str(b.get("t", ""))[:10] < today.isoformat()]

    def contracts(self, root: str, cp: str, lo: date, hi: date, spot: float, band: float = 0.20) -> list:
        q = {"underlying_symbols": root, "status": "active", "type": "call" if cp == "C" else "put", "expiration_date_gte": lo.isoformat(), "expiration_date_lte": hi.isoformat(),
             "strike_price_gte": f"{spot * (1 - band):.2f}", "strike_price_lte": f"{spot * (1 + band):.2f}", "limit": "1000"}
        return self.call("GET", "/v2/options/contracts?" + urllib.parse.urlencode(q)).get("option_contracts", [])

    def quotes(self, symbols) -> dict:
        return ao.get_quotes(self.call, symbols)


def signals(closes: list, iv: float | None = None) -> dict:
    """ret_1d, ret_5d, rsi14, above_sma50, rv20 (annualised), iv_rv. None when the history is too short."""
    out = {}
    if len(closes) >= 6:
        out["ret_1d"] = closes[-1] / closes[-2] - 1
        out["ret_5d"] = closes[-1] / closes[-6] - 1
    if len(closes) >= 15:
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(len(closes) - 14, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(len(closes) - 14, len(closes))]
        ag, al = sum(gains) / 14, sum(losses) / 14
        out["rsi14"] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    if len(closes) >= 50:
        out["above_sma50"] = closes[-1] > sum(closes[-50:]) / 50
    if len(closes) >= 21:
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 20, len(closes))]
        m = sum(rets) / 20
        out["rv20"] = math.sqrt(sum((r - m) ** 2 for r in rets) / 19) * math.sqrt(252)
        if iv:
            out["iv_rv"] = iv / out["rv20"]
    return out


def conditions_met(cond: dict, sig: dict):
    """(True/False, reason). Missing signal data fails closed."""
    for k, v in cond.items():
        name = k.rsplit("_", 1)[0]
        if k == "above_sma50":
            have = sig.get("above_sma50")
            if have is None or have != v:
                return False, "COND_" + k
            continue
        have = sig.get(name)
        if have is None:
            return False, "NO_SIGNAL_DATA"
        if k.endswith("_max") and not have <= v:
            return False, "COND_" + k
        if k.endswith("_min") and not have >= v:
            return False, "COND_" + k
    return True, ""
