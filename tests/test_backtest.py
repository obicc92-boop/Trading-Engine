"""The backtest's market must only ever show days that had closed by the simulated time.

A replay that lets a forming candle leak into a decision trades on information the live
engine never has. Its results look excellent and mean nothing, which makes the leak worse
than having no backtest at all — so the boundary is pinned here.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import soak  # noqa: E402
from backtest import DAY, MINUTE, HistoricalMarket  # noqa: E402
from helm.models import Candles  # noqa: E402
from helm.venues.base import VenueError  # noqa: E402

START = 1_700_006_400_000          # a midnight UTC


def _days(count: int) -> Candles:
    opens = START + np.arange(count, dtype=float) * DAY
    close = 100.0 + np.arange(count, dtype=float)       # each close encodes its day's index
    return Candles(symbol="BTCEUR", timeframe="1d", open_time=opens, open=close - 0.5,
                   high=close + 1, low=close - 1, close=close, volume=np.ones(count))


@pytest.fixture
def market():
    yield HistoricalMarket({"BTCEUR": _days(300)}, {})
    soak.CLOCK[0] = 0


def test_the_forming_day_is_never_visible(market):
    soak.CLOCK[0] = START + 2 * DAY + 12 * 60 * MINUTE       # midday on the third day
    candles = market.candles("BTCEUR", "1d", 50)
    assert len(candles) == 2
    assert candles.open_time[-1] + DAY <= soak.CLOCK[0]


def test_a_day_appears_exactly_when_it_closes(market):
    soak.CLOCK[0] = START + 2 * DAY - 1
    assert len(market.candles("BTCEUR", "1d", 50)) == 1
    soak.CLOCK[0] = START + 2 * DAY
    assert len(market.candles("BTCEUR", "1d", 50)) == 2


def test_the_price_is_the_open_of_the_day_in_progress(market):
    """The moment the engine acts is just after midnight: the new day's opening price."""
    soak.CLOCK[0] = START + 5 * DAY + MINUTE
    assert market.quotes()["BTCEUR"].price == 105.0 - 0.5


def test_after_the_record_ends_the_price_is_the_last_close(market):
    soak.CLOCK[0] = START + 300 * DAY
    assert market.quotes()["BTCEUR"].price == 399.0


def test_limit_returns_the_most_recent_closed_days(market):
    soak.CLOCK[0] = START + 15 * DAY + MINUTE
    candles = market.candles("BTCEUR", "1d", 5)
    assert list(candles.close) == [110.0, 111.0, 112.0, 113.0, 114.0]


def test_nothing_is_visible_before_the_first_close(market):
    soak.CLOCK[0] = START
    with pytest.raises(VenueError):
        market.candles("BTCEUR", "1d", 10)
    assert market.quotes() == {}


def test_a_window_inside_a_cached_download_never_touches_the_network(tmp_path, monkeypatch):
    import backtest

    held = _days(100)
    stacked = np.column_stack([held.open_time, held.open, held.high, held.low, held.close, held.volume])
    end = START + 100 * DAY
    np.save(tmp_path / f"BTCEUR_1d_{START}_{end}.npy", stacked)
    monkeypatch.setattr(backtest, "CACHE", tmp_path)

    def offline(*args, **kwargs):
        raise AssertionError("went to the network for history it already had")

    monkeypatch.setattr(backtest.httpx, "Client", offline)
    inner = backtest.klines("BTCEUR", "1d", START + 10 * DAY, START + 20 * DAY)
    assert list(inner.close) == [100.0 + i for i in range(10, 20)]
