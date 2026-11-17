"""S-042 V3 (oversold pullback in market stress), exactly as the forward tool trades it: decided at the close, bought at the next open, stop at 90 percent of the signal close,
exit when the close is above SMA5 or on the 5th session (entry day counts as 1), at most 10 open, 1,000 USD each, lowest RSI2 then most negative z first."""
from consumer import oversold_panel as op
from engine.strategy import Strategy
from engine.types import Intent

NOTIONAL = 1000.0
STOP_FRACTION = 0.90
HOLD = op.MAX_DAYS


class OversoldV3(Strategy):
    name = "s042v3"

    def signals(self, ctx):
        if not ctx.stress:
            return []
        out = []
        for sym, inds in ctx.inds.items():
            i = ctx.i(sym)
            if i is None:
                continue
            d = ctx.data[sym]
            if not op.eligible(i, d, inds, ctx.universe, sym):
                continue
            if not op.signal("V2", i, inds, d, True):
                continue
            c = d["c"][i]
            if c < 1.0 or NOTIONAL // c < 1:
                continue
            out.append(Intent(self.name, sym, NOTIONAL, STOP_FRACTION, (inds["rsi2"][i], inds["z"][i], sym), c, "z<=-2 in stress"))
        return out

    def exit_reason(self, pos, ctx):
        i = ctx.i(pos.symbol)
        if i is None:
            return ""
        d, inds = ctx.data[pos.symbol], ctx.inds[pos.symbol]
        s5 = inds["sma5"][i]
        if s5 is not None and d["c"][i] > s5:
            return "SIGNAL"
        if pos.sessions_held >= HOLD:
            return "TIME"
        return ""
