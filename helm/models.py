"""Data shapes shared across the engine.

Plain dataclasses, no ORM. Everything that reaches the database or the UI goes through
`to_dict()` so there is one serialisation path and the UI never sees a numpy scalar.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

Side = Literal["BUY", "SELL"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> int:
    return int(time.time() * 1000)


def clean(value: Any) -> Any:
    """Make a value safe for JSON.

    Numpy scalars, NaN and infinity all serialise badly or not at all, and a single NaN
    leaking into the UI payload takes the whole dashboard down. Everything numeric passes
    through here on its way out.
    """
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 10)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


@dataclass(slots=True)
class Candles:
    """OHLCV for one symbol on one timeframe, oldest first.

    Columns are separate arrays rather than a dataframe: the indicator layer is pure numpy
    and this keeps the whole hot path allocation-free and dependency-light.
    """

    symbol: str
    timeframe: str
    open_time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return int(self.close.size)

    @property
    def last(self) -> float:
        return float(self.close[-1])

    def tail(self, n: int) -> "Candles":
        return Candles(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open_time=self.open_time[-n:],
            open=self.open[-n:],
            high=self.high[-n:],
            low=self.low[-n:],
            close=self.close[-n:],
            volume=self.volume[-n:],
        )


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float
    quote_volume_24h: float = 0.0
    change_24h_pct: float = 0.0


@dataclass(slots=True)
class Instrument:
    """Exchange trading rules for one symbol.

    Tick/step sizes and minimum notional are not cosmetic — an order that violates them is
    rejected, and at this account size we live right on top of the minimum.
    """

    symbol: str
    base: str
    quote: str
    tick_size: float
    step_size: float
    min_notional: float
    min_qty: float = 0.0

    def round_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        return math.floor(price / self.tick_size) * self.tick_size

    def round_qty(self, qty: float) -> float:
        if self.step_size <= 0:
            return qty
        # Always floor: rounding a quantity up can exceed the balance and get rejected.
        return math.floor(qty / self.step_size) * self.step_size


@dataclass(slots=True)
class Position:
    """A coin the rule holds. The exchange is the source of truth for `qty`; everything
    else is the engine's record of how it came to hold it."""

    id: str
    symbol: str
    venue: str
    qty: float
    entry_price: float
    initial_qty: float
    opened_at: int
    cost: float                 # quote spent on the purchase, fee included
    entry_close: float = 0.0    # the daily close that turned the coin on
    entry_average: float = 0.0  # and the average it closed above
    fees_paid: float = 0.0
    # Kept separate from `fees_paid` (which accumulates exit fees too) so that attributing
    # the entry cost across partial exits stays correct however many legs there are.
    entry_fee: float = 0.0
    realized_pnl: float = 0.0
    last_note: str = ""

    def cost_held(self) -> float:
        """The share of the purchase cost still in the account."""
        return self.cost * (self.qty / self.initial_qty) if self.initial_qty else 0.0

    def unrealized(self, price: float) -> float:
        """What selling now would add, before the sale's own fee."""
        return self.qty * price - self.cost_held()

    def to_dict(self, price: float | None = None) -> dict[str, Any]:
        mark = price if price is not None else self.entry_price
        held = self.cost_held()
        return clean(
            {
                "id": self.id,
                "symbol": self.symbol,
                "venue": self.venue,
                "qty": self.qty,
                "entry_price": self.entry_price,
                "price": mark,
                "value": self.qty * mark,
                "cost": held,
                "opened_at": self.opened_at,
                "entry_close": self.entry_close,
                "entry_average": self.entry_average,
                "unrealized": self.unrealized(mark),
                "unrealized_pct": (self.unrealized(mark) / held * 100) if held else 0.0,
                "realized_pnl": self.realized_pnl,
                "fees_paid": self.fees_paid,
                "last_note": self.last_note,
            }
        )


@dataclass(slots=True)
class Fill:
    """What the broker actually did, as opposed to what we asked for."""

    order_id: str
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float
    ts: int = field(default_factory=now_ms)
    status: str = "FILLED"
    raw: dict[str, Any] = field(default_factory=dict)
