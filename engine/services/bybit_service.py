from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests

from engine.utils.config import AppConfig
from engine.utils.retry import retry


BYBIT_INTERVAL_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
}


class BybitAPIError(RuntimeError):
    """Raised when the Bybit API returns an error or malformed response."""


@dataclass(slots=True)
class BybitService:
    config: AppConfig
    logger: logging.Logger
    session: requests.Session = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.session = requests.Session()

    def get_kline(self, symbol: str, interval: str, limit: int) -> dict[str, Any]:
        mapped_interval = BYBIT_INTERVAL_MAP.get(interval)
        if mapped_interval is None:
            raise BybitAPIError(f"Unsupported Bybit interval: {interval}")

        return self._request(
            "GET",
            "/v5/market/kline",
            params={
                "category": "spot",
                "symbol": symbol.upper(),
                "interval": mapped_interval,
                "limit": str(limit),
            },
        )

    def get_current_price(self, symbol: str) -> float:
        response = self._request(
            "GET",
            "/v5/market/tickers",
            params={"category": "spot", "symbol": symbol.upper()},
        )
        tickers = response["result"].get("list", [])
        if not tickers:
            raise BybitAPIError(f"No ticker data returned for {symbol}.")
        return float(tickers[0]["lastPrice"])

    def get_account_balance(self, coins: list[str] | None = None) -> dict[str, Any]:
        params = {"accountType": self.config.bybit_account_type}
        if coins:
            params["coin"] = ",".join(coin.upper() for coin in coins)

        response = self._request(
            "GET",
            "/v5/account/wallet-balance",
            params=params,
            auth_required=True,
        )
        accounts = response["result"].get("list", [])
        if not accounts:
            raise BybitAPIError("Bybit wallet balance response did not include account data.")
        account = accounts[0]
        return {
            "account_type": account.get("accountType", self.config.bybit_account_type),
            "total_equity": float(account.get("totalEquity", 0) or 0),
            "total_wallet_balance": float(account.get("totalWalletBalance", 0) or 0),
            "total_available_balance": float(account.get("totalAvailableBalance", 0) or 0),
            "coins": [
                {
                    "coin": coin.get("coin"),
                    "equity": float(coin.get("equity", 0) or 0),
                    "wallet_balance": float(coin.get("walletBalance", 0) or 0),
                    "usd_value": float(coin.get("usdValue", 0) or 0),
                    "locked": float(coin.get("locked", 0) or 0),
                }
                for coin in account.get("coin", [])
            ],
        }

    def place_spot_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        confirmed: bool = False,
        order_type: str = "Market",
        price: float | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.bybit_allow_order_placement:
            raise BybitAPIError("Live order placement is disabled by configuration.")
        if self.config.bybit_require_manual_confirmation and not confirmed:
            raise BybitAPIError("Manual confirmation is required before any live order can be submitted.")
        if side not in {"Buy", "Sell"}:
            raise BybitAPIError("Bybit order side must be 'Buy' or 'Sell'.")

        payload: dict[str, Any] = {
            "category": "spot",
            "symbol": symbol.upper(),
            "side": side,
            "orderType": order_type,
            "qty": f"{quantity:.8f}",
            "isLeverage": 0,
            "orderFilter": "Order",
        }

        if order_type == "Market" and side == "Buy":
            payload["marketUnit"] = "baseCoin"
        if price is not None:
            payload["price"] = f"{price:.8f}"
        if order_link_id:
            payload["orderLinkId"] = order_link_id

        return self._request("POST", "/v5/order/create", json_body=payload, auth_required=True)

    def get_spot_order(
        self,
        *,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not order_id and not order_link_id:
            raise BybitAPIError("Either order_id or order_link_id is required to query a Bybit order.")

        params: dict[str, Any] = {"category": "spot"}
        if order_id:
            params["orderId"] = order_id
        if order_link_id:
            params["orderLinkId"] = order_link_id

        realtime = self._request("GET", "/v5/order/realtime", params=params, auth_required=True)
        realtime_orders = list(realtime.get("result", {}).get("list", []) or [])
        if realtime_orders:
            return dict(realtime_orders[0])

        history = self._request("GET", "/v5/order/history", params=params, auth_required=True)
        history_orders = list(history.get("result", {}).get("list", []) or [])
        if history_orders:
            return dict(history_orders[0])

        return None

    @retry(attempts=3, delay_seconds=1.0, backoff=2.0, exceptions=(requests.RequestException, BybitAPIError))
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth_required: bool = False,
    ) -> dict[str, Any]:
        params = {key: value for key, value in (params or {}).items() if value is not None}
        body = json_body or {}

        headers: dict[str, str] = {}
        if auth_required:
            if not self.config.bybit_api_key or not self.config.bybit_api_secret:
                raise BybitAPIError("Authenticated Bybit requests require API credentials.")
            headers.update(self._build_auth_headers(method=method, params=params, body=body))

        response = self.session.request(
            method=method,
            url=f"{self.config.bybit_base_url}{path}",
            params=params if method.upper() == "GET" else None,
            json=body if method.upper() != "GET" else None,
            headers=headers,
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()

        payload = response.json()
        if payload.get("retCode") != 0:
            raise BybitAPIError(f"Bybit error {payload.get('retCode')}: {payload.get('retMsg')}")
        return payload

    def _build_auth_headers(self, *, method: str, params: dict[str, Any], body: dict[str, Any]) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        recv_window = str(self.config.bybit_recv_window)

        if method.upper() == "GET":
            payload = urlencode(params)
        else:
            payload = json.dumps(body, separators=(",", ":"))

        message = f"{timestamp}{self.config.bybit_api_key}{recv_window}{payload}"
        signature = hmac.new(
            self.config.bybit_api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-BAPI-API-KEY": self.config.bybit_api_key or "",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
            "Content-Type": "application/json",
        }
