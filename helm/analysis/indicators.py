"""Indicators, in pure numpy.

Every function takes and returns full-length arrays aligned to the input, with `nan` in the
warm-up region rather than a truncated array. Alignment bugs — where an indicator is
silently shifted by one bar against price — are the quietest and most expensive class of
error in this kind of system, so the shapes never change.
"""

from __future__ import annotations

import numpy as np


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average, seeded with an SMA of the first `period` values."""
    out = np.full(values.size, np.nan)
    if values.size < period or period <= 0:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = values[:period].mean()
    for i in range(period, values.size):
        out[i] = values[i] * alpha + out[i - 1] * (1 - alpha)
    return out


def sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.size, np.nan)
    if values.size < period or period <= 0:
        return out
    cumulative = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1 :] = (cumulative[period:] - cumulative[:-period]) / period
    return out


def rolling_max(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.size, np.nan)
    for i in range(period - 1, values.size):
        out[i] = values[i - period + 1 : i + 1].max()
    return out


def rolling_min(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.size, np.nan)
    for i in range(period - 1, values.size):
        out[i] = values[i - period + 1 : i + 1].min()
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI."""
    out = np.full(close.size, np.nan)
    if close.size <= period:
        return out
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, close.size):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.concatenate([[close[0]], close[:-1]])
    return np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
    )


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder-smoothed average true range — the system's unit of 'normal movement'."""
    tr = true_range(high, low, close)
    out = np.full(close.size, np.nan)
    if close.size < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, close.size):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Trend *strength*, direction-agnostic. Used to separate trends from drift."""
    size = close.size
    out = np.full(size, np.nan)
    if size < period * 2 + 1:
        return out

    up_move = np.diff(high)
    down_move = -np.diff(low)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(high, low, close)[1:]

    smooth_tr = tr[:period].sum()
    smooth_plus = plus_dm[:period].sum()
    smooth_minus = minus_dm[:period].sum()

    dx_values: list[float] = []
    for i in range(period, tr.size):
        smooth_tr = smooth_tr - smooth_tr / period + tr[i]
        smooth_plus = smooth_plus - smooth_plus / period + plus_dm[i]
        smooth_minus = smooth_minus - smooth_minus / period + minus_dm[i]
        if smooth_tr <= 0:
            continue
        plus_di = 100.0 * smooth_plus / smooth_tr
        minus_di = 100.0 * smooth_minus / smooth_tr
        denominator = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / denominator if denominator > 0 else 0.0
        dx_values.append(dx)
        index = i + 1
        if len(dx_values) == period:
            out[index] = float(np.mean(dx_values))
        elif len(dx_values) > period:
            out[index] = (out[index - 1] * (period - 1) + dx) / period
    return out


def bollinger(close: np.ndarray, period: int = 20, mult: float = 2.0):
    """Returns (upper, middle, lower, bandwidth) — bandwidth as a fraction of the middle."""
    middle = sma(close, period)
    std = np.full(close.size, np.nan)
    for i in range(period - 1, close.size):
        std[i] = close[i - period + 1 : i + 1].std()
    upper = middle + mult * std
    lower = middle - mult * std
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = np.where(middle > 0, (upper - lower) / middle, np.nan)
    return upper, middle, lower, bandwidth


def shift(values: np.ndarray, periods: int = 1) -> np.ndarray:
    """Move a series forward in time, padding with nan.

    The way to ask "what did this look like as of the previous bar" without ever letting a
    bar's own data leak into a condition that is supposed to precede it.
    """
    out = np.full(values.size, np.nan)
    if periods <= 0 or periods >= values.size:
        return values.copy() if periods <= 0 else out
    out[periods:] = values[:-periods]
    return out


def expanding(values: np.ndarray, periods: int = 3) -> bool:
    """True when the latest value is above where it was `periods` bars ago."""
    if values.size <= periods:
        return False
    current, previous = values[-1], values[-1 - periods]
    if np.isnan(current) or np.isnan(previous):
        return False
    return bool(current > previous)


def min_percentile_rank(values: np.ndarray, lookback: int = 100, recent: int = 6) -> float:
    """The lowest percentile rank seen over the last `recent` bars.

    Answers "was this quiet at any point just now", which is what a squeeze actually is.
    """
    if values.size < recent:
        return percentile_rank(values, lookback)
    ranks = []
    for offset in range(recent):
        end = values.size - offset
        ranks.append(percentile_rank(values[:end], lookback))
    return float(min(ranks))


def percentile_rank(values: np.ndarray, lookback: int = 100) -> float:
    """Where the latest value sits within its own recent history, as 0-1.

    Used for the volatility squeeze: 'quiet' only means anything relative to how quiet this
    particular asset usually is.
    """
    window = values[-lookback:]
    window = window[~np.isnan(window)]
    if window.size < 10:
        return 0.5
    return float((window < window[-1]).sum() / window.size)


def swing_low(low: np.ndarray, lookback: int = 20) -> float:
    window = low[-lookback:]
    return float(window.min()) if window.size else float("nan")


def swing_high(high: np.ndarray, lookback: int = 20) -> float:
    window = high[-lookback:]
    return float(window.max()) if window.size else float("nan")


def slope_pct(values: np.ndarray, period: int = 20) -> float:
    """Least-squares slope over `period` bars, expressed as % per bar.

    Normalised by price so it is comparable between a €0.50 asset and a €60,000 one.
    """
    window = values[-period:]
    window = window[~np.isnan(window)]
    if window.size < 3:
        return 0.0
    x = np.arange(window.size, dtype=float)
    slope = float(np.polyfit(x, window, 1)[0])
    mean = float(window.mean())
    return (slope / mean) * 100.0 if mean else 0.0


def bullish_divergence(close: np.ndarray, oscillator: np.ndarray, lookback: int = 40) -> bool:
    """Price makes a lower low while the oscillator makes a higher low.

    Split the window in half and compare each half's trough: crude, but robust to the noise
    that pivot-detection approaches trip over, and it only ever gates a setup rather than
    triggering one on its own.
    """
    if close.size < lookback or oscillator.size < lookback:
        return False
    price_window = close[-lookback:]
    osc_window = oscillator[-lookback:]
    if np.isnan(osc_window).all():
        return False

    half = lookback // 2
    first_price_idx = int(np.nanargmin(price_window[:half]))
    second_price_idx = int(np.nanargmin(price_window[half:])) + half

    if not (price_window[second_price_idx] < price_window[first_price_idx]):
        return False
    first_osc = osc_window[first_price_idx]
    second_osc = osc_window[second_price_idx]
    if np.isnan(first_osc) or np.isnan(second_osc):
        return False
    return bool(second_osc > first_osc)


def last(values: np.ndarray, default: float = float("nan")) -> float:
    """The most recent non-nan value, or `default`."""
    if values.size == 0:
        return default
    value = values[-1]
    return float(value) if not np.isnan(value) else default
