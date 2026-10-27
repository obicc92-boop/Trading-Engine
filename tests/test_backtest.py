"""The backtest's market must only ever show bars that had closed by the simulated time.

A replay that lets a forming bar leak into a decision trades on information the live engine
never has. Its results look excellent and mean nothing, which makes the leak worse than
having no backtest at all — so the boundary is pinned here.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import soak  # noqa: E402
from backtest import MINUTE, HistoricalMarket  # noqa: E402
from helm.models import Candles  # noqa: E402
from helm.venues.base import VenueError  # noqa: E402

START = 1_700_000_000_000


def _bars(timeframe: str, minutes: int, count: int) -> Candles:
    opens = START + np.arange(count, dtype=float) * minutes * MINUTE
    close = 100.0 + np.arange(count, dtype=float)       # each close encodes its bar's index
    return Candles(symbol="BTCEUR", timeframe=timeframe, open_time=opens, open=close,
                   high=close + 0.5, low=close - 0.5, close=close, volume=np.ones(count))


@pytest.fixture
def market():
    frames = {"1m": _bars("1m", 1, 3000), "15m": _bars("15m", 15, 200)}
    yield HistoricalMarket({"BTCEUR": frames}, {})
    soak.CLOCK[0] = 0


def test_the_forming_bar_is_never_visible(market):
    # 40 minutes in: two 15m bars have closed and the third is still forming.
    soak.CLOCK[0] = START + 40 * MINUTE
    candles = market.candles("BTCEUR", "15m", 50)
    assert len(candles) == 2
    assert candles.open_time[-1] + 15 * MINUTE <= soak.CLOCK[0]


def test_a_bar_appears_exactly_when_it_closes(market):
    soak.CLOCK[0] = START + 30 * MINUTE - 1
    assert len(market.candles("BTCEUR", "15m", 50)) == 1
    soak.CLOCK[0] = START + 30 * MINUTE
    assert len(market.candles("BTCEUR", "15m", 50)) == 2


def test_the_price_is_the_last_closed_minute(market):
    soak.CLOCK[0] = START + 90 * MINUTE + 30_000      # half way through the 91st minute
    assert market.quotes()["BTCEUR"].price == 100.0 + 89


def test_limit_returns_the_most_recent_closed_bars(market):
    soak.CLOCK[0] = START + 15 * 15 * MINUTE
    candles = market.candles("BTCEUR", "15m", 5)
    assert list(candles.close) == [110.0, 111.0, 112.0, 113.0, 114.0]


def test_nothing_is_visible_before_the_first_close(market):
    soak.CLOCK[0] = START
    with pytest.raises(VenueError):
        market.candles("BTCEUR", "15m", 10)
    assert market.quotes() == {}


def test_a_window_inside_a_cached_download_never_touches_the_network(tmp_path, monkeypatch):
    import backtest

    held = _bars("15m", 15, 100)
    stacked = np.column_stack([held.open_time, held.open, held.high, held.low, held.close, held.volume])
    end = START + 100 * 15 * MINUTE
    np.save(tmp_path / f"BTCEUR_15m_{START}_{end}.npy", stacked)
    monkeypatch.setattr(backtest, "CACHE", tmp_path)

    def offline(*args, **kwargs):
        raise AssertionError("went to the network for history it already had")

    monkeypatch.setattr(backtest.httpx, "Client", offline)
    inner = backtest.klines("BTCEUR", "15m", START + 10 * 15 * MINUTE, START + 20 * 15 * MINUTE)
    assert list(inner.close) == [100.0 + i for i in range(10, 20)]
