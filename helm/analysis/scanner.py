"""The scanner — turning a venue into a ranked shortlist of candidates.

Two stages, in this order for a reason. First a cheap filter over the whole venue using one
bulk ticker call: liquidity, age, sanity. Only what survives gets the expensive treatment —
three timeframes of candles per symbol. Reversing that order would mean hundreds of API
calls per cycle to learn things a single call already told us.
"""

from __future__ import annotations

import logging

from .. import doctrine
from ..models import Candidate, Candles
from . import regime as regime_module
from . import setups

log = logging.getLogger("helm.scanner")

# Duplicates of an asset already in the universe, and pegged instruments that cannot trend.
EXCLUDED_BASES = {
    "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "EUR", "GBP", "USD",
    "WBTC", "WETH", "STETH", "WBETH", "BETH",
}


class Scanner:
    def __init__(self, market, config) -> None:
        self.market = market
        self.config = config
        self._age_cache: dict[str, float] = {}

    def universe(self) -> list[str]:
        """The symbols worth spending candle requests on."""
        if self.config.symbols:
            # A pinned list is an explicit operator decision; honour it, but only for
            # symbols the venue actually trades.
            available = set(self.market.instruments())
            return [s for s in self.config.symbols if s in available][: doctrine.MAX_UNIVERSE_SIZE]

        quotes = self.market.quotes()
        instruments = self.market.instruments()
        ranked = []
        for symbol, quote in quotes.items():
            instrument = instruments.get(symbol)
            if instrument is None or instrument.base in EXCLUDED_BASES:
                continue
            if quote.quote_volume_24h < doctrine.MIN_QUOTE_VOLUME_24H:
                continue
            if quote.price <= 0:
                continue
            ranked.append((quote.quote_volume_24h, symbol))

        ranked.sort(reverse=True)
        shortlist = [symbol for _, symbol in ranked[: doctrine.MAX_UNIVERSE_SIZE * 2]]
        return self._filter_by_age(shortlist)[: doctrine.MAX_UNIVERSE_SIZE]

    def _filter_by_age(self, symbols: list[str]) -> list[str]:
        """Drop anything younger than the doctrine's listing-age floor.

        Cached permanently — an asset's listing date does not change, and this costs one
        request per symbol the first time it is seen.
        """
        getter = getattr(self.market, "listing_age_days", None)
        if getter is None:
            return symbols
        keep = []
        for symbol in symbols:
            age = self._age_cache.get(symbol)
            if age is None:
                try:
                    age = float(getter(symbol))
                except Exception as exc:
                    log.debug("age lookup failed for %s: %s", symbol, exc)
                    age = 0.0
                self._age_cache[symbol] = age
            if age >= doctrine.MIN_LISTING_AGE_DAYS:
                keep.append(symbol)
        return keep

    def scan(self) -> tuple[list[Candidate], list[dict]]:
        """Analyse the universe. Returns (candidates, per-symbol snapshot for the UI).

        The snapshot includes every symbol looked at, including the rejected ones — the
        dashboard should show what was considered and passed over, not only what was traded.
        """
        symbols = self.universe()
        if not symbols:
            return [], []

        series: dict[str, dict[str, Candles]] = {}
        for symbol in symbols:
            try:
                series[symbol] = {
                    doctrine.REGIME_TF: self.market.candles(symbol, doctrine.REGIME_TF, 250),
                    doctrine.STRUCTURE_TF: self.market.candles(symbol, doctrine.STRUCTURE_TF, 250),
                    doctrine.TRIGGER_TF: self.market.candles(symbol, doctrine.TRIGGER_TF, 120),
                }
            except Exception as exc:
                log.warning("candles unavailable for %s: %s", symbol, exc)

        strength = self._relative_strength(series)

        candidates: list[Candidate] = []
        snapshot: list[dict] = []

        for symbol, frames in series.items():
            regime_tf = frames[doctrine.REGIME_TF]
            structure_tf = frames[doctrine.STRUCTURE_TF]
            trigger_tf = frames[doctrine.TRIGGER_TF]

            regime, metrics = regime_module.classify(regime_tf)
            analysis = setups.analyse(
                symbol, trigger_tf, structure_tf, regime, metrics, strength.get(symbol, 0.5)
            )
            candidate = setups.detect(symbol, trigger_tf, structure_tf, analysis)
            if candidate is not None:
                candidates.append(candidate)

            snapshot.append(
                {
                    "symbol": symbol,
                    "price": analysis.price,
                    "regime": regime,
                    "score": candidate.score if candidate else 0.0,
                    "setup": candidate.setup if candidate else None,
                    "tradeable": regime in doctrine.TRADEABLE_REGIMES,
                    "rsi": analysis.indicators.get("rsi"),
                    "atr_pct": analysis.indicators.get("atr_pct"),
                    "relative_strength": strength.get(symbol, 0.5),
                    "change_24h": self._change_24h(structure_tf),
                }
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        snapshot.sort(key=lambda row: row["score"], reverse=True)
        return candidates, snapshot

    def _relative_strength(self, series: dict[str, dict[str, Candles]]) -> dict[str, float]:
        """Rank each symbol's ~7-day return against the rest of the universe, as 0-1.

        Relative rather than absolute: in a market where everything is up 20%, being up 20%
        is not strength. The point is to prefer the leaders over the laggards.
        """
        returns: dict[str, float] = {}
        for symbol, frames in series.items():
            candles = frames.get(doctrine.REGIME_TF)
            if candles is None or len(candles) < 42:
                continue
            start = float(candles.close[-42])   # 42 x 4h ≈ 7 days
            if start > 0:
                returns[symbol] = float(candles.close[-1]) / start - 1.0
        if len(returns) < 2:
            return {symbol: 0.5 for symbol in series}

        ordered = sorted(returns.items(), key=lambda item: item[1])
        span = len(ordered) - 1
        out = {symbol: index / span for index, (symbol, _) in enumerate(ordered)}
        for symbol in series:
            out.setdefault(symbol, 0.5)
        return out

    @staticmethod
    def _change_24h(candles: Candles) -> float:
        if len(candles) < 25:
            return 0.0
        start = float(candles.close[-25])
        return (float(candles.close[-1]) / start - 1.0) * 100 if start else 0.0
