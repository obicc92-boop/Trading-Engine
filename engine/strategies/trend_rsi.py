from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from engine.models import Signal
from engine.strategies.base import BaseStrategy


@dataclass(slots=True)
class TrendRsiStrategy(BaseStrategy):
    name: str = "trend_rsi"
    breakout_lookback: int = 20
    volume_confirmation_ratio: float = 1.10
    pullback_tolerance_pct: float = 0.012

    def build_market_context(
        self,
        df: pd.DataFrame,
        *,
        symbol: str = "",
        interval: str = "",
        market: str = "spot",
    ) -> dict[str, Any]:
        if df.empty:
            return self._neutral_context(symbol=symbol, interval=interval)

        latest = df.iloc[-1]
        close_price = self._float_value(latest, "close")
        ema20 = self._float_value(latest, "ema_20")
        ma50 = self._float_value(latest, "ma_50")
        ma200 = self._float_value(latest, "ma_200")
        rsi14 = self._float_value(latest, "rsi_14")
        atr_pct = self._float_value(latest, "atr_pct_14")
        adx14 = self._float_value(latest, "adx_14")
        volume_ratio = self._float_value(latest, "volume_ratio_20")
        highest_high = self._rolling_high(df, self.breakout_lookback)
        macd = self._float_value(latest, "macd")
        macd_signal = self._float_value(latest, "macd_signal")

        regime = self._classify_regime(
            close_price=close_price,
            ema20=ema20,
            ma50=ma50,
            ma200=ma200,
            rsi=rsi14,
            adx=adx14,
            atr_pct=atr_pct,
            volume_ratio=volume_ratio,
            highest_high=highest_high,
        )
        bullish = close_price > ma50 > 0 and ma50 >= ma200 > 0 and rsi14 >= 52
        macd_bullish = macd > macd_signal and macd_signal != 0
        strength_score = self._score_strength(
            close_price=close_price,
            ema20=ema20,
            ma50=ma50,
            ma200=ma200,
            rsi=rsi14,
            adx=adx14,
            volume_ratio=volume_ratio,
            atr_pct=atr_pct,
            bullish=bullish,
            macd_bullish=macd_bullish,
            regime_slug=regime["slug"],
        )

        return {
            "symbol": symbol,
            "interval": interval,
            "market": market,
            "bullish": bullish,
            "regime_slug": regime["slug"],
            "regime_label": regime["label"],
            "strength_score": strength_score,
            "volume_ratio": round(volume_ratio, 4),
            "adx_14": round(adx14, 4),
            "atr_pct_14": round(atr_pct, 6),
            "ma_trend_pct": round(((ma50 - ma200) / ma200) * 100, 4) if ma200 > 0 else 0.0,
            "close": round(close_price, 6),
            "ema_20": round(ema20, 6),
            "ma_50": round(ma50, 6),
            "ma_200": round(ma200, 6),
            "rsi_14": round(rsi14, 4),
        }

    def run_strategy(
        self,
        df: pd.DataFrame,
        *,
        symbol: str = "",
        interval: str = "",
        market: str = "spot",
        higher_timeframe_context: dict[str, Any] | None = None,
    ) -> Signal:
        if len(df.index) < 3:
            return Signal(
                symbol=symbol,
                interval=interval,
                market=market,
                action="HOLD",
                confidence=0,
                reason="Insufficient candle history to evaluate the upgraded strategy.",
                entry=self._float_value(df.iloc[-1], "close") if not df.empty else None,
            )

        latest = df.iloc[-1]
        previous = df.iloc[-2]
        close_price = self._float_value(latest, "close")
        ema20 = self._float_value(latest, "ema_20")
        ma50 = self._float_value(latest, "ma_50")
        ma200 = self._float_value(latest, "ma_200")
        rsi14 = self._float_value(latest, "rsi_14")
        macd = self._float_value(latest, "macd")
        macd_signal = self._float_value(latest, "macd_signal")
        macd_diff = self._float_value(latest, "macd_diff")
        previous_macd_diff = self._float_value(previous, "macd_diff")
        atr14 = self._float_value(latest, "atr_14")
        atr_pct = self._float_value(latest, "atr_pct_14")
        adx14 = self._float_value(latest, "adx_14")
        volume = self._float_value(latest, "volume")
        volume_ratio = self._float_value(latest, "volume_ratio_20")
        highest_high = self._rolling_high(df, self.breakout_lookback)
        lowest_low = self._rolling_low(df, self.breakout_lookback)

        if ma50 <= 0 or rsi14 <= 0 or ema20 <= 0:
            return Signal(
                symbol=symbol,
                interval=interval,
                market=market,
                action="HOLD",
                confidence=0,
                reason="Insufficient indicator history to generate a signal.",
                entry=close_price,
            )

        local_regime = self._classify_regime(
            close_price=close_price,
            ema20=ema20,
            ma50=ma50,
            ma200=ma200,
            rsi=rsi14,
            adx=adx14,
            atr_pct=atr_pct,
            volume_ratio=volume_ratio,
            highest_high=highest_high,
        )
        higher_context = higher_timeframe_context or self._neutral_context(symbol=symbol, interval=interval)
        higher_timeframe_bullish = bool(higher_context.get("bullish"))
        higher_timeframe_label = str(higher_context.get("regime_label") or "Unclassified")
        symbol_strength_score = self._coerce_float(higher_context.get("strength_score"))
        symbol_rank = int(round(self._coerce_float(higher_context.get("symbol_rank"))))
        trade_priority_allowed = bool(higher_context.get("trade_priority_allowed", True))

        bullish_trend = close_price > ma50 and ema20 >= ma50
        long_trend_bullish = ma50 >= ma200 > 0
        bearish_trend = close_price < ema20 and ema20 <= ma50
        macd_bullish = macd > macd_signal and macd_diff > 0
        macd_bearish = macd < macd_signal and macd_diff < 0

        recent_pullback_low = float(df.tail(4)["low"].min())
        pulled_back_into_trend = recent_pullback_low <= max(ema20, ma50) * (1 + (self.pullback_tolerance_pct / 2))
        recovered_from_pullback = (
            close_price >= ema20
            and close_price > self._float_value(previous, "close")
            and rsi14 >= 50
            and rsi14 <= 68
            and macd_diff >= previous_macd_diff
        )
        breakout_ready = (
            bullish_trend
            and long_trend_bullish
            and highest_high > 0
            and close_price > highest_high
            and rsi14 >= 56
            and rsi14 <= 78
            and macd_bullish
            and volume_ratio >= self.volume_confirmation_ratio
            and local_regime["slug"] in {"trend", "breakout"}
        )
        pullback_ready = (
            bullish_trend
            and long_trend_bullish
            and pulled_back_into_trend
            and recovered_from_pullback
            and volume_ratio >= 0.90
            and local_regime["slug"] in {"trend", "transition"}
        )
        sell_ready = (
            bearish_trend
            and (rsi14 < 48 or not long_trend_bullish)
            and macd_bearish
        )

        action = "HOLD"
        setup_type = "none"
        reasons: list[str] = []

        if breakout_ready:
            action = "BUY"
            setup_type = "breakout"
            reasons.append("Price broke above the recent breakout range with trend confirmation.")
            reasons.append(f"Volume is {volume_ratio:.2f}x its 20-candle average, confirming participation.")
        elif pullback_ready:
            action = "BUY"
            setup_type = "pullback"
            reasons.append("Price pulled back into the trend zone and recovered without losing structure.")
            reasons.append("Momentum improved after the pullback, which supports a continuation entry.")
        elif sell_ready:
            action = "SELL"
            reasons.append("Momentum deteriorated below the short-term trend line, so the open long should be de-risked.")
            reasons.append("In spot mode this remains an exit signal rather than a short entry.")
        else:
            reasons.append("Breakout and pullback rules are not complete, so the setup stays neutral.")

        if action == "BUY":
            if higher_timeframe_bullish:
                reasons.append(f"The higher timeframe is supportive: {higher_timeframe_label}.")
            else:
                reasons.append(
                    f"The local setup exists, but the higher timeframe is not bullish enough yet: {higher_timeframe_label}."
                )
            if not trade_priority_allowed:
                reasons.append(
                    f"{symbol} is outside the strongest ranked symbols right now, so the risk layer should keep this as a watch-only setup."
                )
            else:
                reasons.append(f"{symbol} is currently ranked #{max(symbol_rank, 1)} on the strength table.")
            reasons.append(f"Local regime: {local_regime['label']}.")
        elif action == "SELL":
            reasons.append(f"Local regime: {local_regime['label']}.")

        confidence = self._score_confidence(
            action=action,
            setup_type=setup_type,
            close_price=close_price,
            ema20=ema20,
            ma50=ma50,
            ma200=ma200 if ma200 > 0 else None,
            rsi=rsi14,
            adx=adx14,
            volume_ratio=volume_ratio,
            macd_alignment=macd_bullish if action == "BUY" else macd_bearish,
            local_regime_slug=local_regime["slug"],
            higher_timeframe_bullish=higher_timeframe_bullish,
            symbol_strength_score=symbol_strength_score,
            trade_priority_allowed=trade_priority_allowed,
        )

        return Signal(
            symbol=symbol,
            interval=interval,
            market=market,
            action=action,
            confidence=confidence,
            reason="\n".join(f"- {item}" for item in reasons),
            entry=close_price,
            indicators={
                "close": round(close_price, 6),
                "ema_20": round(ema20, 6),
                "ma_50": round(ma50, 6),
                "ma_200": round(ma200, 6) if ma200 > 0 else 0.0,
                "rsi_14": round(rsi14, 4),
                "atr_14": round(atr14, 6),
                "atr_pct_14": round(atr_pct, 6),
                "adx_14": round(adx14, 4),
                "macd": round(macd, 6),
                "macd_signal": round(macd_signal, 6),
                "macd_diff": round(macd_diff, 6),
                "volume": round(volume, 6),
                "volume_ratio_20": round(volume_ratio, 4),
                "highest_high_20": round(highest_high, 6) if highest_high > 0 else 0.0,
                "lowest_low_20": round(lowest_low, 6) if lowest_low > 0 else 0.0,
                "setup_type": setup_type,
                "regime_slug": local_regime["slug"],
                "regime_label": local_regime["label"],
                "regime_tradable": local_regime["slug"] in {"trend", "breakout", "transition"},
                "higher_timeframe_aligned": higher_timeframe_bullish,
                "higher_timeframe_regime_slug": str(higher_context.get("regime_slug") or "unclassified"),
                "higher_timeframe_regime_label": higher_timeframe_label,
                "higher_timeframe_strength_score": round(symbol_strength_score, 2),
                "symbol_strength_score": round(symbol_strength_score, 2),
                "symbol_rank": symbol_rank,
                "trade_priority_allowed": trade_priority_allowed,
            },
        )

    @staticmethod
    def _float_value(row: pd.Series, key: str) -> float:
        value = row.get(key)
        return TrendRsiStrategy._coerce_float(value)

    @staticmethod
    def _rolling_high(df: pd.DataFrame, lookback: int) -> float:
        if len(df.index) <= 1:
            return 0.0
        window = df.tail(max(lookback + 1, 2)).iloc[:-1]
        return TrendRsiStrategy._coerce_float(window["high"].max())

    @staticmethod
    def _rolling_low(df: pd.DataFrame, lookback: int) -> float:
        if len(df.index) <= 1:
            return 0.0
        window = df.tail(max(lookback + 1, 2)).iloc[:-1]
        return TrendRsiStrategy._coerce_float(window["low"].min())

    @staticmethod
    def _coerce_float(value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        if pd.isna(numeric):
            return 0.0
        return numeric

    @staticmethod
    def _neutral_context(*, symbol: str, interval: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "interval": interval,
            "bullish": False,
            "regime_slug": "unclassified",
            "regime_label": "Unclassified",
            "strength_score": 0.0,
            "trade_priority_allowed": True,
            "symbol_rank": 0,
        }

    @staticmethod
    def _classify_regime(
        *,
        close_price: float,
        ema20: float,
        ma50: float,
        ma200: float,
        rsi: float,
        adx: float,
        atr_pct: float,
        volume_ratio: float,
        highest_high: float,
    ) -> dict[str, str]:
        if close_price <= 0 or ma50 <= 0:
            return {"slug": "unclassified", "label": "Unclassified"}

        breakout = (
            highest_high > 0
            and close_price > highest_high
            and volume_ratio >= 1.15
            and rsi >= 58
        )
        if breakout:
            return {"slug": "breakout", "label": "Breakout / expansion"}
        if adx < 18 and abs(rsi - 50) <= 6 and atr_pct < 0.009:
            return {"slug": "range", "label": "Range / indecision"}
        if close_price > ema20 >= ma50 >= ma200 > 0 and adx >= 18 and rsi >= 54:
            return {"slug": "trend", "label": "Trend / steady continuation"}
        if close_price < ema20 <= ma50 <= ma200 and adx >= 18 and rsi <= 45:
            return {"slug": "selloff", "label": "Selloff / bearish pressure"}
        if close_price > ma50 and rsi >= 50:
            return {"slug": "transition", "label": "Transition / constructive"}
        return {"slug": "mixed", "label": "Mixed / noisy tape"}

    @staticmethod
    def _score_strength(
        *,
        close_price: float,
        ema20: float,
        ma50: float,
        ma200: float,
        rsi: float,
        adx: float,
        volume_ratio: float,
        atr_pct: float,
        bullish: bool,
        macd_bullish: bool,
        regime_slug: str,
    ) -> float:
        if close_price <= 0 or ema20 <= 0 or ma50 <= 0:
            return 0.0

        score = 32.0
        score += min(max(((close_price - ema20) / ema20) * 100 * 3.0, 0.0), 8.0)
        score += min(max(((ma50 - ma200) / ma200) * 100 * 2.0, 0.0), 18.0) if ma200 > 0 else 0.0
        score += min(max((rsi - 50) * 0.7, 0.0), 12.0)
        score += min(max(adx - 18, 0.0) * 0.7, 12.0)
        score += min(max(volume_ratio - 1.0, 0.0) * 20.0, 10.0)
        if bullish:
            score += 8.0
        if macd_bullish:
            score += 5.0
        if regime_slug == "breakout":
            score += 8.0
        elif regime_slug == "trend":
            score += 5.0
        elif regime_slug == "range":
            score -= 12.0
        if atr_pct > 0.04:
            score -= 6.0
        return max(0.0, min(round(score, 2), 100.0))

    @staticmethod
    def _score_confidence(
        *,
        action: str,
        setup_type: str,
        close_price: float,
        ema20: float,
        ma50: float,
        ma200: float | None,
        rsi: float,
        adx: float,
        volume_ratio: float,
        macd_alignment: bool,
        local_regime_slug: str,
        higher_timeframe_bullish: bool,
        symbol_strength_score: float,
        trade_priority_allowed: bool,
    ) -> int:
        if action == "HOLD":
            return 32

        base = 54.0 if action == "SELL" else 58.0 if setup_type == "pullback" else 62.0
        distance_score = min(max(((close_price - ma50) / max(ma50, 1e-9)) * 100 * 3.0, 0.0), 10.0)
        ema_score = min(max(((close_price - ema20) / max(ema20, 1e-9)) * 100 * 2.5, 0.0), 8.0)
        momentum_score = min(abs(rsi - 50) * 0.55, 12.0)
        trend_score = 8.0 if ma200 is not None and ma50 > ma200 else 0.0
        adx_score = min(max(adx - 18, 0.0) * 0.6, 8.0)
        volume_score = min(max(volume_ratio - 1.0, 0.0) * 18.0, 10.0)
        strength_score = min(max(symbol_strength_score - 50.0, 0.0) * 0.25, 8.0)
        regime_score = 6.0 if local_regime_slug in {"trend", "breakout"} else -12.0 if local_regime_slug == "range" else 0.0
        htf_score = 8.0 if higher_timeframe_bullish else (-18.0 if action == "BUY" else 0.0)
        ranking_score = 6.0 if trade_priority_allowed else -12.0
        macd_score = 5.0 if macd_alignment else 0.0
        overextension_penalty = 6.0 if setup_type == "breakout" and rsi >= 74 else 0.0

        confidence = (
            base
            + distance_score
            + ema_score
            + momentum_score
            + trend_score
            + adx_score
            + volume_score
            + strength_score
            + regime_score
            + htf_score
            + ranking_score
            + macd_score
            - overextension_penalty
        )
        return max(0, min(int(round(confidence)), 95))
