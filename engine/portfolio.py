import math

from engine.types import Position


class Portfolio:
    """Turns a strategy's intents into the orders that fit the rules. Deterministic: same intents and positions give the same orders, in the same order."""

    def __init__(self, max_positions=10, max_per_symbol=1):
        self.max_positions = max_positions
        self.max_per_symbol = max_per_symbol

    def select(self, intents, positions, leaving=()):
        """Returns (orders, skipped). `leaving` are symbols whose exit is already queued: they free a slot at the next open."""
        held = {p.symbol for p in positions if p.symbol not in leaving}
        slots = max(0, self.max_positions - len(held))
        orders, skipped = [], []
        for it in sorted(intents, key=lambda x: x.rank):
            if it.symbol in {p.symbol for p in positions}:
                skipped.append((it.symbol, "ALREADY_HELD"))
            elif len(orders) >= slots:
                skipped.append((it.symbol, "NO_FREE_SLOT"))
            else:
                qty = math.floor(it.notional / it.signal_close)
                if qty < 1:
                    skipped.append((it.symbol, "ONE_SHARE_TOO_EXPENSIVE"))
                else:
                    orders.append((it, qty))
        return orders, skipped
