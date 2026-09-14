"""Data shapes shared across the engine.

Plain dataclasses, no ORM. Everything that reaches the database or the UI goes through
`to_dict()` so there is one serialisation path and the UI never sees a numpy scalar.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

import numpy as np

Side = Literal["BUY", "SELL"]
Regime = Literal["TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", "UNKNOWN"]
SetupKind = Literal["TREND_PULLBACK", "SQUEEZE_BREAKOUT", "CAPITULATION_REVERSAL"]


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
class Analysis:
    """Everything the analysis layer knows about one symbol at one moment."""

    symbol: str
    price: float
    regime: Regime
    indicators: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return clean(asdict(self))


@dataclass(slots=True)
class Candidate:
    """A setup that passed deterministic detection and wants a decision."""

    symbol: str
    setup: SetupKind
    score: float
    factors: dict[str, float]
    price: float
    stop: float
    target: float
    regime: Regime
    analysis: Analysis
    rationale: list[str] = field(default_factory=list)

    @property
    def stop_distance(self) -> float:
        return abs(self.price - self.stop) / self.price if self.price else 0.0

    @property
    def reward_risk(self) -> float:
        risk = abs(self.price - self.stop)
        return abs(self.target - self.price) / risk if risk > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return clean(
            {
                "symbol": self.symbol,
                "setup": self.setup,
                "score": self.score,
                "factors": self.factors,
                "price": self.price,
                "stop": self.stop,
                "target": self.target,
                "regime": self.regime,
                "stop_distance": self.stop_distance,
                "reward_risk": self.reward_risk,
                "rationale": self.rationale,
                "indicators": self.analysis.indicators,
            }
        )


@dataclass(slots=True)
class Verdict:
    """The decision layer's answer on a candidate."""

    go: bool
    conviction: int
    size_multiplier: float
    stop_override: float | None
    thesis: str
    invalidation: str
    source: str = "claude"          # or "fallback" when the model was unreachable
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return clean(
            {
                "go": self.go,
                "conviction": self.conviction,
                "size_multiplier": self.size_multiplier,
                "stop_override": self.stop_override,
                "thesis": self.thesis,
                "invalidation": self.invalidation,
                "source": self.source,
            }
        )


@dataclass(slots=True)
class Position:
    """A live position. The exchange is the source of truth for `qty`; everything else is
    the engine's plan for managing it."""

    id: str
    symbol: str
    venue: str
    qty: float
    entry_price: float
    stop: float
    target: float
    initial_stop: float
    initial_qty: float
    risk_amount: float
    opened_at: int
    setup: SetupKind
    score: float
    conviction: int
    thesis: str = ""
    invalidation: str = ""
    scaled_out: bool = False
    breakeven_set: bool = False
    highest_close: float = 0.0
    fees_paid: float = 0.0
    # Kept separate from `fees_paid` (which accumulates exit fees too) so that attributing
    # the entry cost across partial exits stays correct however many legs there are.
    entry_fee: float = 0.0
    realized_pnl: float = 0.0
    regime_break_closes: int = 0
    last_note: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price

    @property
    def r_unit(self) -> float:
        """One R in price terms — the distance from entry to the *original* stop.

        Deliberately the initial stop: once the stop trails, measuring R against the new
        stop would make every trade look like it's at +1R forever.
        """
        return abs(self.entry_price - self.initial_stop)

    def r_multiple(self, price: float) -> float:
        unit = self.r_unit
        if unit <= 0:
            return 0.0
        return (price - self.entry_price) / unit

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.qty

    def to_dict(self, price: float | None = None) -> dict[str, Any]:
        mark = price if price is not None else self.entry_price
        return clean(
            {
                "id": self.id,
                "symbol": self.symbol,
                "venue": self.venue,
                "qty": self.qty,
                "entry_price": self.entry_price,
                "price": mark,
                "stop": self.stop,
                "target": self.target,
                "initial_stop": self.initial_stop,
                "notional": self.qty * mark,
                "risk_amount": self.risk_amount,
                "opened_at": self.opened_at,
                "setup": self.setup,
                "score": self.score,
                "conviction": self.conviction,
                "thesis": self.thesis,
                "invalidation": self.invalidation,
                "scaled_out": self.scaled_out,
                "breakeven_set": self.breakeven_set,
                "unrealized": self.unrealized(mark),
                "unrealized_pct": (mark / self.entry_price - 1) * 100 if self.entry_price else 0.0,
                "r_multiple": self.r_multiple(mark),
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
