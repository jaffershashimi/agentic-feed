class Strategy:
    """Daily-bar strategy: everything is decided at a close from data known at that close, orders fill at the next open.

    Subclasses implement `signals` and `exit_reason`. They never see the future: `ctx` only exposes bars up to and including the decision date.
    """
    name = "base"

    def signals(self, ctx):
        """Return a list of Intent for the next open."""
        raise NotImplementedError

    def exit_reason(self, pos, ctx):
        """Return a reason string to sell at the next open, or '' to keep holding. pos.sessions_held counts the entry day as 1."""
        raise NotImplementedError


class Context:
    """The view of the market at one close. `data[sym]` is a dict of parallel lists; `idx[sym]` maps a date to its position; `i(sym)` is today's index (or None if the symbol has no bar today)."""

    def __init__(self, data, idx, inds, universe, date, stress):
        self.data, self.idx, self.inds, self.universe, self.date, self.stress = data, idx, inds, universe, date, stress

    def i(self, sym):
        return self.idx[sym].get(self.date)

    def close(self, sym):
        i = self.i(sym)
        return None if i is None else self.data[sym]["c"][i]
