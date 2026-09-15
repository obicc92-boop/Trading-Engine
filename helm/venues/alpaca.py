"""Alpaca — US equities with fractional shares.

Stocks need two accommodations that crypto doesn't. First, fractional shares: €100 cannot
buy a meaningful round lot of anything worth owning, so every order is notional-denominated.
Second, the market closes — which means a stop can sit unreachable overnight and across
weekends. The engine's time stops are measured in wall-clock hours, so equities are the
secondary venue by design and crypto is the default.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from ..models import Candles, Fill, Instrument, Quote
from .base import HttpClient, VenueError

log = logging.getLogger("helm.alpaca")

TIMEFRAMES = {"15m": "15Min", "1h": "1Hour", "4h": "4Hour", "1d": "1Day", "5m": "5Min"}


class AlpacaVenue:
    name = "alpaca"

    def __init__(self, config) -> None:
        self.config = config
        self.quote_asset = "USD"
        self.live = bool(config.alpaca_key and config.alpaca_secret)
        self.trading = HttpClient(config.alpaca_base, timeout=config.request_timeout, rate=3.0)
        self.data = HttpClient(config.alpaca_data_base, timeout=config.request_timeout, rate=3.0)
        self._instruments: dict[str, Instrument] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.config.alpaca_key,
            "APCA-API-SECRET-KEY": self.config.alpaca_secret,
        }

    def market_open(self) -> bool:
        try:
            return bool(self.trading.request("GET", "/v2/clock", headers=self._headers)["is_open"])
        except Exception as exc:  # a clock failure must not be read as "market open"
            log.warning("clock check failed: %s", exc)
            return False

    def instruments(self) -> dict[str, Instrument]:
        if self._instruments:
            return self._instruments
        symbols = self.config.symbols or []
        out: dict[str, Instrument] = {}
        for symbol in symbols:
            out[symbol] = Instrument(
                symbol=symbol,
                base=symbol,
                quote="USD",
                tick_size=0.01,
                step_size=0.0,        # fractional: any quantity is valid
                min_notional=1.0,     # Alpaca's fractional minimum
            )
        self._instruments = out
        return out

    def quotes(self) -> dict[str, Quote]:
        symbols = list(self.instruments())
        if not symbols:
            return {}
        data = self.data.request(
            "GET",
            "/v2/stocks/snapshots",
            params={"symbols": ",".join(symbols)},
            headers=self._headers,
        )
        out: dict[str, Quote] = {}
        snapshots = data.get("snapshots", data)
        for symbol, snap in snapshots.items():
            bar = snap.get("dailyBar") or {}
            prev = snap.get("prevDailyBar") or {}
            price = float((snap.get("latestTrade") or {}).get("p", bar.get("c", 0)) or 0)
            prev_close = float(prev.get("c", 0) or 0)
            if price <= 0:
                continue
            out[symbol] = Quote(
                symbol=symbol,
                price=price,
                quote_volume_24h=float(bar.get("v", 0) or 0) * price,
                change_24h_pct=((price / prev_close - 1) * 100) if prev_close else 0.0,
            )
        return out

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        tf = TIMEFRAMES.get(timeframe)
        if tf is None:
            raise VenueError(f"unsupported timeframe {timeframe}")
        data = self.data.request(
            "GET",
            "/v2/stocks/bars",
            params={"symbols": symbol, "timeframe": tf, "limit": limit, "adjustment": "split"},
            headers=self._headers,
        )
        bars = (data.get("bars") or {}).get(symbol) or []
        if not bars:
            raise VenueError(f"no bars for {symbol} {timeframe}")
        arr = np.array(
            [
                [
                    _epoch_ms(bar["t"]),
                    float(bar["o"]),
                    float(bar["h"]),
                    float(bar["l"]),
                    float(bar["c"]),
                    float(bar["v"]),
                ]
                for bar in bars
            ],
            dtype=float,
        )
        return Candles(
            symbol=symbol,
            timeframe=timeframe,
            open_time=arr[:, 0],
            open=arr[:, 1],
            high=arr[:, 2],
            low=arr[:, 3],
            close=arr[:, 4],
            volume=arr[:, 5],
        )

    def balances(self) -> dict[str, float]:
        account = self.trading.request("GET", "/v2/account", headers=self._headers)
        out = {"USD": float(account.get("cash", 0) or 0)}
        positions = self.trading.request("GET", "/v2/positions", headers=self._headers)
        for item in positions or []:
            out[item["symbol"]] = float(item.get("qty", 0) or 0)
        return out

    def buy(self, symbol: str, quote_amount: float) -> Fill:
        return self._order(symbol, "buy", notional=round(quote_amount, 2))

    def sell(self, symbol: str, qty: float) -> Fill:
        return self._order(symbol, "sell", qty=qty)

    def _order(self, symbol: str, side: str, notional: float | None = None, qty: float | None = None) -> Fill:
        if not self.live:
            raise VenueError("Alpaca credentials are not configured")
        body: dict = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day"}
        if notional is not None:
            body["notional"] = str(notional)
        else:
            body["qty"] = str(qty)
        import json as _json

        created = self.trading.request(
            "POST",
            "/v2/orders",
            headers={**self._headers, "Content-Type": "application/json"},
            data=_json.dumps(body),
            retries=1,   # never blind-retry an order
        )
        return self._await_fill(created["id"], symbol, side.upper())

    def _await_fill(self, order_id: str, symbol: str, side: str, timeout: float = 30.0) -> Fill:
        """Equity fills are asynchronous; poll until the order settles.

        On timeout we raise rather than assume — the next cycle reconciles against the
        broker's own position list, which is the only number that can be trusted.
        """
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = self.trading.request("GET", f"/v2/orders/{order_id}", headers=self._headers)
            status = last.get("status")
            if status == "filled":
                qty = float(last.get("filled_qty", 0) or 0)
                price = float(last.get("filled_avg_price", 0) or 0)
                return Fill(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,  # type: ignore[arg-type]
                    qty=qty,
                    price=price,
                    fee=0.0,  # Alpaca equities are commission-free
                    status="FILLED",
                    raw={"id": order_id},
                )
            if status in {"canceled", "expired", "rejected"}:
                raise VenueError(f"order {order_id} {status}")
            time.sleep(1.0)
        raise VenueError(f"order {order_id} did not fill within {timeout:.0f}s")


def _epoch_ms(timestamp: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000
