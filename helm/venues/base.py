"""Venue interfaces and shared HTTP plumbing.

A venue is split into two roles because they fail differently and are configured
differently: a `MarketSource` reads prices (public, cheap, tolerant of retries) and a
`Broker` moves money (signed, expensive, and emphatically *not* safe to retry blindly).

The retry policy below reflects that: reads retry, writes do not. A timed-out order may
well have been filled, and firing a second one to find out is how you end up with two
positions and one plan.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Protocol

import httpx

from ..models import Candles, Fill, Instrument, Quote

log = logging.getLogger("helm.venue")


class VenueError(RuntimeError):
    """Anything the venue refused or couldn't answer."""


class RateLimiter:
    """A token bucket, because every exchange bans you for the same reason."""

    def __init__(self, rate_per_sec: float, burst: int = 10) -> None:
        self.rate = rate_per_sec
        self.burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                time.sleep(wait)
                self._tokens = 0.0
                self._last = time.monotonic()
            else:
                self._tokens -= 1.0


class HttpClient:
    def __init__(self, base_url: str, timeout: float = 15.0, rate: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "helm/1.0"},
        )
        self._limiter = RateLimiter(rate)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: Any = None,
        retries: int = 3,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        last: Exception | None = None
        for attempt in range(retries):
            self._limiter.acquire()
            try:
                response = self._client.request(
                    method, url, params=params, headers=headers, content=data
                )
            except httpx.HTTPError as exc:
                last = VenueError(f"{method} {path}: {exc}")
            else:
                if response.status_code < 400:
                    return response.json() if response.content else {}
                body = response.text[:400]
                # 4xx other than rate-limiting is a bug in our request, not bad luck.
                if response.status_code in (418, 429) or response.status_code >= 500:
                    last = VenueError(f"{method} {path}: {response.status_code} {body}")
                else:
                    raise VenueError(f"{method} {path}: {response.status_code} {body}")
            if attempt < retries - 1:
                backoff = (2**attempt) + random.uniform(0, 0.4)
                log.warning("retrying %s %s in %.1fs (%s)", method, path, backoff, last)
                time.sleep(backoff)
        raise last or VenueError(f"{method} {path} failed")

    def close(self) -> None:
        self._client.close()


class MarketSource(Protocol):
    """Read-only market access."""

    name: str

    def instruments(self) -> dict[str, Instrument]: ...

    def quotes(self) -> dict[str, Quote]: ...

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> Candles: ...


class Broker(Protocol):
    """Anything that can hold a balance and fill an order."""

    name: str
    live: bool

    def balances(self) -> dict[str, float]: ...

    def buy(self, symbol: str, quote_amount: float) -> Fill: ...

    def sell(self, symbol: str, qty: float) -> Fill: ...
