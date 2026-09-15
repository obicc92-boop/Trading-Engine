"""Binance Spot — market data and real order execution.

Chosen as the default crypto venue for one reason that matters at €100: the minimum order
is ~€5-10 and the taker fee is 0.1%, which is the cheapest way to hold a real position with
a hundred euros. Spot only; this client has no code path that can open a margin position.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import numpy as np

from ..models import Candles, Fill, Instrument, Quote, now_ms
from .base import HttpClient, VenueError

log = logging.getLogger("helm.binance")

# Binance interval names happen to match ours, but pinning the mapping means a future
# venue with different names doesn't silently get the wrong timeframe.
INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "5m": "5m", "1m": "1m"}


class BinanceVenue:
    name = "binance"

    def __init__(self, config) -> None:
        self.config = config
        self.quote_asset = config.quote
        self.live = bool(config.binance_key and config.binance_secret)
        self.http = HttpClient(config.binance_base, timeout=config.request_timeout, rate=8.0)
        self._time_offset = 0
        self._instruments: dict[str, Instrument] = {}
        self._instruments_at = 0.0

    # -- signing -------------------------------------------------------------------------
    def sync_time(self) -> None:
        """Binance rejects requests whose timestamp drifts from its clock.

        Containers drift. We measure the offset once at boot and apply it to every signed
        request rather than trusting the local clock.
        """
        data = self.http.request("GET", "/api/v3/time")
        self._time_offset = int(data["serverTime"]) - now_ms()
        log.info("binance clock offset %dms", self._time_offset)

    def _signed(self, method: str, path: str, params: dict | None = None, retries: int = 3):
        if not self.live:
            raise VenueError("Binance credentials are not configured")
        payload = dict(params or {})
        payload["timestamp"] = now_ms() + self._time_offset
        payload["recvWindow"] = 5000
        query = urlencode(payload)
        signature = hmac.new(
            self.config.binance_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return self.http.request(
            method,
            f"{path}?{query}&signature={signature}",
            headers={"X-MBX-APIKEY": self.config.binance_key},
            retries=retries,
        )

    # -- market data ---------------------------------------------------------------------
    def instruments(self) -> dict[str, Instrument]:
        # Exchange rules change rarely; refetching them every cycle is pure rate-limit waste.
        if self._instruments and time.time() - self._instruments_at < 3600:
            return self._instruments

        data = self.http.request("GET", "/api/v3/exchangeInfo")
        instruments: dict[str, Instrument] = {}
        for item in data.get("symbols", []):
            if item.get("status") != "TRADING":
                continue
            if item.get("quoteAsset") != self.quote_asset:
                continue
            if not item.get("isSpotTradingAllowed", True):
                continue
            tick = step = min_notional = min_qty = 0.0
            for flt in item.get("filters", []):
                kind = flt.get("filterType")
                if kind == "PRICE_FILTER":
                    tick = float(flt.get("tickSize", 0) or 0)
                elif kind == "LOT_SIZE":
                    step = float(flt.get("stepSize", 0) or 0)
                    min_qty = float(flt.get("minQty", 0) or 0)
                elif kind in ("NOTIONAL", "MIN_NOTIONAL"):
                    min_notional = float(flt.get("minNotional", 0) or 0)
            instruments[item["symbol"]] = Instrument(
                symbol=item["symbol"],
                base=item["baseAsset"],
                quote=item["quoteAsset"],
                tick_size=tick,
                step_size=step,
                min_notional=min_notional,
                min_qty=min_qty,
            )
        self._instruments = instruments
        self._instruments_at = time.time()
        log.info("loaded %d %s instruments", len(instruments), self.quote_asset)
        return instruments

    def quotes(self) -> dict[str, Quote]:
        """One call for the whole book — 24h stats for every symbol at once."""
        data = self.http.request("GET", "/api/v3/ticker/24hr")
        allowed = self.instruments()
        out: dict[str, Quote] = {}
        for item in data:
            symbol = item.get("symbol")
            if symbol not in allowed:
                continue
            try:
                out[symbol] = Quote(
                    symbol=symbol,
                    price=float(item["lastPrice"]),
                    quote_volume_24h=float(item.get("quoteVolume", 0) or 0),
                    change_24h_pct=float(item.get("priceChangePercent", 0) or 0),
                )
            except (KeyError, ValueError):
                continue
        return out

    def price(self, symbol: str) -> float:
        data = self.http.request("GET", "/api/v3/ticker/price", params={"symbol": symbol})
        return float(data["price"])

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        interval = INTERVALS.get(timeframe)
        if interval is None:
            raise VenueError(f"unsupported timeframe {timeframe}")
        rows = self.http.request(
            "GET",
            "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        if not rows:
            raise VenueError(f"no candles for {symbol} {timeframe}")
        # The final row is the still-forming candle. Signals must only ever fire on closed
        # bars, otherwise the same setup appears and disappears within a single bar and the
        # engine chases its own tail.
        if len(rows) > 1:
            rows = rows[:-1]
        arr = np.array(
            [[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows],
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

    def listing_age_days(self, symbol: str) -> float:
        """Age from the first monthly candle — cheap and good enough for a 90-day gate."""
        rows = self.http.request(
            "GET",
            "/api/v3/klines",
            params={"symbol": symbol, "interval": "1M", "limit": 1, "startTime": 0},
        )
        if not rows:
            return 0.0
        return (now_ms() - float(rows[0][0])) / 86_400_000.0

    # -- account -------------------------------------------------------------------------
    def balances(self) -> dict[str, float]:
        data = self._signed("GET", "/api/v3/account")
        out: dict[str, float] = {}
        for item in data.get("balances", []):
            total = float(item.get("free", 0) or 0) + float(item.get("locked", 0) or 0)
            if total > 0:
                out[item["asset"]] = total
        return out

    # -- execution -----------------------------------------------------------------------
    def buy(self, symbol: str, quote_amount: float) -> Fill:
        """Market buy for a fixed amount of quote currency.

        `quoteOrderQty` lets us say "spend €37.50" instead of computing a quantity and
        rounding it to the lot size — which at these sizes is the difference between a
        filled order and a `LOT_SIZE` rejection.

        Note `retries=1`: a market order that times out may already be live. We surface the
        error and reconcile against the exchange on the next cycle rather than risk a
        duplicate position.
        """
        instrument = self.instruments().get(symbol)
        if instrument and instrument.min_notional and quote_amount < instrument.min_notional:
            raise VenueError(
                f"{symbol}: {quote_amount:.2f} below exchange minimum {instrument.min_notional:.2f}"
            )
        data = self._signed(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quoteOrderQty": f"{quote_amount:.8f}".rstrip("0"),
                "newOrderRespType": "FULL",
            },
            retries=1,
        )
        return self._fill_from(data, symbol, "BUY")

    def sell(self, symbol: str, qty: float) -> Fill:
        instrument = self.instruments().get(symbol)
        if instrument:
            qty = instrument.round_qty(qty)
        if qty <= 0:
            raise VenueError(f"{symbol}: quantity rounds to zero")
        data = self._signed(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": f"{qty:.8f}".rstrip("0"),
                "newOrderRespType": "FULL",
            },
            retries=1,
        )
        return self._fill_from(data, symbol, "SELL")

    def _fill_from(self, data: dict, symbol: str, side: str) -> Fill:
        """Reconstruct the true average fill from the trade legs.

        A market order can fill across several price levels. Taking `price` from the order
        response would be wrong (it is often 0 for market orders); the volume-weighted
        average of the fills is what actually happened.
        """
        fills = data.get("fills") or []
        qty = float(data.get("executedQty", 0) or 0)
        quote_qty = float(data.get("cummulativeQuoteQty", 0) or 0)
        fee = 0.0
        for leg in fills:
            # Fees paid in the base asset (no BNB balance) reduce the quantity we own.
            if leg.get("commissionAsset") == symbol.replace(self.quote_asset, ""):
                continue
            fee += float(leg.get("commission", 0) or 0)
        price = (quote_qty / qty) if qty > 0 else 0.0
        if price <= 0 and fills:
            price = float(fills[0].get("price", 0) or 0)
        return Fill(
            order_id=str(data.get("orderId", "")),
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            qty=qty,
            price=price,
            fee=fee,
            status=data.get("status", "FILLED"),
            raw={"orderId": data.get("orderId"), "status": data.get("status")},
        )
