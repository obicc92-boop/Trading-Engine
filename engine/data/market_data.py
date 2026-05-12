from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests

from engine.services.bybit_service import BybitAPIError, BybitService
from engine.utils.config import AppConfig
from engine.utils.retry import retry


BINANCE_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
}


class MarketDataError(RuntimeError):
    """Raised when market data cannot be fetched or normalized."""


@dataclass(slots=True)
class MarketDataService:
    config: AppConfig
    bybit_service: BybitService
    logger: logging.Logger
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        self.session = requests.Session()

    def get_crypto_data(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if self.config.market_data_provider == "binance":
            return self._get_binance_crypto_data(symbol=symbol, interval=interval, limit=limit)
        return self._get_bybit_crypto_data(symbol=symbol, interval=interval, limit=limit)

    def _get_bybit_crypto_data(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
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

    def _get_binance_crypto_data(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        mapped_interval = BINANCE_INTERVAL_MAP.get(interval)
        if mapped_interval is None:
            raise MarketDataError(f"Unsupported Binance interval: {interval}")

        candles = self._request_binance_klines(
            symbol=symbol.upper(),
            interval=mapped_interval,
            limit=limit,
        )
        if not candles:
            raise MarketDataError(f"Binance returned empty kline data for {symbol} {interval}.")

        frame = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "number_of_trades",
                "taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume",
                "ignore",
            ],
        )
        frame = frame[["timestamp", "open", "high", "low", "close", "volume"]]
        frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")

        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        normalized = frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
        normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], unit="ms", utc=True)
        if normalized.empty:
            raise MarketDataError(f"Binance returned no usable normalized data for {symbol} {interval}.")
        return normalized.reset_index(drop=True)

    @retry(attempts=3, delay_seconds=1.0, backoff=2.0, exceptions=(requests.RequestException, MarketDataError))
    def _request_binance_klines(self, *, symbol: str, interval: str, limit: int) -> list[list[Any]]:
        if self.session is None:
            self.session = requests.Session()

        response = self.session.get(
            f"{self.config.binance_base_url}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise MarketDataError(f"Unexpected Binance kline payload for {symbol} {interval}.")
        return payload
