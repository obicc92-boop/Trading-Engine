"""Dry-run venue: a simulated broker, and a synthetic market to drive it.

Two distinct jobs.

`SimulatedBroker` wraps any `MarketSource` and fills orders against real prices with a fee
and a slippage haircut. It runs the *identical* engine code path as live trading — same
sizing, same rails, same exits — so a dry run is a rehearsal rather than a different
program. The only substituted component is the one that moves money.

`SyntheticMarket` generates plausible OHLCV offline. It exists so the system can be tested
deterministically and so the dashboard has something to show before any keys are configured.
It is never used when a real market source is available, and it is never a basis for any
claim about how the strategy performs.
"""

from __future__ import annotations

import hashlib
import logging
import math

import numpy as np

from ..models import Candles, Fill, Instrument, Quote, new_id, now_ms
from .base import VenueError

log = logging.getLogger("helm.sim")

TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


class SimulatedBroker:
    """Fills at the current mark, minus fees and a pessimistic slippage assumption."""

    name = "simulated"
    live = False

    def __init__(self, market, quote_asset: str, starting_cash: float, fee: float = 0.001) -> None:
        self.market = market
        self.quote_asset = quote_asset
        self.fee = fee
        self.slippage = 0.0005  # 5bps against us on every fill, always
        self._balances: dict[str, float] = {quote_asset: starting_cash}
        self._marks: dict[str, float] = {}

    def set_marks(self, marks: dict[str, float]) -> None:
        """Fill against the same prices the engine made its decision on.

        Without this the simulation decides at one price and fills at another it fetched
        independently, which quietly invents P&L that has nothing to do with the strategy.
        Live trading has no equivalent problem: there, the exchange's price is the only one.
        """
        self._marks = dict(marks)

    def restore(self, balances: dict[str, float]) -> None:
        """Reload simulated balances after a restart so dry runs survive a redeploy."""
        if balances:
            self._balances = dict(balances)

    def snapshot(self) -> dict[str, float]:
        return dict(self._balances)

    def balances(self) -> dict[str, float]:
        return dict(self._balances)

    def _mark(self, symbol: str) -> float:
        override = self._marks.get(symbol)
        if override and override > 0:
            return override
        quote = self.market.quotes().get(symbol)
        if quote is None or quote.price <= 0:
            raise VenueError(f"no price for {symbol}")
        return quote.price

    def _base_of(self, symbol: str) -> str:
        instrument = self.market.instruments().get(symbol)
        return instrument.base if instrument else symbol.replace(self.quote_asset, "")

    def buy(self, symbol: str, quote_amount: float) -> Fill:
        cash = self._balances.get(self.quote_asset, 0.0)
        if quote_amount > cash:
            raise VenueError(f"insufficient cash: need {quote_amount:.2f}, have {cash:.2f}")
        price = self._mark(symbol) * (1 + self.slippage)
        fee = quote_amount * self.fee
        qty = (quote_amount - fee) / price
        base = self._base_of(symbol)
        self._balances[self.quote_asset] = cash - quote_amount
        self._balances[base] = self._balances.get(base, 0.0) + qty
        return Fill(
            order_id=new_id("sim"), symbol=symbol, side="BUY", qty=qty, price=price, fee=fee,
            raw={"simulated": True},
        )

    def sell(self, symbol: str, qty: float) -> Fill:
        base = self._base_of(symbol)
        held = self._balances.get(base, 0.0)
        qty = min(qty, held)
        if qty <= 0:
            raise VenueError(f"nothing to sell in {symbol}")
        price = self._mark(symbol) * (1 - self.slippage)
        gross = qty * price
        fee = gross * self.fee
        self._balances[base] = held - qty
        self._balances[self.quote_asset] = self._balances.get(self.quote_asset, 0.0) + gross - fee
        return Fill(
            order_id=new_id("sim"), symbol=symbol, side="SELL", qty=qty, price=price, fee=fee,
            raw={"simulated": True},
        )


class SyntheticMarket:
    """Offline OHLCV generated as a pure function of wall-clock time.

    The important property is cross-timeframe consistency: the 15m, 1h and 4h series must
    describe the *same* price history, or the multi-timeframe analysis is reading three
    different markets and every regime call is meaningless. So rather than simulating each
    timeframe separately, one deterministic function maps a timestamp to a log-price, and
    every timeframe simply samples it at its own bar boundaries.

    That function is a sum of sinusoids across scales (weeks down to minutes) plus hashed
    pseudo-noise — enough structure for trends, ranges and volatility clustering to appear,
    while staying bounded and exactly reproducible. It is a plausible-looking fake, not a
    market model, and no claim about strategy performance should ever rest on it.
    """

    name = "synthetic"

    UNIVERSE = {
        "BTCEUR": (62_000.0, 0.55, 900_000_000.0),
        "ETHEUR": (3_100.0, 0.75, 420_000_000.0),
        "SOLEUR": (142.0, 1.15, 180_000_000.0),
        "ADAEUR": (0.58, 1.05, 90_000_000.0),
        "LINKEUR": (16.4, 1.00, 75_000_000.0),
        "AVAXEUR": (28.7, 1.20, 68_000_000.0),
        "DOTEUR": (6.2, 0.95, 55_000_000.0),
        "XRPEUR": (0.52, 0.90, 260_000_000.0),
    }

    # (period in minutes, relative amplitude). Long waves set the trend, short ones the
    # texture; the 1/f-ish amplitude decay is what makes the result look market-like.
    _SCALES = (
        (20_160, 1.00),   # ~2 weeks
        (7_200, 0.55),    # ~5 days
        (2_880, 0.34),    # 2 days
        (720, 0.20),      # 12 hours
        (240, 0.12),      # 4 hours
        (60, 0.07),       # 1 hour
        (15, 0.035),      # 15 minutes
    )

    def __init__(self, quote_asset: str = "EUR") -> None:
        self.quote_asset = quote_asset

    def instruments(self) -> dict[str, Instrument]:
        return {
            symbol: Instrument(
                symbol=symbol,
                base=symbol[: -len(self.quote_asset)],
                quote=self.quote_asset,
                tick_size=0.0,
                step_size=0.0,
                min_notional=5.0,
            )
            for symbol in self.UNIVERSE
        }

    def _phases(self, symbol: str) -> np.ndarray:
        """Per-symbol phase offsets, so assets are correlated in shape but not in timing."""
        digest = hashlib.sha256(symbol.encode()).digest()
        return np.array(
            [digest[i] / 255.0 * 2 * math.pi for i in range(len(self._SCALES))]
        )

    def _log_price(self, symbol: str, minutes: np.ndarray) -> np.ndarray:
        """Log-price deviation at absolute times, in minutes since epoch."""
        _, amplitude, _ = self.UNIVERSE[symbol]
        phases = self._phases(symbol)
        total = np.zeros_like(minutes, dtype=float)
        for (period, weight), phase in zip(self._SCALES, phases):
            total += weight * np.sin(2 * math.pi * minutes / period + phase)
        # Deterministic fine-grain jitter: hash-based, so it is reproducible without
        # carrying any state between calls.
        seed = int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:4], "big")
        jitter = np.sin(minutes * 12.9898 + seed % 1000) * 43758.5453
        total += (jitter - np.floor(jitter) - 0.5) * 0.06
        return total * amplitude * 0.18

    def _price_at(self, symbol: str, minutes: np.ndarray) -> np.ndarray:
        base, _, _ = self.UNIVERSE[symbol]
        return base * np.exp(self._log_price(symbol, minutes))

    def quotes(self) -> dict[str, Quote]:
        now_min = now_ms() / 60_000.0
        out: dict[str, Quote] = {}
        for symbol, (_, _, volume) in self.UNIVERSE.items():
            points = self._price_at(symbol, np.array([now_min, now_min - 1440.0]))
            price, day_ago = float(points[0]), float(points[1])
            out[symbol] = Quote(
                symbol=symbol,
                price=price,
                quote_volume_24h=volume,
                change_24h_pct=(price / day_ago - 1) * 100 if day_ago else 0.0,
            )
        return out

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        if symbol not in self.UNIVERSE:
            raise VenueError(f"unknown synthetic symbol {symbol}")
        minutes = TF_MINUTES.get(timeframe)
        if minutes is None:
            raise VenueError(f"unsupported timeframe {timeframe}")

        bar_ms = minutes * 60_000
        last_closed = int(now_ms() // bar_ms) - 1   # closed bars only, as with a real feed
        starts = np.array([(last_closed - i) * bar_ms for i in range(limit - 1, -1, -1)], dtype=float)

        # Sample within each bar so the high and low are real extremes of the same path the
        # neighbouring timeframes see, rather than invented wicks.
        steps = 8
        offsets = np.linspace(0.0, float(minutes), steps)
        grid = (starts[:, None] / 60_000.0) + offsets[None, :]
        prices = self._price_at(symbol, grid)

        opens = prices[:, 0]
        closes = prices[:, -1]
        highs = prices.max(axis=1)
        lows = prices.min(axis=1)

        # Volume tracks absolute move, with a floor so quiet bars are quiet, not empty.
        moves = np.abs(closes / np.maximum(opens, 1e-12) - 1.0)
        base_volume = self.UNIVERSE[symbol][2] / 1440.0 * minutes
        volumes = base_volume * (0.55 + moves * 90.0)

        return Candles(
            symbol=symbol, timeframe=timeframe, open_time=starts, open=opens,
            high=highs, low=lows, close=closes, volume=volumes,
        )

    def listing_age_days(self, symbol: str) -> float:
        return 9999.0
