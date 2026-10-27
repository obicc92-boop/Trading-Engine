"""The rule — doctrine section 2 and 3 — as pure functions.

No I/O and no clock: closed daily candles go in, a verdict per coin comes out, and the
purchases that verdict implies are sized from numbers handed in. Everything that touches an
exchange lives in the engine, so the part that decides can be tested exhaustively.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import doctrine
from .models import Candles, clean


@dataclass(slots=True)
class Signal:
    """One coin against its average, as of one closed day."""

    symbol: str
    day: int              # open time, unix ms, of the daily candle the verdict rests on
    close: float
    average: float

    @property
    def on(self) -> bool:
        return self.close > self.average

    @property
    def distance(self) -> float:
        """How far the close sits from the average, as a fraction of the average."""
        return self.close / self.average - 1 if self.average else 0.0

    def to_dict(self) -> dict:
        return clean({"symbol": self.symbol, "day": self.day, "close": self.close,
                      "average": self.average, "distance": self.distance, "on": self.on})


@dataclass(slots=True)
class Entry:
    symbol: str
    amount: float         # quote to spend; zero when skipped
    skipped: str = ""     # why nothing was bought, when nothing was


def read(candles: Candles) -> Signal | None:
    """The verdict on the last closed day, or None with too little history to have one.

    The candles must be closed days only, which every market source here guarantees. The
    average includes the day being judged: the close and the average it is compared with
    describe the same moment.
    """
    if len(candles) < doctrine.SMA_DAYS:
        return None
    closes = candles.close[-doctrine.SMA_DAYS:]
    return Signal(symbol=candles.symbol, day=int(candles.open_time[-1]),
                  close=float(closes[-1]), average=float(np.mean(closes)))


def exits(signals: dict[str, Signal], held: set[str]) -> list[str]:
    """Coins held against a broken trend. A coin with no verdict is left alone."""
    return [symbol for symbol in doctrine.SYMBOLS
            if symbol in held and symbol in signals and not signals[symbol].on]


def entries(
    signals: dict[str, Signal],
    held: set[str],
    equity: float,
    cash: float,
    minimums: dict[str, float] | None = None,
) -> list[Entry]:
    """Purchases for every coin that is on and not already held, in doctrine order.

    `equity` is the account's value at the decision, before anything is sold — the figure
    the rule was tested with. `cash` is what is free once the day's sales have settled.
    Each coin gets a quarter of that equity or whatever cash is left, whichever is less, and
    is skipped below the minimum order. Never an addition to a coin already held.
    """
    out: list[Entry] = []
    for symbol in doctrine.SYMBOLS:
        signal = signals.get(symbol)
        if signal is None or not signal.on or symbol in held:
            continue
        # Floored to the cent: rounding up could ask for more than the account holds.
        amount = math.floor(min(cash, equity * doctrine.ENTRY_FRACTION) * 100) / 100
        minimum = max(doctrine.MIN_NOTIONAL, (minimums or {}).get(symbol, 0.0))
        if amount < minimum:
            out.append(Entry(symbol, 0.0, skipped=f"{amount:.2f} is below the {minimum:.2f} minimum order"))
            continue
        out.append(Entry(symbol, amount))
        cash -= amount
    return out
