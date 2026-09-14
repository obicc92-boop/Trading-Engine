"""Regime classification — the tide, read on the 4h chart.

The regime is a veto, not a signal. Its only job is to answer "is this an environment where
my setups have any business firing?" Longs are permitted in `TRENDING_UP` and `RANGING`;
everything else is sat out. Most of the money this system doesn't lose, it doesn't lose here.
"""

from __future__ import annotations

import numpy as np

from ..models import Candles, Regime
from . import indicators as ind


def classify(candles: Candles) -> tuple[Regime, dict[str, float]]:
    close, high, low = candles.close, candles.high, candles.low
    if close.size < 60:
        return "UNKNOWN", {}

    ema50 = ind.ema(close, 50)
    ema200 = ind.ema(close, 200) if close.size >= 200 else ind.ema(close, min(100, close.size - 1))
    adx_series = ind.adx(high, low, close)
    atr_series = ind.atr(high, low, close)

    price = float(close[-1])
    fast = ind.last(ema50, price)
    slow = ind.last(ema200, price)
    trend_strength = ind.last(adx_series, 0.0)
    atr_value = ind.last(atr_series, 0.0)
    atr_pct = (atr_value / price) if price else 0.0
    slope = ind.slope_pct(ema50, 20)

    # "Volatile" is relative to the asset's own history, not an absolute number — 3% daily
    # range is calm for one asset and a crisis for another.
    atr_pct_series = atr_series / np.where(close > 0, close, np.nan)
    vol_rank = ind.percentile_rank(atr_pct_series, 120)

    metrics = {
        "price": price,
        "ema50": fast,
        "ema200": slow,
        "adx": trend_strength,
        "atr": atr_value,
        "atr_pct": atr_pct,
        "slope_pct": slope,
        "vol_rank": vol_rank,
        "ema_spread_pct": ((fast / slow - 1) * 100) if slow else 0.0,
    }

    # Extreme volatility overrides everything: stops get run in both directions and the
    # sizing maths stops describing reality.
    if vol_rank > 0.92 and atr_pct > 0.035:
        return "VOLATILE", metrics

    aligned_up = fast > slow and slope > 0.02
    aligned_down = fast < slow and slope < -0.02
    trending = trend_strength >= 20.0

    if aligned_up and trending:
        return "TRENDING_UP", metrics
    if aligned_down and trending:
        return "TRENDING_DOWN", metrics
    # A weak downward alignment is still not somewhere to buy dips.
    if aligned_down and trend_strength >= 15.0:
        return "TRENDING_DOWN", metrics
    return "RANGING", metrics
