"""Indicator correctness.

Checked against hand-computable cases rather than a reference library, so a regression
shows up as a specific wrong number instead of a vague disagreement.
"""

import numpy as np
import pytest

from helm.analysis import indicators as ind


def test_sma_matches_manual_mean():
    values = np.arange(1, 11, dtype=float)
    result = ind.sma(values, 3)
    assert np.isnan(result[:2]).all()
    assert result[2] == pytest.approx(2.0)     # (1+2+3)/3
    assert result[-1] == pytest.approx(9.0)    # (8+9+10)/3


def test_ema_of_constant_is_the_constant():
    values = np.full(60, 7.5)
    assert ind.last(ind.ema(values, 21)) == pytest.approx(7.5)


def test_ema_tracks_faster_than_sma():
    """The EMA leads mid-move, which is the property the setups depend on.

    Measured five bars into the step change: wait the full window and the SMA catches up
    completely, so the comparison only means anything while price is still moving.
    """
    values = np.concatenate([np.full(40, 10.0), np.full(5, 20.0)])
    assert ind.last(ind.ema(values, 20)) > ind.last(ind.sma(values, 20))


def test_rsi_bounds():
    rising = np.arange(1, 80, dtype=float)
    falling = rising[::-1].copy()
    assert ind.last(ind.rsi(rising)) == pytest.approx(100.0)
    assert ind.last(ind.rsi(falling)) == pytest.approx(0.0, abs=1e-6)


def test_rsi_of_flat_series_is_neutral_or_extreme_not_nan():
    flat = np.full(60, 50.0)
    assert not np.isnan(ind.last(ind.rsi(flat)))


def test_atr_of_fixed_range_equals_that_range():
    high = np.full(50, 11.0)
    low = np.full(50, 9.0)
    close = np.full(50, 10.0)
    assert ind.last(ind.atr(high, low, close)) == pytest.approx(2.0)


def test_adx_is_high_in_a_clean_trend():
    close = np.arange(1, 120, dtype=float)
    assert ind.last(ind.adx(close + 0.5, close - 0.5, close)) > 40


def test_shift_does_not_leak_the_current_bar():
    values = np.array([1.0, 2.0, 3.0, 4.0])
    shifted = ind.shift(values, 1)
    assert np.isnan(shifted[0])
    assert shifted[-1] == 3.0        # the last value now describes the previous bar


def test_rolling_max_then_shift_is_a_breakable_channel():
    """The exact composition the breakout setup relies on.

    Without the shift the channel contains the current bar's own high and can never be
    exceeded by that bar's close — the bug this test exists to prevent recurring.
    """
    high = np.array([10.0] * 20 + [12.0])
    close = np.array([9.0] * 20 + [11.0])
    channel = ind.shift(ind.rolling_max(high, 20), 1)
    assert close[-1] > channel[-1]


def test_bollinger_bandwidth_widens_with_volatility():
    calm = np.concatenate([np.full(40, 10.0), np.full(40, 10.0) + np.tile([0.01, -0.01], 20)])
    wild = np.concatenate([np.full(40, 10.0), np.full(40, 10.0) + np.tile([2.0, -2.0], 20)])
    assert ind.last(ind.bollinger(wild)[3]) > ind.last(ind.bollinger(calm)[3])


def test_percentile_rank_is_bounded():
    values = np.arange(100, dtype=float)
    assert ind.percentile_rank(values) == pytest.approx(0.99)
    assert 0.0 <= ind.percentile_rank(values[::-1].copy()) <= 1.0


def test_min_percentile_rank_remembers_recent_quiet():
    # Rising volatility: the current rank is high, but it was low a few bars back.
    values = np.concatenate([np.full(95, 1.0), np.array([1.0, 1.0, 5.0, 6.0, 7.0])])
    assert ind.min_percentile_rank(values, 100, recent=6) < ind.percentile_rank(values, 100)


def test_bullish_divergence_detects_higher_low_in_oscillator():
    price = np.concatenate([np.linspace(10, 8, 20), np.linspace(9, 7, 20)])
    oscillator = np.concatenate([np.linspace(40, 20, 20), np.linspace(35, 30, 20)])
    assert ind.bullish_divergence(price, oscillator, 40)


def test_no_divergence_when_both_make_lower_lows():
    price = np.linspace(10, 6, 40)
    oscillator = np.linspace(50, 10, 40)
    assert not ind.bullish_divergence(price, oscillator, 40)


def test_every_indicator_preserves_length():
    close = np.linspace(10, 20, 80)
    high, low = close * 1.01, close * 0.99
    for series in (
        ind.ema(close, 21), ind.sma(close, 21), ind.rsi(close), ind.atr(high, low, close),
        ind.adx(high, low, close), ind.rolling_max(high, 20), ind.shift(close, 1),
        *ind.bollinger(close),
    ):
        assert series.size == close.size
