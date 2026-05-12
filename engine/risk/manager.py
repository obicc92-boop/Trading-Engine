from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from engine.models import RiskAssessment, RiskProfile, Signal


class RiskManagementError(RuntimeError):
    """Raised when trade levels or risk eligibility cannot be derived safely."""


@dataclass(slots=True)
class RiskManager:
    risk_per_trade: float = 0.01
    daily_loss_limit: float = 0.03
    swing_lookback: int = 20
    risk_reward_ratio: float = 2.0
    stop_atr_multiplier: float = 1.35
    trail_atr_multiplier: float = 1.75
    partial_profit_rr: float = 1.0
    symbol_top_n: int = 2
    confidence_threshold: int = 70
    max_volatility_pct: float = 0.03
    max_open_trades: int = 3
    min_order_notional: float = 10.0
    logger: logging.Logger | None = None

    def evaluate_signal(
        self,
        df: pd.DataFrame,
        signal: Signal,
        *,
        total_equity: float,
        available_cash: float,
        daily_realized_pnl: float,
        open_trade: dict[str, object] | None,
        open_trades_count: int,
    ) -> RiskAssessment:
        if df.empty:
            raise RiskManagementError("Cannot evaluate risk on an empty market frame.")

        volatility_pct = self._estimate_volatility(df)
        daily_loss_used_pct = abs(min(daily_realized_pnl, 0.0)) / max(total_equity, 1e-9)

        if signal.action == "HOLD":
            return RiskAssessment(
                accepted=False,
                execution_intent="NO_ACTION",
                rejection_reasons=["Strategy returned HOLD, so no trade should be considered."],
                market_volatility_pct=volatility_pct,
                daily_loss_used_pct=daily_loss_used_pct,
            )

        if signal.action == "SELL":
            if open_trade is None:
                return RiskAssessment(
                    accepted=False,
                    execution_intent="NO_ACTION",
                    rejection_reasons=[
                        "Spot mode is long-only in this build, so SELL is treated as an exit signal rather than a short entry."
                    ],
                    market_volatility_pct=volatility_pct,
                    daily_loss_used_pct=daily_loss_used_pct,
                )

            warnings: list[str] = []
            if signal.confidence < self.confidence_threshold:
                warnings.append("SELL signal confidence is below the entry threshold, but exits remain allowed for capital protection.")

            return RiskAssessment(
                accepted=True,
                execution_intent="CLOSE_LONG",
                warnings=warnings,
                market_volatility_pct=volatility_pct,
                daily_loss_used_pct=daily_loss_used_pct,
            )

        rejection_reasons: list[str] = []
        warnings: list[str] = []
        setup_type = self._indicator_text(signal, "setup_type")
        trade_priority_allowed = self._indicator_flag(signal, "trade_priority_allowed", default=True)
        higher_timeframe_aligned = self._indicator_flag(signal, "higher_timeframe_aligned", default=True)
        regime_tradable = self._indicator_flag(signal, "regime_tradable", default=True)
        symbol_rank = int(round(self._indicator_number(signal, "symbol_rank")))

        if signal.confidence < self.confidence_threshold:
            rejection_reasons.append(
                f"Signal confidence {signal.confidence}% is below the required threshold of {self.confidence_threshold}%."
            )
        if setup_type == "none":
            rejection_reasons.append("The strategy did not classify this setup as a clean breakout or pullback entry.")
        if not higher_timeframe_aligned:
            rejection_reasons.append(
                f"Higher-timeframe filter rejected this long: {signal.symbol} is not aligned with the configured bullish trend bias."
            )
        if not trade_priority_allowed:
            rank_text = f"#{symbol_rank}" if symbol_rank > 0 else "outside the ranked list"
            rejection_reasons.append(
                f"Symbol-strength ranking skipped this setup: {signal.symbol} is {rank_text} and only the top {self.symbol_top_n} symbols are tradable."
            )
        if not regime_tradable:
            rejection_reasons.append(
                "Market regime filter rejected this setup because the tape looks too rangy or unstable for this strategy."
            )
        if daily_loss_used_pct >= self.daily_loss_limit:
            rejection_reasons.append(
                f"Daily loss limit reached: {daily_loss_used_pct:.2%} used versus the {self.daily_loss_limit:.2%} cap."
            )
        if volatility_pct >= self.max_volatility_pct:
            rejection_reasons.append(
                f"Market instability detected: recent average candle range is {volatility_pct:.2%}, above the {self.max_volatility_pct:.2%} threshold."
            )
        if open_trade is not None:
            rejection_reasons.append("A spot position is already open for this symbol and interval.")
        if open_trades_count >= self.max_open_trades:
            rejection_reasons.append(
                f"Open trade limit reached: {open_trades_count} open positions versus a {self.max_open_trades} position cap."
            )
        if available_cash < self.min_order_notional:
            rejection_reasons.append(
                f"Available cash {available_cash:.2f} is below the minimum tradable notional of {self.min_order_notional:.2f}."
            )

        risk_profile: RiskProfile | None = None
        if not rejection_reasons:
            risk_profile, was_capped = self.calculate_long_trade_levels(
                df=df,
                signal=signal,
                entry=signal.entry or float(df.iloc[-1]["close"]),
                total_equity=total_equity,
                available_cash=available_cash,
            )
            if risk_profile.notional_value < self.min_order_notional:
                rejection_reasons.append(
                    f"Calculated notional {risk_profile.notional_value:.2f} is below the minimum tradable notional of {self.min_order_notional:.2f}."
                )
                risk_profile = None
            elif was_capped:
                warnings.append("Position size was capped by available cash, reducing risk below the nominal 1% budget.")

        return RiskAssessment(
            accepted=not rejection_reasons,
            execution_intent="OPEN_LONG" if not rejection_reasons else "NO_ACTION",
            rejection_reasons=rejection_reasons,
            warnings=warnings,
            market_volatility_pct=volatility_pct,
            daily_loss_used_pct=daily_loss_used_pct,
            risk_profile=risk_profile,
        )

    def calculate_long_trade_levels(
        self,
        *,
        df: pd.DataFrame,
        signal: Signal,
        entry: float,
        total_equity: float,
        available_cash: float,
    ) -> tuple[RiskProfile, bool]:
        setup_type = self._indicator_text(signal, "setup_type")
        atr_value = self._indicator_number(signal, "atr_14")
        lookback = 12 if setup_type == "breakout" else self.swing_lookback
        recent_window = df.tail(lookback)
        swing_stop = float(recent_window["low"].min())
        atr_multiplier = self.stop_atr_multiplier
        if setup_type == "breakout":
            atr_multiplier *= 0.95
        elif setup_type == "pullback":
            atr_multiplier *= 1.1

        atr_stop = entry - (atr_value * atr_multiplier) if atr_value > 0 else None
        stop_candidates = [swing_stop]
        if atr_stop is not None and atr_stop > 0:
            stop_candidates.append(float(atr_stop))
        stop_loss = max(stop_candidates)
        if stop_loss >= entry:
            stop_loss = entry * 0.995

        price_risk = entry - stop_loss
        if price_risk <= 0:
            raise RiskManagementError("Derived stop loss does not create a positive risk distance.")

        risk_amount = total_equity * self.risk_per_trade
        raw_position_size = risk_amount / price_risk
        max_affordable_size = available_cash / entry
        position_size = min(raw_position_size, max_affordable_size)

        if position_size <= 0:
            raise RiskManagementError("Calculated position size is not tradable.")

        target_rr = self.risk_reward_ratio
        if setup_type == "breakout":
            target_rr = max(target_rr, 3.0)
        elif setup_type == "pullback":
            target_rr = max(target_rr, 2.5)

        partial_take_profit = entry + (price_risk * self.partial_profit_rr)
        take_profit = entry + (price_risk * target_rr)
        estimated_loss = price_risk * position_size
        estimated_reward = (take_profit - entry) * position_size
        notional_value = entry * position_size

        return (
            RiskProfile(
                entry=round(entry, 6),
                stop_loss=round(stop_loss, 6),
                take_profit=round(take_profit, 6),
                position_size=round(position_size, 6),
                risk_reward_ratio=round(target_rr, 4),
                risk_amount=round(risk_amount, 6),
                estimated_loss=round(estimated_loss, 6),
                estimated_reward=round(estimated_reward, 6),
                notional_value=round(notional_value, 6),
                partial_take_profit=round(partial_take_profit, 6),
                breakeven_trigger=round(partial_take_profit, 6),
                trailing_stop_buffer=round(atr_value * self.trail_atr_multiplier, 6) if atr_value > 0 else round(price_risk, 6),
                atr_reference=round(atr_value, 6) if atr_value > 0 else None,
                stop_source="max_of_swing_and_atr" if atr_value > 0 else "swing_fallback",
                setup_type=setup_type or None,
                exit_style="adaptive",
            ),
            max_affordable_size < raw_position_size,
        )

    @staticmethod
    def _estimate_volatility(df: pd.DataFrame, window: int = 20) -> float:
        recent = df.tail(window)
        if recent.empty:
            return 0.0
        candle_ranges = (recent["high"] - recent["low"]).div(recent["close"].replace(0, pd.NA))
        return float(candle_ranges.fillna(0).mean())

    @staticmethod
    def _indicator_flag(signal: Signal, key: str, *, default: bool) -> bool:
        value = signal.indicators.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return bool(value)

    @staticmethod
    def _indicator_number(signal: Signal, key: str) -> float:
        value = signal.indicators.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _indicator_text(signal: Signal, key: str) -> str:
        value = signal.indicators.get(key)
        if value is None:
            return ""
        return str(value).strip().lower()
