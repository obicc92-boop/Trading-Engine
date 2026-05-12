from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from engine.services.bybit_service import BybitAPIError, BybitService


class MarketDataError(RuntimeError):
    """Raised when market data cannot be fetched or normalized."""


@dataclass(slots=True)
class MarketDataService:
    bybit_service: BybitService
    logger: logging.Logger

    def get_crypto_data(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        try:
            response = self.bybit_service.get_kline(symbol=symbol, interval=interval, limit=limit)
        except BybitAPIError as exc:
            raise MarketDataError(str(exc)) from exc

        candles = response["result"].get("list", [])
        if not candles:
            raise MarketDataError(f"Bybit returned empty kline data for {symbol} {interval}.")

        frame = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
        )
        frame = frame[["timestamp", "open", "high", "low", "close", "volume"]]
        frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")

        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        normalized = frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
        normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], unit="ms", utc=True)
        if normalized.empty:
            raise MarketDataError(f"Bybit returned no usable normalized data for {symbol} {interval}.")
        return normalized.reset_index(drop=True)
