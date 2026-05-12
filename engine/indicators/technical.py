from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD, SMAIndicator
from ta.volatility import AverageTrueRange


def calculate_ma(df: pd.DataFrame, period: int) -> pd.DataFrame:
    result = df.copy()
    result[f"ma_{period}"] = SMAIndicator(close=result["close"], window=period).sma_indicator()
    return result


def calculate_rsi(df: pd.DataFrame, period: int) -> pd.DataFrame:
    result = df.copy()
    result[f"rsi_{period}"] = RSIIndicator(close=result["close"], window=period).rsi()
    return result


def calculate_macd(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    indicator = MACD(close=result["close"])
    result["macd"] = indicator.macd()
    result["macd_signal"] = indicator.macd_signal()
    result["macd_diff"] = indicator.macd_diff()
    return result


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    result = df.copy()
    result[f"atr_{period}"] = AverageTrueRange(
        high=result["high"], low=result["low"], close=result["close"], window=period
    ).average_true_range()
    return result


def apply_core_indicators(df: pd.DataFrame, include_macd: bool = True) -> pd.DataFrame:
    result = df.copy()
    result["ema_20"] = EMAIndicator(close=result["close"], window=20).ema_indicator()
    result["ma_50"] = SMAIndicator(close=result["close"], window=50).sma_indicator()
    result["ma_200"] = SMAIndicator(close=result["close"], window=200).sma_indicator()
    result["rsi_14"] = RSIIndicator(close=result["close"], window=14).rsi()
    result["atr_14"] = AverageTrueRange(
        high=result["high"], low=result["low"], close=result["close"], window=14
    ).average_true_range()
    result["adx_14"] = ADXIndicator(
        high=result["high"], low=result["low"], close=result["close"], window=14
    ).adx()
    result["volume_sma_20"] = result["volume"].rolling(window=20).mean()
    result["volume_ratio_20"] = result["volume"].div(result["volume_sma_20"].replace(0, pd.NA))
    result["highest_high_20"] = result["high"].rolling(window=20).max().shift(1)
    result["lowest_low_20"] = result["low"].rolling(window=20).min().shift(1)
    result["atr_pct_14"] = result["atr_14"].div(result["close"].replace(0, pd.NA))
    if include_macd:
        indicator = MACD(close=result["close"])
        result["macd"] = indicator.macd()
        result["macd_signal"] = indicator.macd_signal()
        result["macd_diff"] = indicator.macd_diff()
    return result
