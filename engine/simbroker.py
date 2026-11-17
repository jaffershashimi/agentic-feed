"""Execution model for backtests. It is deliberately the same as what the paper account does: market orders queued after a close fill at the next open (plus slippage),
the stop is a broker-side order that fills at the stop price, or at the open if the day opens below it."""
from engine.types import Position, Trade


class SimBroker:
    def __init__(self, slip=0.0005, entry_gap=0.0, slip_out=None, exit_gap=0.0):
        self.slip = slip                                 # entry cost vs the reference price, per side
        self.slip_out = slip if slip_out is None else slip_out
        self.entry_gap = entry_gap                       # extra adverse move on entries (price drift between the reference time and the fill)
        self.exit_gap = exit_gap                         # extra move on exits; negative means the later price was better for a seller
        self.positions = []
        self.trades = []

    def buy(self, intent, qty, date, open_px):
        px = open_px * (1 + self.slip + self.entry_gap)
        self.positions.append(Position(intent.strategy, intent.symbol, qty, date, px, round(intent.signal_close * intent.stop_fraction, 2), 1))

    def _close(self, pos, date, px, how):
        net = px * (1 - self.slip_out - self.exit_gap)
        pnl = (net - pos.entry_price) * pos.qty
        self.trades.append(Trade(pos.strategy, pos.symbol, pos.entry_date, date, pos.entry_price, net, pos.qty, how, pnl, net / pos.entry_price - 1.0))
        self.positions.remove(pos)

    def sell_at_open(self, pos, date, open_px, how):
        self._close(pos, date, open_px, how)

    def check_stops(self, date, bar_of):
        """bar_of(sym) -> (open, high, low, close) for today or None. A stop that is touched fills at the stop, or at the open when the open is already below it."""
        for pos in list(self.positions):
            b = bar_of(pos.symbol)
            if b and b[2] <= pos.stop_price:
                self._close(pos, date, min(pos.stop_price, b[0]), "STOP")
