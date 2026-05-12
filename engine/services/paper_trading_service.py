from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from engine.models import JournalEntry, PortfolioSnapshot, RiskAssessment, RiskProfile, Signal, TradeRecord


@dataclass(slots=True)
class PaperTradingService:
    starting_balance: float
    fee_rate: float
    slippage_bps: float
    min_order_notional: float
    logger: logging.Logger

    def build_open_trade(self, signal: Signal, assessment: RiskAssessment, *, available_cash: float) -> TradeRecord:
        if assessment.risk_profile is None:
            raise ValueError("A paper trade cannot be created without an accepted risk profile.")

        profile = assessment.risk_profile
        entry_fill_price = self._apply_slippage(profile.entry, side="buy")
        position_size, was_capped = self._fit_position_size(
            requested_size=profile.position_size,
            fill_price=entry_fill_price,
            available_cash=available_cash,
        )
        notional_value = entry_fill_price * position_size
        if notional_value < self.min_order_notional:
            raise ValueError("Paper trade notional fell below the minimum tradable amount after fee/slippage simulation.")

        entry_fee_paid = notional_value * self.fee_rate
        metadata = self._merge_metadata(
            self._build_entry_simulation_metadata(
                raw_entry=profile.entry,
                fill_entry=entry_fill_price,
                requested_position_size=profile.position_size,
                filled_position_size=position_size,
                entry_fee_paid=entry_fee_paid,
                available_cash=available_cash,
                was_capped=was_capped,
            ),
            self._build_management_metadata(profile, position_size),
        )
        notes = "Opened automatically in paper trading mode after a qualified setup with simulated fees and slippage."
        if was_capped:
            notes += " Position size was trimmed slightly so entry cost plus fees fit inside available cash."

        return TradeRecord(
            symbol=signal.symbol,
            interval=signal.interval,
            action=signal.action,
            execution_mode="paper",
            entry=round(entry_fill_price, 6),
            stop_loss=profile.stop_loss,
            take_profit=profile.take_profit,
            position_size=round(position_size, 6),
            confidence=signal.confidence,
            risk_reward_ratio=profile.risk_reward_ratio,
            notional_value=round(notional_value, 6),
            status="OPEN",
            notes=notes,
            metadata=metadata,
        )

    def build_pending_trade(self, signal: Signal, assessment: RiskAssessment, *, available_cash: float) -> TradeRecord:
        if assessment.risk_profile is None:
            raise ValueError("A pending paper trade cannot be created without an accepted risk profile.")

        profile = assessment.risk_profile
        entry_fill_price = self._apply_slippage(profile.entry, side="buy")
        position_size, was_capped = self._fit_position_size(
            requested_size=profile.position_size,
            fill_price=entry_fill_price,
            available_cash=available_cash,
        )
        notional_value = entry_fill_price * position_size
        if notional_value < self.min_order_notional:
            raise ValueError("Pending paper trade notional fell below the minimum tradable amount after fee/slippage simulation.")
        entry_fee_paid = notional_value * self.fee_rate

        return TradeRecord(
            symbol=signal.symbol,
            interval=signal.interval,
            action=signal.action,
            execution_mode="paper",
            entry=round(entry_fill_price, 6),
            stop_loss=profile.stop_loss,
            take_profit=profile.take_profit,
            position_size=round(position_size, 6),
            confidence=signal.confidence,
            risk_reward_ratio=profile.risk_reward_ratio,
            notional_value=round(notional_value, 6),
            status="PENDING_APPROVAL",
            notes="Awaiting manual approval before opening this paper trade.",
            metadata=self._merge_metadata(
                self._build_entry_simulation_metadata(
                    raw_entry=profile.entry,
                    fill_entry=entry_fill_price,
                    requested_position_size=profile.position_size,
                    filled_position_size=position_size,
                    entry_fee_paid=entry_fee_paid,
                    available_cash=available_cash,
                    was_capped=was_capped,
                ),
                self._build_management_metadata(profile, position_size),
            ),
        )

    def activate_pending_trade(self, trade: dict[str, Any]) -> dict[str, Any]:
        updated_trade = dict(trade)
        updated_trade.update(
            {
                "status": "OPEN",
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "notes": "Opened in paper trading mode after manual approval.",
            }
        )
        return updated_trade

    def refresh_pending_trade(self, trade: dict[str, Any], signal: Signal, assessment: RiskAssessment, *, available_cash: float) -> dict[str, Any]:
        if assessment.risk_profile is None:
            raise ValueError("A pending paper trade cannot be refreshed without an accepted risk profile.")

        profile = assessment.risk_profile
        entry_fill_price = self._apply_slippage(profile.entry, side="buy")
        position_size, was_capped = self._fit_position_size(
            requested_size=profile.position_size,
            fill_price=entry_fill_price,
            available_cash=available_cash,
        )
        notional_value = entry_fill_price * position_size
        if notional_value < self.min_order_notional:
            raise ValueError("Pending paper trade notional fell below the minimum tradable amount after fee/slippage simulation.")
        entry_fee_paid = notional_value * self.fee_rate
        updated_trade = dict(trade)
        updated_trade.update(
            {
                "entry": round(entry_fill_price, 6),
                "stop_loss": profile.stop_loss,
                "take_profit": profile.take_profit,
                "position_size": round(position_size, 6),
                "confidence": signal.confidence,
                "risk_reward_ratio": profile.risk_reward_ratio,
                "notional_value": round(notional_value, 6),
                "notes": (
                    "Awaiting manual approval before opening this paper trade."
                    + (" Position size was trimmed slightly so entry cost plus fees fit inside available cash." if was_capped else "")
                ),
                "metadata": self._merge_metadata(
                    self._merge_metadata(
                        trade.get("metadata"),
                        self._build_entry_simulation_metadata(
                            raw_entry=profile.entry,
                            fill_entry=entry_fill_price,
                            requested_position_size=profile.position_size,
                            filled_position_size=position_size,
                            entry_fee_paid=entry_fee_paid,
                            available_cash=available_cash,
                            was_capped=was_capped,
                        ),
                    ),
                    self._build_management_metadata(profile, position_size),
                ),
            }
        )
        return updated_trade

    def manage_open_trade(self, trade: dict[str, Any], latest_candle: pd.Series) -> dict[str, Any] | None:
        if str(trade.get("status") or "") != "OPEN":
            return None

        updated_trade = dict(trade)
        metadata = self._merge_metadata(updated_trade.get("metadata"), {})
        management = self._trade_management(updated_trade)
        high = float(latest_candle["high"])
        low = float(latest_candle["low"])
        current_stop = float(updated_trade.get("stop_loss") or 0)
        stretch_target = float(updated_trade.get("take_profit") or 0)
        highest_price = max(self._management_value(management, "highest_price"), high, float(updated_trade.get("entry") or 0))
        management["highest_price"] = round(highest_price, 6)

        trailing_active = bool(management.get("trailing_active"))
        if current_stop > 0 and low <= current_stop:
            note = (
                "Paper trade closed because the adaptive trailing stop was hit."
                if trailing_active
                else "Paper trade closed because the stop loss was hit."
            )
            return self.close_trade(updated_trade, exit_price=current_stop, note=note)

        changed = False
        partial_target = self._management_value(management, "partial_take_profit")
        partial_taken = bool(management.get("partial_exit_taken"))
        if not partial_taken and partial_target > 0 and high >= partial_target:
            updated_trade = self._apply_partial_take_profit(updated_trade, management, partial_target)
            metadata = self._merge_metadata(updated_trade.get("metadata"), {})
            management = self._trade_management(updated_trade)
            changed = True

        if not bool(management.get("trailing_active")):
            breakeven_trigger = self._management_value(management, "breakeven_trigger")
            if breakeven_trigger > 0 and high >= breakeven_trigger:
                management["trailing_active"] = True
                management["trailing_activated_at"] = datetime.now(timezone.utc).isoformat()
                changed = True

        if bool(management.get("trailing_active")):
            trailing_stop = self._trailing_stop_candidate(updated_trade, latest_candle, management)
            if trailing_stop > current_stop + 1e-9:
                updated_trade["stop_loss"] = round(trailing_stop, 6)
                management["last_trailing_stop"] = round(trailing_stop, 6)
                if "partial profit" not in str(updated_trade.get("notes") or "").lower():
                    updated_trade["notes"] = "Paper trade advanced its adaptive stop to protect unrealized gains."
                changed = True

        if stretch_target > 0 and high >= stretch_target:
            return self.close_trade(
                updated_trade,
                exit_price=stretch_target,
                note="Paper trade closed because the adaptive stretch target was reached.",
            )

        if not changed:
            return None

        metadata["trade_management"] = management
        updated_trade["metadata"] = metadata
        return updated_trade

    def close_trade(self, trade: dict[str, Any], *, exit_price: float, note: str) -> dict[str, Any]:
        entry = float(trade["entry"])
        position_size = float(trade["position_size"])
        executed_exit_price = self._apply_slippage(exit_price, side="sell")
        gross_pnl = (executed_exit_price - entry) * position_size
        remaining_entry_fee_paid = self._remaining_entry_fee_paid(trade)
        exit_fee_paid = executed_exit_price * position_size * self.fee_rate
        partial_realized_pnl = self._paper_simulation_value(trade, "partial_realized_pnl")
        remaining_leg_pnl = gross_pnl - remaining_entry_fee_paid - exit_fee_paid
        pnl = partial_realized_pnl + remaining_leg_pnl
        initial_notional = self._paper_simulation_value(trade, "initial_notional_value")
        if initial_notional <= 0:
            initial_notional = entry * max(self._paper_simulation_value(trade, "initial_filled_position_size"), position_size)
        pnl_pct = (pnl / initial_notional) * 100 if initial_notional > 0 else 0.0
        entry_fee_total = self._paper_simulation_value(trade, "entry_fee_paid_total")
        if entry_fee_total <= 0:
            entry_fee_total = self._paper_simulation_value(trade, "entry_fee_paid")
        partial_exit_fee_paid = self._paper_simulation_value(trade, "partial_exit_fee_paid")
        simulation_metadata = self._merge_metadata(
            trade.get("metadata"),
            {
                "paper_simulation": {
                    **self._paper_simulation(trade),
                    "exit_price_raw": round(exit_price, 6),
                    "exit_price_filled": round(executed_exit_price, 6),
                    "exit_slippage_bps": round(self.slippage_bps, 4),
                    "exit_fee_paid": round(exit_fee_paid, 6),
                    "gross_pnl": round(gross_pnl, 6),
                    "remaining_leg_pnl": round(remaining_leg_pnl, 6),
                    "partial_realized_pnl": round(partial_realized_pnl, 6),
                    "net_pnl": round(pnl, 6),
                    "total_fees_paid": round(entry_fee_total + partial_exit_fee_paid + exit_fee_paid, 6),
                    "closed_with_fee_and_slippage_model": True,
                }
            },
        )

        updated_trade = dict(trade)
        updated_trade.update(
            {
                "status": "CLOSED",
                "exit_price": round(executed_exit_price, 6),
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "pnl": round(pnl, 6),
                "pnl_pct": round(pnl_pct, 4),
                "notes": note,
                "metadata": simulation_metadata,
            }
        )
        return updated_trade

    def close_trade_if_levels_hit(self, trade: dict[str, Any], latest_candle: pd.Series) -> dict[str, Any] | None:
        updated_trade = self.manage_open_trade(trade, latest_candle)
        if updated_trade is None:
            return None
        return updated_trade if str(updated_trade.get("status") or "") == "CLOSED" else None

    def calculate_portfolio_snapshot(
        self,
        *,
        execution_mode: str,
        open_trades: list[dict[str, Any]],
        closed_trades: list[dict[str, Any]],
        latest_prices: dict[str, float],
    ) -> PortfolioSnapshot:
        total_realized_pnl = sum(float(trade.get("pnl") or 0) for trade in closed_trades)
        realized_pnl_today = sum(
            float(trade.get("pnl") or 0)
            for trade in closed_trades
            if self._is_today(trade.get("closed_at"))
        )
        partial_realized_open_pnl = sum(self._paper_simulation_value(trade, "partial_realized_pnl") for trade in open_trades)
        open_notional = sum(float(trade.get("entry") or 0) * float(trade.get("position_size") or 0) for trade in open_trades)
        open_entry_fees = sum(self._remaining_entry_fee_paid(trade) for trade in open_trades)
        unrealized_pnl = sum(
            self.mark_to_market_trade(
                trade,
                latest_prices.get(str(trade.get("symbol")), float(trade.get("entry") or 0)),
                include_partial_realized=False,
            )
            for trade in open_trades
        )

        available_cash = self.starting_balance + total_realized_pnl + partial_realized_open_pnl - open_notional - open_entry_fees
        total_equity = self.starting_balance + total_realized_pnl + partial_realized_open_pnl + unrealized_pnl
        wins = sum(1 for trade in closed_trades if float(trade.get("pnl") or 0) > 0)
        win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0

        return PortfolioSnapshot(
            execution_mode=execution_mode,  # type: ignore[arg-type]
            total_equity=round(total_equity, 6),
            available_cash=round(available_cash, 6),
            realized_pnl_today=round(realized_pnl_today, 6),
            total_realized_pnl=round(total_realized_pnl, 6),
            unrealized_pnl=round(unrealized_pnl + partial_realized_open_pnl, 6),
            open_positions=len(open_trades),
            win_rate=round(win_rate, 2),
        )

    @staticmethod
    def build_journal_entry(*, title: str, body: str, symbol: str | None = None, related_trade_id: str | None = None, tags: list[str] | None = None) -> JournalEntry:
        return JournalEntry(
            title=title,
            body=body,
            symbol=symbol,
            related_trade_id=related_trade_id,
            tags=tags or [],
        )

    def mark_to_market_trade(self, trade: dict[str, Any], latest_price: float, *, include_partial_realized: bool = True) -> float:
        entry = float(trade.get("entry") or 0)
        position_size = float(trade.get("position_size") or 0)
        if entry <= 0 or position_size <= 0:
            return 0.0

        gross_pnl = (latest_price - entry) * position_size
        entry_fee_paid = self._remaining_entry_fee_paid(trade)
        estimated_exit_fee = latest_price * position_size * self._paper_simulation_fee_rate(trade)
        pnl = gross_pnl - entry_fee_paid - estimated_exit_fee
        if include_partial_realized:
            pnl += self._paper_simulation_value(trade, "partial_realized_pnl")
        return round(pnl, 6)

    def estimate_exit_fee(self, trade: dict[str, Any], latest_price: float) -> float:
        position_size = float(trade.get("position_size") or 0)
        if latest_price <= 0 or position_size <= 0:
            return 0.0
        return round(latest_price * position_size * self._paper_simulation_fee_rate(trade), 6)

    def _fit_position_size(self, *, requested_size: float, fill_price: float, available_cash: float) -> tuple[float, bool]:
        total_cost_per_unit = fill_price * (1 + self.fee_rate)
        if total_cost_per_unit <= 0:
            raise ValueError("Paper trade fill price must be positive.")
        max_affordable_size = available_cash / total_cost_per_unit
        position_size = min(requested_size, max_affordable_size)
        if position_size <= 0:
            raise ValueError("Paper trade could not fit inside available cash after fee simulation.")
        return position_size, max_affordable_size < requested_size

    def _apply_partial_take_profit(self, trade: dict[str, Any], management: dict[str, Any], partial_target: float) -> dict[str, Any]:
        entry = float(trade.get("entry") or 0)
        current_size = float(trade.get("position_size") or 0)
        if entry <= 0 or current_size <= 0:
            return trade

        sold_size = current_size * 0.5
        remaining_size = current_size - sold_size
        updated_trade = dict(trade)
        metadata = self._merge_metadata(updated_trade.get("metadata"), {})
        simulation = self._paper_simulation(updated_trade)
        current_entry_fee = self._remaining_entry_fee_paid(updated_trade)

        if entry * remaining_size < self.min_order_notional:
            management["trailing_active"] = True
            management["partial_exit_taken"] = False
            management["partial_exit_skipped_due_to_min_notional"] = True
            management["highest_price"] = round(max(self._management_value(management, "highest_price"), partial_target), 6)
            updated_trade["stop_loss"] = round(max(float(updated_trade.get("stop_loss") or 0), entry), 6)
            updated_trade["notes"] = (
                "Paper trade reached the 1R management zone, but the position was too small for a clean partial exit. "
                "The stop moved to breakeven and adaptive trailing is now active."
            )
            metadata["trade_management"] = management
            updated_trade["metadata"] = metadata
            return updated_trade

        executed_exit_price = self._apply_slippage(partial_target, side="sell")
        allocated_entry_fee = current_entry_fee * (sold_size / current_size)
        remaining_entry_fee = current_entry_fee - allocated_entry_fee
        exit_fee_paid = executed_exit_price * sold_size * self.fee_rate
        gross_pnl = (executed_exit_price - entry) * sold_size
        existing_partial_realized = self._paper_simulation_value(updated_trade, "partial_realized_pnl")
        existing_partial_exit_fee = self._paper_simulation_value(updated_trade, "partial_exit_fee_paid")
        partial_realized_pnl = gross_pnl - allocated_entry_fee - exit_fee_paid

        simulation.update(
            {
                "partial_exit_taken": True,
                "partial_exit_price_raw": round(partial_target, 6),
                "partial_exit_price_filled": round(executed_exit_price, 6),
                "partial_exit_size": round(sold_size, 6),
                "partial_exit_fee_paid": round(existing_partial_exit_fee + exit_fee_paid, 6),
                "partial_exit_entry_fee_allocated": round(allocated_entry_fee, 6),
                "partial_realized_pnl": round(existing_partial_realized + partial_realized_pnl, 6),
                "remaining_entry_fee_paid": round(remaining_entry_fee, 6),
                "current_position_size": round(remaining_size, 6),
            }
        )

        management.update(
            {
                "partial_exit_taken": True,
                "partial_exit_timestamp": datetime.now(timezone.utc).isoformat(),
                "partial_exit_size": round(sold_size, 6),
                "partial_exit_rr": round(self._management_value(management, "partial_profit_rr"), 4),
                "trailing_active": True,
                "highest_price": round(max(self._management_value(management, "highest_price"), executed_exit_price), 6),
            }
        )

        updated_trade.update(
            {
                "position_size": round(remaining_size, 6),
                "notional_value": round(entry * remaining_size, 6),
                "stop_loss": round(max(float(updated_trade.get("stop_loss") or 0), entry), 6),
                "notes": "Paper trade locked a partial profit at 1R and moved the stop to breakeven for the runner.",
                "metadata": {
                    **metadata,
                    "paper_simulation": simulation,
                    "trade_management": management,
                },
            }
        )
        return updated_trade

    def _trailing_stop_candidate(self, trade: dict[str, Any], latest_candle: pd.Series, management: dict[str, Any]) -> float:
        current_stop = float(trade.get("stop_loss") or 0)
        entry = float(trade.get("entry") or 0)
        ema20 = self._series_number(latest_candle, "ema_20")
        atr14 = self._series_number(latest_candle, "atr_14")
        highest_price = max(self._management_value(management, "highest_price"), self._series_number(latest_candle, "high"), entry)
        trailing_buffer = self._management_value(management, "trailing_stop_buffer")
        if trailing_buffer <= 0:
            trailing_buffer = atr14 if atr14 > 0 else max(entry * 0.005, 0.01)

        price_trail = highest_price - trailing_buffer
        ema_trail = ema20 - (atr14 * 0.35) if ema20 > 0 and atr14 > 0 else price_trail
        candidate = max(current_stop, entry, price_trail, ema_trail)
        return round(candidate, 6)

    def _apply_slippage(self, price: float, *, side: str) -> float:
        slippage_multiplier = self.slippage_bps / 10_000
        if side == "buy":
            return round(price * (1 + slippage_multiplier), 6)
        return round(price * max(1 - slippage_multiplier, 0), 6)

    def _build_entry_simulation_metadata(
        self,
        *,
        raw_entry: float,
        fill_entry: float,
        requested_position_size: float,
        filled_position_size: float,
        entry_fee_paid: float,
        available_cash: float,
        was_capped: bool,
    ) -> dict[str, Any]:
        notional_value = fill_entry * filled_position_size
        return {
            "paper_simulation": {
                "fee_rate": round(self.fee_rate, 6),
                "slippage_bps": round(self.slippage_bps, 4),
                "entry_price_raw": round(raw_entry, 6),
                "entry_price_filled": round(fill_entry, 6),
                "requested_position_size": round(requested_position_size, 6),
                "filled_position_size": round(filled_position_size, 6),
                "initial_filled_position_size": round(filled_position_size, 6),
                "entry_fee_paid": round(entry_fee_paid, 6),
                "entry_fee_paid_total": round(entry_fee_paid, 6),
                "remaining_entry_fee_paid": round(entry_fee_paid, 6),
                "notional_value": round(notional_value, 6),
                "initial_notional_value": round(notional_value, 6),
                "total_entry_cost": round(notional_value + entry_fee_paid, 6),
                "available_cash_at_entry": round(available_cash, 6),
                "size_capped_for_cash": was_capped,
                "partial_exit_taken": False,
                "partial_realized_pnl": 0.0,
                "partial_exit_fee_paid": 0.0,
                "model": "adaptive_runner_plus_spot_fees",
            }
        }

    def _build_management_metadata(self, profile: RiskProfile, position_size: float) -> dict[str, Any]:
        return {
            "trade_management": {
                "style": profile.exit_style,
                "setup_type": profile.setup_type or "none",
                "stop_source": profile.stop_source,
                "partial_profit_rr": round(
                    ((profile.partial_take_profit or profile.entry) - profile.entry) / max(profile.entry - profile.stop_loss, 1e-9),
                    4,
                )
                if profile.partial_take_profit is not None
                else 0.0,
                "partial_take_profit": round(profile.partial_take_profit, 6) if profile.partial_take_profit is not None else None,
                "breakeven_trigger": round(profile.breakeven_trigger, 6) if profile.breakeven_trigger is not None else None,
                "trailing_stop_buffer": round(profile.trailing_stop_buffer, 6) if profile.trailing_stop_buffer is not None else None,
                "atr_reference": round(profile.atr_reference, 6) if profile.atr_reference is not None else None,
                "initial_entry": round(profile.entry, 6),
                "initial_stop_loss": round(profile.stop_loss, 6),
                "initial_take_profit": round(profile.take_profit, 6),
                "initial_position_size": round(position_size, 6),
                "highest_price": round(profile.entry, 6),
                "trailing_active": False,
                "partial_exit_taken": False,
            }
        }

    @staticmethod
    def _merge_metadata(existing: Any, updates: dict[str, Any]) -> dict[str, Any]:
        base = dict(existing) if isinstance(existing, dict) else {}
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merged_nested = dict(base[key])
                merged_nested.update(value)
                base[key] = merged_nested
            else:
                base[key] = value
        return base

    @staticmethod
    def _paper_simulation(trade: dict[str, Any]) -> dict[str, Any]:
        metadata = trade.get("metadata")
        if not isinstance(metadata, dict):
            return {}
        paper_simulation = metadata.get("paper_simulation")
        return dict(paper_simulation) if isinstance(paper_simulation, dict) else {}

    @staticmethod
    def _trade_management(trade: dict[str, Any]) -> dict[str, Any]:
        metadata = trade.get("metadata")
        if not isinstance(metadata, dict):
            return {}
        trade_management = metadata.get("trade_management")
        return dict(trade_management) if isinstance(trade_management, dict) else {}

    def _paper_simulation_fee_rate(self, trade: dict[str, Any]) -> float:
        stored_rate = self._paper_simulation_value(trade, "fee_rate")
        return stored_rate if stored_rate > 0 else self.fee_rate

    def _remaining_entry_fee_paid(self, trade: dict[str, Any]) -> float:
        remaining_entry_fee = self._paper_simulation_value(trade, "remaining_entry_fee_paid")
        if remaining_entry_fee > 0:
            return remaining_entry_fee
        return self._paper_simulation_value(trade, "entry_fee_paid")

    def _paper_simulation_value(self, trade: dict[str, Any], key: str) -> float:
        numeric = self._paper_simulation(trade).get(key)
        try:
            return float(numeric)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _management_value(management: dict[str, Any], key: str) -> float:
        numeric = management.get(key)
        try:
            return float(numeric)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _series_number(candle: pd.Series, key: str) -> float:
        try:
            value = float(candle.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if pd.isna(value) else value

    @staticmethod
    def _is_today(value: object) -> bool:
        if not value:
            return False
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
        return timestamp.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()
