"""S-048 daily breakout, exactly as frozen in Agentic-Lab system/06-research/specs/S-048-daily-breakout.md.

Signal at a close D: close above the highest close of the previous N sessions and above SMA200 (variants with the market filter also need SPY above its SMA200).
Rank: highest 63-session return first, then symbol. Stop 10 percent below the signal close. Exit at a close below the lowest close of the previous M sessions
(M = 10 for N = 20, M = 20 for N = 55) or on the 40th session held; filled at the next open. Everything at index i uses only closes before i (no look-ahead).
"""
from collections import deque

from consumer import oversold_panel as op
from engine.strategy import Strategy
from engine.types import Intent

NOTIONAL = 1000.0
STOP_FRACTION = 0.90
HOLD = 40
VARIANTS = {"B20": (20, 10, False), "B55": (55, 20, False), "B20M": (20, 10, True), "B55M": (55, 20, True)}


def prior_extreme(closes, n, pick):
    """out[i] = pick(closes[i-n:i]) (the n closes BEFORE i, never including i), None until n closes exist."""
    out, win = [None] * len(closes), deque()
    for i, c in enumerate(closes):
        if i >= n:
            out[i] = pick(win)
            win.popleft()
        win.append(c)
        if len(win) > n:
            win.popleft()
    return out


class Breakout(Strategy):
    def __init__(self, variant="B20"):
        self.n, self.m, self.market_filter = VARIANTS[variant]
        self.name = f"s048{variant.lower()}"
        self._hi, self._lo, self._spy_sma = {}, {}, None

    def _levels(self, sym, closes):
        if sym not in self._hi:
            self._hi[sym] = prior_extreme(closes, self.n, max)
            self._lo[sym] = prior_extreme(closes, self.m, min)
        return self._hi[sym], self._lo[sym]

    def _market_ok(self, ctx):
        if not self.market_filter:
            return True
        spy = ctx.data["SPY"]
        if self._spy_sma is None:
            self._spy_sma = op.sma(spy["c"], 200)
        i = ctx.idx["SPY"].get(ctx.date)
        return i is not None and self._spy_sma[i] is not None and spy["c"][i] > self._spy_sma[i]

    def signals(self, ctx):
        if not self._market_ok(ctx):
            return []
        out = []
        for sym, inds in ctx.inds.items():
            i = ctx.i(sym)
            if i is None:
                continue
            d = ctx.data[sym]
            if not op.eligible(i, d, inds, ctx.universe, sym):
                continue
            hi, _ = self._levels(sym, d["c"])
            c = d["c"][i]
            if hi[i] is None or i < 63 or c <= hi[i] or c <= inds["sma200"][i] or c < 1.0 or NOTIONAL // c < 1:
                continue
            ret63 = c / d["c"][i - 63] - 1.0
            out.append(Intent(self.name, sym, NOTIONAL, STOP_FRACTION, (-ret63, sym), c, f"close above prior {self.n}-session high"))
        return out

    def exit_reason(self, pos, ctx):
        i = ctx.i(pos.symbol)
        if i is None:
            return ""
        d = ctx.data[pos.symbol]
        _, lo = self._levels(pos.symbol, d["c"])
        if lo[i] is not None and d["c"][i] < lo[i]:
            return "TRAIL"
        if pos.sessions_held >= HOLD:
            return "TIME"
        return ""
