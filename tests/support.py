"""A market the tests can steer, and a clock they can move.

The synthetic market is a fair rehearsal for the plumbing, but the rule needs to be tested
against closes chosen on purpose: this coin above its average, that one below, a day that has
not closed yet. `TrendMarket` serves exactly the daily candles a test sets, dated relative to
the test's clock, and fills the simulated broker at the latest close.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from helm import doctrine
from helm.config import Config
from helm.engine import Engine
from helm.models import Candles, Instrument, Quote

DAY = 86_400_000
FLAT = 100.0
# With 199 closes at 100, one more at 110 sits above the 200-day average and one at 90 sits
# below it — whatever came before.
UP, DOWN = 110.0, 90.0


class Clock:
    def __init__(self) -> None:
        # One minute after midnight UTC: the moment the engine reads a fresh daily close.
        self.ms = int(datetime(2026, 10, 27, 0, 1, tzinfo=timezone.utc).timestamp() * 1000)

    def now(self) -> int:
        return self.ms

    def day_start(self, now=None) -> datetime:
        moment = datetime.fromtimestamp(self.ms / 1000, timezone.utc)
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    def advance(self, ms: int) -> None:
        self.ms += ms


class TrendMarket:
    name = "trend-test"

    def __init__(self, clock: Clock, step: float = 0.0) -> None:
        self.clock = clock
        self.step = step
        self.closes = {symbol: [FLAT] * 250 for symbol in doctrine.SYMBOLS}
        self.price = {symbol: FLAT for symbol in doctrine.SYMBOLS}
        self.unpublished: set[str] = set()     # coins whose latest daily candle is not out yet
        self.failing: set[str] = set()         # coins whose candle request raises

    def set_close(self, symbol: str, close: float) -> None:
        """Make `close` the latest closed day, and the price now."""
        self.closes[symbol][-1] = close
        self.price[symbol] = close

    def new_day(self, closes: dict[str, float] | None = None) -> None:
        """Move the clock to the next midnight; every coin closes where it was unless told."""
        for symbol in doctrine.SYMBOLS:
            close = (closes or {}).get(symbol, self.closes[symbol][-1])
            self.closes[symbol].append(close)
            self.price[symbol] = close
        self.clock.advance(DAY)

    def instruments(self) -> dict[str, Instrument]:
        return {
            symbol: Instrument(symbol=symbol, base=symbol[: -len(doctrine.QUOTE)],
                               quote=doctrine.QUOTE, tick_size=0.0, step_size=self.step,
                               min_notional=5.0)
            for symbol in doctrine.SYMBOLS
        }

    def quotes(self) -> dict[str, Quote]:
        return {symbol: Quote(symbol=symbol, price=price) for symbol, price in self.price.items()}

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        assert timeframe == doctrine.TREND_TF
        if symbol in self.failing:
            from helm.venues.base import VenueError

            raise VenueError("candles unavailable")
        closes = self.closes[symbol]
        if symbol in self.unpublished:
            closes = closes[:-1]
        last = int(self.clock.day_start().timestamp() * 1000) - DAY
        if symbol in self.unpublished:
            last -= DAY
        closes = np.array(closes[-limit:], dtype=float)
        times = np.array([last - (len(closes) - 1 - i) * DAY for i in range(len(closes))], dtype=float)
        return Candles(symbol=symbol, timeframe=timeframe, open_time=times, open=closes,
                       high=closes, low=closes, close=closes, volume=np.ones_like(closes))


def build_engine(db_path: str, market: TrendMarket, equity: float = 100.0) -> Engine:
    config = Config()
    config.db_path = db_path
    config.venue = "synthetic"         # builds the simulated broker; the market is swapped in
    config.telegram_token = ""
    config.starting_equity = equity
    engine = Engine(config)
    engine.market = engine.broker.market = market
    engine.instruments = {}
    engine.refresh_prices()
    return engine
