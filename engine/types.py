from dataclasses import dataclass, field


@dataclass(frozen=True)
class Intent:
    """What a strategy wants to do, decided at a close, to be filled at the next open."""
    strategy: str
    symbol: str
    notional: float          # target dollars
    stop_fraction: float     # stop = signal close * stop_fraction (broker-side, attached to the entry)
    rank: tuple              # lower sorts first; the portfolio takes the best-ranked intents that fit
    signal_close: float
    reason: str = ""


@dataclass
class Position:
    strategy: str
    symbol: str
    qty: int
    entry_date: str
    entry_price: float
    stop_price: float
    sessions_held: int = 0
    pending_exit: str = ""   # reason, set at a close; the sell fills at the next open


@dataclass
class Trade:
    strategy: str
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    qty: int
    how: str                 # STOP, SIGNAL, TIME
    pnl: float
    ret: float
