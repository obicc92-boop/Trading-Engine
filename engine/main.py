from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from engine.data.market_data import MarketDataError, MarketDataService
from engine.indicators.technical import apply_core_indicators
from engine.models import PortfolioSnapshot, RiskAssessment, RiskProfile, Signal, TradeRecord
from engine.risk.manager import RiskManagementError, RiskManager
from engine.services.ai_coach_service import AICoachService
from engine.services.bybit_service import BybitAPIError, BybitService
from engine.services.paper_trading_service import PaperTradingService
from engine.services.supabase_service import SupabaseService
from engine.services.telegram_service import TelegramService
from engine.strategies.base import BaseStrategy
from engine.strategies.trend_rsi import TrendRsiStrategy
from engine.utils.config import AppConfig, load_config
from engine.utils.logging_config import configure_logging


LIVE_PENDING_CONFIRMATION_STATUS = "PENDING_CONFIRMATION"
LIVE_ENTRY_SUBMITTED_STATUS = "ENTRY_SUBMITTED"
LIVE_EXIT_SUBMITTED_STATUS = "EXIT_SUBMITTED"
LIVE_ACTIVE_STATUSES = {LIVE_PENDING_CONFIRMATION_STATUS, LIVE_ENTRY_SUBMITTED_STATUS, "OPEN", LIVE_EXIT_SUBMITTED_STATUS}
LIVE_RISK_BLOCKING_STATUSES = {LIVE_ENTRY_SUBMITTED_STATUS, "OPEN", LIVE_EXIT_SUBMITTED_STATUS}
LIVE_FILLED_ORDER_STATUSES = {"filled"}
LIVE_PENDING_ORDER_STATUSES = {"created", "new", "partiallyfilled", "untriggered"}
LIVE_FAILED_ORDER_STATUSES = {"cancelled", "rejected", "deactivated"}


@dataclass(slots=True)
class ExecutionLaneEvaluation:
    mode: str
    signal: Signal
    assessment: RiskAssessment
    portfolio_snapshot: PortfolioSnapshot
    trades: list[dict[str, Any]]
    closed_trades: list[dict[str, Any]]
    active_trade: dict[str, Any] | None
    open_trade: dict[str, Any] | None
    pending_confirmation_trade: dict[str, Any] | None = None


@dataclass(slots=True)
class TradingEngine:
    config: AppConfig
    logger: logging.Logger
    strategy: BaseStrategy
    bybit_service: BybitService = field(init=False)
    data_service: MarketDataService = field(init=False)
    risk_manager: RiskManager = field(init=False)
    supabase_service: SupabaseService = field(init=False)
    telegram_service: TelegramService = field(init=False)
    ai_coach_service: AICoachService = field(init=False)
    paper_trading_service: PaperTradingService = field(init=False)
    last_signals: dict[str, str] = field(default_factory=dict, init=False)
    telegram_commands_synced: bool = field(default=False, init=False)
    latest_portfolio_snapshot: PortfolioSnapshot | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.bybit_service = BybitService(config=self.config, logger=self.logger)
        self.data_service = MarketDataService(
            config=self.config,
            bybit_service=self.bybit_service,
            logger=self.logger,
        )
        self.risk_manager = RiskManager(
            risk_per_trade=self.config.risk_per_trade,
            daily_loss_limit=self.config.daily_loss_limit,
            swing_lookback=self.config.swing_lookback,
            risk_reward_ratio=self.config.risk_reward_ratio,
            stop_atr_multiplier=self.config.stop_atr_multiplier,
            trail_atr_multiplier=self.config.trail_atr_multiplier,
            partial_profit_rr=self.config.partial_profit_rr,
            symbol_top_n=self.config.symbol_top_n,
            confidence_threshold=self.config.confidence_threshold,
            max_volatility_pct=self.config.max_volatility_pct,
            max_open_trades=self.config.max_open_trades,
            min_order_notional=self.config.min_order_notional,
            logger=self.logger,
        )
        self.supabase_service = SupabaseService(
            url=self.config.supabase_url,
            key=self.config.supabase_key,
            logger=self.logger,
        )
        self.telegram_service = TelegramService(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
            logger=self.logger,
            timeout_seconds=self.config.request_timeout_seconds,
            allowed_user_id=self.config.telegram_allowed_user_id,
        )
        self.ai_coach_service = AICoachService(
            api_key=self.config.anthropic_api_key,
            model=self.config.anthropic_model,
            enabled=self.config.ai_coach_enabled,
            max_tokens=self.config.ai_coach_max_tokens,
            timeout_seconds=self.config.request_timeout_seconds,
            logger=self.logger,
        )
        self.paper_trading_service = PaperTradingService(
            starting_balance=self.config.paper_starting_balance,
            fee_rate=self.config.paper_fee_rate,
            slippage_bps=self.config.paper_slippage_bps,
            min_order_notional=self.config.min_order_notional,
            logger=self.logger,
        )

    def run(self) -> None:
        self.logger.info(
            "Starting trading intelligence loop | mode=%s | paper_lane=%s | live_lane=%s | testnet=%s | symbols=%s",
            self.config.execution_mode,
            self.config.paper_trading_enabled,
            self.config.live_trading_enabled,
            self.config.bybit_use_testnet,
            ",".join(self.config.traded_symbols),
        )
        self.logger.info("Using %s market data provider.", self.config.market_data_provider)
        self._poll_telegram_updates()
        while True:
            started_at = time.monotonic()
            self.run_cycle()
            elapsed = time.monotonic() - started_at
            sleep_seconds = max(self.config.poll_interval_seconds - elapsed, 0)
            if sleep_seconds <= 0:
                continue

            if not self.config.telegram_polling_enabled:
                time.sleep(sleep_seconds)
                continue

            sleep_deadline = time.monotonic() + sleep_seconds
            while True:
                remaining = sleep_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._poll_telegram_updates()
                time.sleep(min(2.0, remaining))

    def run_cycle(self) -> None:
        execution_modes = self._enabled_execution_modes()
        all_trades = self.supabase_service.get_recent_trades(limit=500)
        trades_by_mode: dict[str, list[dict[str, Any]]] = {
            mode: [trade for trade in all_trades if str(trade.get("execution_mode") or "") == mode]
            for mode in execution_modes
        }
        closed_trades_by_mode: dict[str, list[dict[str, Any]]] = {
            mode: self._filter_trades(mode_trades, {"CLOSED"})
            for mode, mode_trades in trades_by_mode.items()
        }

        if "live" in execution_modes:
            live_trades = trades_by_mode.get("live", [])
            live_closed_trades = closed_trades_by_mode.get("live", [])
            self._reconcile_live_orders(live_trades, live_closed_trades)
            refreshed_live_trades = self.supabase_service.get_recent_trades(limit=500, execution_mode="live")
            trades_by_mode["live"] = refreshed_live_trades
            closed_trades_by_mode["live"] = self._filter_trades(refreshed_live_trades, {"CLOSED"})

        latest_prices: dict[str, float] = {}
        symbol_market_contexts = self._build_symbol_market_contexts()

        for symbol in self.config.traded_symbols:
            for interval in self.config.market_intervals:
                try:
                    market_frame = self.data_service.get_crypto_data(symbol, interval, self.config.market_data_limit)
                    indicator_frame = apply_core_indicators(market_frame)
                    latest_candle = indicator_frame.iloc[-1]
                    latest_prices[symbol] = float(latest_candle["close"])
                    lane_snapshots: dict[str, PortfolioSnapshot] = {}

                    if "paper" in execution_modes:
                        paper_trades = trades_by_mode.get("paper", [])
                        paper_closed_trades = closed_trades_by_mode.get("paper", [])
                        maybe_closed_paper_trade = self._sync_open_trade_levels(
                            paper_trades,
                            paper_closed_trades,
                            symbol,
                            interval,
                            latest_candle,
                        )
                        if maybe_closed_paper_trade:
                            latest_prices[symbol] = float(maybe_closed_paper_trade.get("exit_price") or latest_prices[symbol])

                        paper_snapshot = self._build_lane_portfolio_snapshot(
                            mode="paper",
                            mode_trades=paper_trades,
                            closed_trades=paper_closed_trades,
                            latest_prices=latest_prices,
                        )
                        lane_snapshots["paper"] = paper_snapshot

                        if maybe_closed_paper_trade and str(maybe_closed_paper_trade.get("status")) == "CLOSED":
                            self._save_post_close_coach_note(
                                mode="paper",
                                closed_trade=maybe_closed_paper_trade,
                                mode_trades=paper_trades,
                                portfolio_snapshot=paper_snapshot,
                            )

                    if "live" in execution_modes:
                        live_trades = trades_by_mode.get("live", [])
                        live_closed_trades = closed_trades_by_mode.get("live", [])
                        maybe_closed_live_trade = self._sync_live_open_trade_levels(
                            live_trades,
                            live_closed_trades,
                            symbol,
                            interval,
                            latest_candle,
                        )
                        if maybe_closed_live_trade:
                            latest_prices[symbol] = float(maybe_closed_live_trade.get("exit_price") or latest_prices[symbol])

                        live_snapshot = self._build_lane_portfolio_snapshot(
                            mode="live",
                            mode_trades=live_trades,
                            closed_trades=live_closed_trades,
                            latest_prices=latest_prices,
                        )
                        lane_snapshots["live"] = live_snapshot

                        if maybe_closed_live_trade and str(maybe_closed_live_trade.get("status")) == "CLOSED":
                            self._save_post_close_coach_note(
                                mode="live",
                                closed_trade=maybe_closed_live_trade,
                                mode_trades=live_trades,
                                portfolio_snapshot=live_snapshot,
                            )

                    if "manual" in execution_modes:
                        lane_snapshots["manual"] = self._build_lane_portfolio_snapshot(
                            mode="manual",
                            mode_trades=trades_by_mode.get("manual", []),
                            closed_trades=closed_trades_by_mode.get("manual", []),
                            latest_prices=latest_prices,
                        )

                    base_signal = self.strategy.run_strategy(
                        indicator_frame,
                        symbol=symbol,
                        interval=interval,
                        market="spot",
                        higher_timeframe_context=symbol_market_contexts.get(symbol),
                    )

                    evaluations = [
                        self._evaluate_execution_lane(
                            mode=mode,
                            base_signal=base_signal,
                            indicator_frame=indicator_frame,
                            mode_trades=trades_by_mode.get(mode, []),
                            closed_trades=closed_trades_by_mode.get(mode, []),
                            portfolio_snapshot=lane_snapshots[mode],
                        )
                        for mode in execution_modes
                    ]

                    preferred_signal_mode = self._preferred_signal_mode(execution_modes)
                    primary_evaluation = next(
                        (evaluation for evaluation in evaluations if evaluation.mode == preferred_signal_mode),
                        evaluations[0],
                    )
                    saved_signal = self.supabase_service.save_signal(primary_evaluation.signal.to_dict())
                    signal_changed_by_mode = {
                        evaluation.mode: self._signal_changed(evaluation.mode, evaluation.signal)
                        for evaluation in evaluations
                    }
                    entry_evaluations = [
                        evaluation
                        for evaluation in evaluations
                        if signal_changed_by_mode[evaluation.mode]
                        and evaluation.assessment.accepted
                        and evaluation.assessment.execution_intent == "OPEN_LONG"
                        and evaluation.active_trade is None
                    ]
                    trade_group_id = uuid4().hex if entry_evaluations else None

                    for evaluation in evaluations:
                        if evaluation.pending_confirmation_trade is not None:
                            self._sync_pending_live_confirmation(
                                signal=evaluation.signal,
                                assessment=evaluation.assessment,
                                trade=evaluation.pending_confirmation_trade,
                                mode_trades=evaluation.trades,
                            )

                    created_entries: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
                    for evaluation in evaluations:
                        changed = signal_changed_by_mode[evaluation.mode]
                        if evaluation.assessment.accepted and changed:
                            if evaluation.assessment.execution_intent == "OPEN_LONG" and evaluation.active_trade is None:
                                saved_trade = self._handle_long_entry(
                                    evaluation.signal,
                                    evaluation.assessment,
                                    mode=evaluation.mode,
                                    mode_trades=evaluation.trades,
                                    portfolio_snapshot=evaluation.portfolio_snapshot,
                                    source_signal_record=saved_signal,
                                    trade_group_id=trade_group_id,
                                )
                                if saved_trade is not None:
                                    created_entries.append((saved_trade, evaluation.trades))
                            elif evaluation.assessment.execution_intent == "CLOSE_LONG" and evaluation.open_trade is not None:
                                self._handle_signal_exit(
                                    mode=evaluation.mode,
                                    signal=evaluation.signal,
                                    trade=evaluation.open_trade,
                                    latest_price=latest_prices[symbol],
                                    mode_trades=evaluation.trades,
                                    closed_trades=evaluation.closed_trades,
                                )

                        self.logger.info(
                            "Signal generated | lane=%s symbol=%s interval=%s action=%s confidence=%s eligible=%s intent=%s",
                            evaluation.mode,
                            evaluation.signal.symbol,
                            evaluation.signal.interval,
                            evaluation.signal.action,
                            evaluation.signal.confidence,
                            evaluation.signal.eligible,
                            evaluation.signal.execution_intent,
                        )

                    if len(created_entries) >= 2 and trade_group_id is not None:
                        self._link_paired_trades(created_entries, trade_group_id)
                except (MarketDataError, RiskManagementError, BybitAPIError, ValueError) as exc:
                    self.logger.error("Processing failed for %s %s: %s", symbol, interval, exc)
                except Exception as exc:  # pragma: no cover - defensive runtime guard.
                    self.logger.exception("Unexpected failure for %s %s: %s", symbol, interval, exc)

        final_snapshots: dict[str, PortfolioSnapshot] = {}
        for mode in execution_modes:
            final_snapshot = self._build_lane_portfolio_snapshot(
                mode=mode,
                mode_trades=trades_by_mode.get(mode, []),
                closed_trades=closed_trades_by_mode.get(mode, []),
                latest_prices=latest_prices,
            )
            final_snapshots[mode] = final_snapshot
            self.supabase_service.save_performance_log(final_snapshot.to_dict())

        self.latest_portfolio_snapshot = (
            final_snapshots.get("paper")
            or final_snapshots.get("live")
            or next(iter(final_snapshots.values()), None)
        )
        self._poll_telegram_updates()

    def _evaluate_execution_lane(
        self,
        *,
        mode: str,
        base_signal: Signal,
        indicator_frame: pd.DataFrame,
        mode_trades: list[dict[str, Any]],
        closed_trades: list[dict[str, Any]],
        portfolio_snapshot: PortfolioSnapshot,
    ) -> ExecutionLaneEvaluation:
        signal = replace(base_signal)
        active_trade = self._find_trade(
            mode_trades,
            symbol=signal.symbol,
            interval=signal.interval,
            statuses=self._active_trade_statuses(mode),
        )
        blocking_trade = self._find_trade(
            mode_trades,
            symbol=signal.symbol,
            interval=signal.interval,
            statuses=self._risk_blocking_trade_statuses(mode),
        )
        open_trade = self._find_trade(mode_trades, symbol=signal.symbol, interval=signal.interval, statuses={"OPEN"})
        pending_confirmation_trade = (
            self._find_trade(
                mode_trades,
                symbol=signal.symbol,
                interval=signal.interval,
                statuses={LIVE_PENDING_CONFIRMATION_STATUS},
            )
            if mode == "live"
            else None
        )

        assessment = self.risk_manager.evaluate_signal(
            indicator_frame,
            signal,
            total_equity=portfolio_snapshot.total_equity,
            available_cash=portfolio_snapshot.available_cash,
            daily_realized_pnl=portfolio_snapshot.realized_pnl_today,
            open_trade=blocking_trade,
            open_trades_count=self._risk_open_trade_count(mode, mode_trades),
        )
        if mode == "live":
            assessment = self._apply_live_safety_overrides(assessment, mode_trades)

        signal.apply_assessment(assessment)
        return ExecutionLaneEvaluation(
            mode=mode,
            signal=signal,
            assessment=assessment,
            portfolio_snapshot=portfolio_snapshot,
            trades=mode_trades,
            closed_trades=closed_trades,
            active_trade=active_trade,
            open_trade=open_trade,
            pending_confirmation_trade=pending_confirmation_trade,
        )

    def _handle_long_entry(
        self,
        signal: Signal,
        assessment: RiskAssessment,
        *,
        mode: str,
        mode_trades: list[dict[str, Any]],
        portfolio_snapshot: PortfolioSnapshot,
        source_signal_record: dict[str, Any] | None = None,
        trade_group_id: str | None = None,
    ) -> dict[str, Any] | None:
        signal_context_metadata = {
            **self._build_signal_context_metadata(signal),
            **(self._build_trade_management_context(assessment.risk_profile) if assessment.risk_profile is not None else {}),
            **self._build_trade_linkage_metadata(
                mode=mode,
                signal=signal,
                source_signal_record=source_signal_record,
                trade_group_id=trade_group_id,
            ),
        }

        if mode == "paper":
            trade = self.paper_trading_service.build_open_trade(
                signal,
                assessment,
                available_cash=portfolio_snapshot.available_cash,
            )
            trade.metadata = {
                **(trade.metadata or {}),
                **signal_context_metadata,
            }
            saved_trade = self.supabase_service.save_trade(trade.to_dict())
            if saved_trade is None:
                return None

            mode_trades.append(saved_trade)
            trade_id = str(saved_trade.get("id")) if saved_trade.get("id") is not None else None
            regime_tag = self._regime_tag_from_metadata(saved_trade.get("metadata"))
            journal_entry = self.paper_trading_service.build_journal_entry(
                title="Paper lane trade opened",
                body=f"Opened a paper trade automatically for {signal.symbol} on {signal.interval} after a qualified BUY setup.",
                symbol=signal.symbol,
                related_trade_id=trade_id,
                tags=[tag for tag in ["paper", "entry", "auto", signal.interval.lower(), regime_tag] if tag],
            )
            self.supabase_service.save_journal_entry(journal_entry.to_dict())

            coach_note = self._build_coach_note(
                mode="paper",
                signal=signal,
                open_trades=self._filter_trades(mode_trades, {"OPEN"}),
                portfolio_snapshot=portfolio_snapshot,
            )
            if coach_note is not None:
                coach_journal_entry = self.paper_trading_service.build_journal_entry(
                    title=f"AI coach: {coach_note.verdict}",
                    body=self.ai_coach_service.format_for_journal(coach_note),
                    symbol=signal.symbol,
                    related_trade_id=trade_id,
                    tags=["ai-coach", "anthropic", "paper", coach_note.verdict.lower().replace("_", "-")],
                )
                self.supabase_service.save_journal_entry(coach_journal_entry.to_dict())

            self.telegram_service.send_signal_alert(signal, lane_label="PAPER")
            self.telegram_service.send_message(
                (
                    "PAPER trade opened automatically.\n"
                    f"Pair: {signal.symbol}\n"
                    f"Timeframe: {signal.interval}\n"
                    f"Entry: {saved_trade.get('entry')}\n"
                    f"Stop Loss: {saved_trade.get('stop_loss')}\n"
                    f"Take Profit: {saved_trade.get('take_profit')}\n"
                    f"Simulated Fee: {self.config.paper_fee_rate:.4%}\n"
                    f"Simulated Slippage: {self.config.paper_slippage_bps:.2f} bps"
                )
            )
            return saved_trade

        if mode == "manual" or not self.config.bybit_allow_order_placement:
            self.telegram_service.send_signal_alert(signal, lane_label="LIVE" if mode == "live" else "MANUAL")
            if mode == "live" and not self.config.bybit_allow_order_placement:
                self.telegram_service.send_message(
                    (
                        "LIVE read-only lane: no order was sent.\n"
                        f"Pair: {signal.symbol}\n"
                        f"Timeframe: {signal.interval}\n"
                        "Set BYBIT_ALLOW_ORDER_PLACEMENT=true only after read-only validation passes."
                    )
                )
            elif mode == "manual":
                self.telegram_service.send_message(
                    (
                        "MANUAL alert-only lane detected a qualified setup.\n"
                        f"Pair: {signal.symbol}\n"
                        f"Timeframe: {signal.interval}\n"
                        "No paper or live order was opened from this lane."
                    )
                )
            return None

        if self.config.bybit_require_manual_confirmation:
            return self._request_live_confirmation(
                signal=signal,
                assessment=assessment,
                mode_trades=mode_trades,
                portfolio_snapshot=portfolio_snapshot,
                source_signal_record=source_signal_record,
                trade_group_id=trade_group_id,
            )

        trade = self._build_live_trade_record(
            signal=signal,
            assessment=assessment,
            status=LIVE_PENDING_CONFIRMATION_STATUS,
            notes="Submitting live order automatically without manual confirmation.",
            metadata=self._build_live_trade_metadata(**signal_context_metadata),
        )
        saved_trade = self.supabase_service.save_trade(trade.to_dict())
        if saved_trade is None:
            return None

        mode_trades.append(saved_trade)
        message = self._submit_live_entry_order(saved_trade, confirmation_source="auto")
        self.telegram_service.send_signal_alert(signal, lane_label="LIVE")
        self.telegram_service.send_message(message)
        return saved_trade

    def _handle_signal_exit(
        self,
        *,
        mode: str,
        signal: Signal,
        trade: dict[str, Any],
        latest_price: float,
        mode_trades: list[dict[str, Any]],
        closed_trades: list[dict[str, Any]],
    ) -> None:
        if mode == "paper":
            updated_trade = self.paper_trading_service.close_trade(
                trade,
                exit_price=latest_price,
                note="Paper trade closed because a SELL exit signal appeared in spot mode.",
            )
            trade_id = updated_trade.get("id")
            if trade_id is not None:
                self.supabase_service.update_trade(trade_id, updated_trade)
            else:
                self.logger.warning("Closed trade for %s had no Supabase id; close record may need manual reconciliation.", signal.symbol)

            self._replace_trade(mode_trades, updated_trade)
            closed_trades.append(updated_trade)

            journal_entry = self.paper_trading_service.build_journal_entry(
                title="Paper trade closed",
                body=f"Closed the paper trade for {signal.symbol} on {signal.interval} because the model flipped to SELL in spot mode.",
                symbol=signal.symbol,
                related_trade_id=str(trade_id) if trade_id is not None else None,
                tags=self._paper_exit_tags(updated_trade, signal.interval),
            )
            self.supabase_service.save_journal_entry(journal_entry.to_dict())
            self.telegram_service.send_message(
                (
                    "PAPER exit executed automatically.\n"
                    f"Pair: {signal.symbol}\n"
                    f"Timeframe: {signal.interval}\n"
                    f"Exit Price: {updated_trade.get('exit_price')}\n"
                    f"Net PnL: {updated_trade.get('pnl')}"
                )
            )
            return

        message = self._submit_live_exit_order(
            trade=trade,
            reason=f"Strategy flipped to SELL for {signal.symbol} {signal.interval}.",
            price_hint=latest_price,
            mode_trades=mode_trades,
            closed_trades=closed_trades,
        )
        self.telegram_service.send_message(message)

    def _sync_open_trade_levels(
        self,
        mode_trades: list[dict[str, Any]],
        closed_trades: list[dict[str, Any]],
        symbol: str,
        interval: str,
        latest_candle: pd.Series,
    ) -> dict[str, Any] | None:
        open_trade = self._find_trade(mode_trades, symbol=symbol, interval=interval, statuses={"OPEN"})
        if open_trade is None:
            return None

        updated_trade = self.paper_trading_service.manage_open_trade(open_trade, latest_candle)
        if updated_trade is None:
            return None

        trade_id = updated_trade.get("id")
        if trade_id is not None:
            self.supabase_service.update_trade(trade_id, updated_trade)
        else:
            self.logger.warning("Trade managed by level sync for %s %s had no Supabase id.", symbol, interval)

        self._replace_trade(mode_trades, updated_trade)
        if str(updated_trade.get("status") or "") == "CLOSED":
            closed_trades.append(updated_trade)
            journal_entry = self.paper_trading_service.build_journal_entry(
                title="Paper trade auto-closed",
                body=f"{symbol} on {interval} was closed automatically because a managed stop or target level was reached.",
                symbol=symbol,
                related_trade_id=str(trade_id) if trade_id is not None else None,
                tags=self._paper_exit_tags(updated_trade, interval),
            )
            self.supabase_service.save_journal_entry(journal_entry.to_dict())
        else:
            management_note = str(updated_trade.get("notes") or "")
            if "partial profit" in management_note.lower() or "breakeven" in management_note.lower():
                journal_tags = self._paper_management_tags(updated_trade, interval)
                journal_title = "Paper trade managed"
                journal_body = management_note or f"{symbol} on {interval} advanced its trade management state."
                self.supabase_service.save_journal_entry(
                    self.paper_trading_service.build_journal_entry(
                        title=journal_title,
                        body=journal_body,
                        symbol=symbol,
                        related_trade_id=str(trade_id) if trade_id is not None else None,
                        tags=journal_tags,
                    ).to_dict()
                )
        return updated_trade

    def _sync_live_open_trade_levels(
        self,
        mode_trades: list[dict[str, Any]],
        closed_trades: list[dict[str, Any]],
        symbol: str,
        interval: str,
        latest_candle: pd.Series,
    ) -> dict[str, Any] | None:
        trade = self._find_trade(mode_trades, symbol=symbol, interval=interval, statuses={"OPEN"})
        if trade is None:
            return None

        low = float(latest_candle["low"])
        high = float(latest_candle["high"])
        stop_loss = float(trade.get("stop_loss") or 0)
        take_profit = float(trade.get("take_profit") or 0)

        if stop_loss > 0 and low <= stop_loss:
            self.telegram_service.send_message(
                self._submit_live_exit_order(
                    trade=trade,
                    reason=f"Stop loss touched for {symbol} {interval}.",
                    price_hint=stop_loss,
                    mode_trades=mode_trades,
                    closed_trades=closed_trades,
                )
            )
            return self._find_trade(mode_trades, symbol=symbol, interval=interval, statuses={"CLOSED"})

        if take_profit > 0 and high >= take_profit:
            self.telegram_service.send_message(
                self._submit_live_exit_order(
                    trade=trade,
                    reason=f"Take profit touched for {symbol} {interval}.",
                    price_hint=take_profit,
                    mode_trades=mode_trades,
                    closed_trades=closed_trades,
                )
            )
            return self._find_trade(mode_trades, symbol=symbol, interval=interval, statuses={"CLOSED"})

        return None

    def _signal_changed(self, mode: str, signal: Signal) -> bool:
        signal_key = f"{mode}:{signal.symbol}:{signal.interval}"
        current_state = f"{signal.action}:{signal.eligible}:{signal.execution_intent}"
        previous_state = self.last_signals.get(signal_key)
        self.last_signals[signal_key] = current_state
        return previous_state != current_state

    @staticmethod
    def _paper_exit_tags(trade: dict[str, Any], interval: str) -> list[str]:
        normalized_note = str(trade.get("notes") or "").lower()
        tags = ["paper", interval.lower()]
        regime_tag = TradingEngine._regime_tag_from_metadata(trade.get("metadata"))
        if regime_tag:
            tags.append(regime_tag)

        if "stop loss" in normalized_note:
            tags.extend(["risk", "stop-loss"])
        elif "trailing stop" in normalized_note:
            tags.extend(["risk", "trailing-stop"])
        elif "take profit" in normalized_note:
            tags.extend(["risk", "take-profit"])
        elif "stretch target" in normalized_note:
            tags.extend(["risk", "adaptive-target"])
        elif "sell exit" in normalized_note or "flipped to sell" in normalized_note:
            tags.extend(["exit", "signal-flip"])
        else:
            tags.append("exit")

        return tags

    @staticmethod
    def _paper_management_tags(trade: dict[str, Any], interval: str) -> list[str]:
        normalized_note = str(trade.get("notes") or "").lower()
        tags = ["paper", "management", interval.lower()]
        regime_tag = TradingEngine._regime_tag_from_metadata(trade.get("metadata"))
        if regime_tag:
            tags.append(regime_tag)

        if "partial profit" in normalized_note:
            tags.append("partial-profit")
        if "adaptive stop" in normalized_note or "trailing" in normalized_note:
            tags.append("trailing-stop")
        if "breakeven" in normalized_note:
            tags.append("breakeven")

        return tags

    @staticmethod
    def _regime_tag_from_metadata(metadata_value: Any) -> str | None:
        if not isinstance(metadata_value, dict):
            return None
        regime = metadata_value.get("regime")
        if not isinstance(regime, dict):
            return None
        slug = str(regime.get("slug") or "").strip().lower()
        return slug or None

    def _build_signal_context_metadata(self, signal: Signal) -> dict[str, Any]:
        indicators = dict(signal.indicators)
        return {
            "regime": self._infer_market_regime_from_signal(signal),
            "setup": {
                "type": str(indicators.get("setup_type") or "none"),
                "trade_priority_allowed": bool(indicators.get("trade_priority_allowed", True)),
                "symbol_rank": int(round(self._coerce_indicator_float(indicators.get("symbol_rank")))),
                "symbol_strength_score": round(self._coerce_indicator_float(indicators.get("symbol_strength_score")), 2),
            },
            "higher_timeframe": {
                "aligned": bool(indicators.get("higher_timeframe_aligned", False)),
                "regime_slug": str(indicators.get("higher_timeframe_regime_slug") or "unclassified"),
                "regime_label": str(indicators.get("higher_timeframe_regime_label") or "Unclassified"),
                "strength_score": round(self._coerce_indicator_float(indicators.get("higher_timeframe_strength_score")), 2),
            },
            "signal_snapshot": {
                "action": signal.action,
                "confidence": signal.confidence,
                "reason": signal.reason,
                "indicators": indicators,
                "captured_at": signal.timestamp.isoformat(),
            },
        }

    @staticmethod
    def _build_trade_management_context(profile: RiskProfile) -> dict[str, Any]:
        return {
            "trade_management": {
                "style": profile.exit_style,
                "setup_type": profile.setup_type or "none",
                "stop_source": profile.stop_source,
                "partial_take_profit": round(profile.partial_take_profit, 6) if profile.partial_take_profit is not None else None,
                "breakeven_trigger": round(profile.breakeven_trigger, 6) if profile.breakeven_trigger is not None else None,
                "trailing_stop_buffer": round(profile.trailing_stop_buffer, 6) if profile.trailing_stop_buffer is not None else None,
                "atr_reference": round(profile.atr_reference, 6) if profile.atr_reference is not None else None,
                "initial_entry": round(profile.entry, 6),
                "initial_stop_loss": round(profile.stop_loss, 6),
                "initial_take_profit": round(profile.take_profit, 6),
                "trailing_active": False,
                "partial_exit_taken": False,
            }
        }

    @staticmethod
    def _infer_market_regime_from_signal(signal: Signal) -> dict[str, Any]:
        indicators = signal.indicators or {}
        slug = str(indicators.get("regime_slug") or "unclassified").strip().lower() or "unclassified"
        label = str(indicators.get("regime_label") or "Unclassified").strip() or "Unclassified"
        volatility_pct = TradingEngine._coerce_indicator_float(indicators.get("atr_pct_14")) * 100
        return {
            "slug": slug,
            "label": label,
            "volatility_pct": round(volatility_pct, 3),
        }

    def _build_symbol_market_contexts(self) -> dict[str, dict[str, Any]]:
        contexts: dict[str, dict[str, Any]] = {}
        interval = self.config.higher_timeframe_interval

        for symbol in self.config.traded_symbols:
            try:
                market_frame = self.data_service.get_crypto_data(symbol, interval, self.config.market_data_limit)
                indicator_frame = apply_core_indicators(market_frame)
                context = self.strategy.build_market_context(
                    indicator_frame,
                    symbol=symbol,
                    interval=interval,
                    market="spot",
                )
                contexts[symbol] = dict(context)
            except Exception as exc:  # pragma: no cover - strategy context should not halt the cycle
                self.logger.warning(
                    "Could not build higher-timeframe context for %s %s: %s",
                    symbol,
                    interval,
                    exc,
                )
                contexts[symbol] = {
                    "symbol": symbol,
                    "interval": interval,
                    "bullish": False,
                    "regime_slug": "unclassified",
                    "regime_label": "Unclassified",
                    "strength_score": 0.0,
                }

        ranked = sorted(
            contexts.values(),
            key=lambda item: self._coerce_indicator_float(item.get("strength_score")),
            reverse=True,
        )
        for rank, context in enumerate(ranked, start=1):
            strength_score = self._coerce_indicator_float(context.get("strength_score"))
            trade_priority_allowed = rank <= self.config.symbol_top_n and strength_score >= self.config.symbol_min_strength_score
            context["symbol_rank"] = rank
            context["trade_priority_allowed"] = trade_priority_allowed
            context["priority_note"] = (
                f"Ranked #{rank} with strength score {strength_score:.1f}."
                if trade_priority_allowed
                else f"Ranked #{rank} with strength score {strength_score:.1f}; watch-only until strength improves."
            )

        return contexts

    def _enabled_execution_modes(self) -> list[str]:
        modes: list[str] = []
        if self.config.paper_trading_enabled:
            modes.append("paper")
        if self.config.live_trading_enabled:
            modes.append("live")
        if not modes:
            modes.append(self.config.execution_mode)
        return modes

    @staticmethod
    def _preferred_signal_mode(modes: list[str]) -> str:
        if "paper" in modes:
            return "paper"
        if "live" in modes:
            return "live"
        return modes[0]

    def _build_lane_portfolio_snapshot(
        self,
        *,
        mode: str,
        mode_trades: list[dict[str, Any]],
        closed_trades: list[dict[str, Any]],
        latest_prices: dict[str, float],
    ) -> PortfolioSnapshot:
        if mode == "live":
            return self._build_live_portfolio_snapshot(mode_trades, closed_trades)

        if mode == "paper":
            return self.paper_trading_service.calculate_portfolio_snapshot(
                execution_mode="paper",
                open_trades=self._filter_trades(mode_trades, {"OPEN"}),
                closed_trades=closed_trades,
                latest_prices=latest_prices,
            )

        total_realized_pnl = sum(float(trade.get("pnl") or 0) for trade in closed_trades)
        realized_pnl_today = sum(
            float(trade.get("pnl") or 0)
            for trade in closed_trades
            if self._is_today(trade.get("closed_at"))
        )
        wins = sum(1 for trade in closed_trades if float(trade.get("pnl") or 0) > 0)
        win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0
        open_positions = len(self._filter_trades(mode_trades, {"OPEN"}))

        return PortfolioSnapshot(
            execution_mode="manual",
            total_equity=round(self.config.account_balance + total_realized_pnl, 6),
            available_cash=round(self.config.account_balance + total_realized_pnl, 6),
            realized_pnl_today=round(realized_pnl_today, 6),
            total_realized_pnl=round(total_realized_pnl, 6),
            unrealized_pnl=0.0,
            open_positions=open_positions,
            win_rate=round(win_rate, 2),
        )

    def _build_trade_linkage_metadata(
        self,
        *,
        mode: str,
        signal: Signal,
        source_signal_record: dict[str, Any] | None,
        trade_group_id: str | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "execution_lane": mode,
            "paired_lanes": self._enabled_execution_modes(),
            "source_signal": {
                "symbol": signal.symbol,
                "interval": signal.interval,
                "timestamp": signal.timestamp.isoformat(),
            },
        }
        if trade_group_id is not None:
            metadata["trade_group_id"] = trade_group_id
        if source_signal_record is not None:
            source_signal_id = source_signal_record.get("id")
            if source_signal_id is not None:
                metadata["source_signal_id"] = str(source_signal_id)
            metadata["source_signal"] = {
                **metadata["source_signal"],
                "timestamp": str(source_signal_record.get("timestamp") or signal.timestamp.isoformat()),
                "action": str(source_signal_record.get("action") or signal.action),
                "execution_intent": str(source_signal_record.get("execution_intent") or signal.execution_intent),
            }
        return metadata

    def _link_paired_trades(
        self,
        created_entries: list[tuple[dict[str, Any], list[dict[str, Any]]]],
        trade_group_id: str,
    ) -> None:
        if len(created_entries) < 2:
            return

        saved_trades = [trade for trade, _trade_list in created_entries]
        for trade, trade_list in created_entries:
            trade_id = trade.get("id")
            if trade_id is None:
                continue

            sibling_ids = [
                str(other_trade.get("id"))
                for other_trade in saved_trades
                if other_trade.get("id") is not None and other_trade.get("id") != trade_id
            ]
            sibling_modes = [
                str(other_trade.get("execution_mode") or "")
                for other_trade in saved_trades
                if other_trade.get("id") != trade_id
            ]
            metadata = self._trade_metadata(trade)
            metadata.update(
                {
                    "trade_group_id": trade_group_id,
                    "paired_trade_ids": sibling_ids,
                    "paired_execution_modes": sibling_modes,
                }
            )
            updated_trade = dict(trade)
            updated_trade["metadata"] = metadata
            self.supabase_service.update_trade(trade_id, updated_trade)
            self._replace_trade(trade_list, updated_trade)

    def _request_live_confirmation(
        self,
        *,
        signal: Signal,
        assessment: RiskAssessment,
        mode_trades: list[dict[str, Any]],
        portfolio_snapshot: PortfolioSnapshot,
        source_signal_record: dict[str, Any] | None = None,
        trade_group_id: str | None = None,
    ) -> dict[str, Any] | None:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.config.live_confirmation_ttl_minutes)
        metadata = self._build_live_trade_metadata(
            confirmation_requested_at=datetime.now(timezone.utc).isoformat(),
            confirmation_expires_at=expires_at.isoformat(),
            rollout={
                "size_scale": self.config.live_tiny_size_scale,
                "max_order_notional": self.config.live_max_order_notional,
                "max_daily_entries": self.config.live_max_daily_entries,
            },
            **self._build_signal_context_metadata(signal),
            **self._build_trade_linkage_metadata(
                mode="live",
                signal=signal,
                source_signal_record=source_signal_record,
                trade_group_id=trade_group_id,
            ),
        )
        trade = self._build_live_trade_record(
            signal=signal,
            assessment=assessment,
            status=LIVE_PENDING_CONFIRMATION_STATUS,
            notes="Awaiting manual confirmation before sending a live Bybit order.",
            metadata=metadata,
        )
        saved_trade = self.supabase_service.save_trade(trade.to_dict())
        if saved_trade is None:
            return None

        mode_trades.append(saved_trade)
        trade_id = str(saved_trade.get("id")) if saved_trade.get("id") is not None else "pending"

        journal_entry = self.paper_trading_service.build_journal_entry(
            title="Live trade confirmation requested",
            body=(
                f"A live trade for {signal.symbol} on {signal.interval} qualified under the rules and is waiting for manual confirmation.\n"
                f"Confirmation ID: {trade_id}\n"
                f"Entry: {signal.entry}\n"
                f"Stop Loss: {signal.stop_loss}\n"
                f"Take Profit: {signal.take_profit}\n"
                f"Position Size: {signal.position_size}\n"
                f"Rollout Cap: {self.config.live_max_order_notional}"
            ),
            symbol=signal.symbol,
            related_trade_id=trade_id if saved_trade.get("id") is not None else None,
            tags=["live", "confirmation-request", signal.interval.lower()],
        )
        self.supabase_service.save_journal_entry(journal_entry.to_dict())

        coach_message = ""
        coach_note = self._build_coach_note(
            mode="live",
            signal=signal,
            open_trades=self._filter_trades(mode_trades, {"OPEN"}),
            portfolio_snapshot=portfolio_snapshot,
        )
        if coach_note is not None:
            coach_journal_entry = self.paper_trading_service.build_journal_entry(
                title=f"AI coach: {coach_note.verdict}",
                body=self.ai_coach_service.format_for_journal(coach_note),
                symbol=signal.symbol,
                related_trade_id=trade_id if saved_trade.get("id") is not None else None,
                tags=["ai-coach", "anthropic", "live-confirmation", coach_note.verdict.lower().replace("_", "-")],
            )
            self.supabase_service.save_journal_entry(coach_journal_entry.to_dict())
            coach_message = self.ai_coach_service.format_for_telegram(coach_note)

        lines = [
            "Live trade confirmation requested",
            f"ID: {self._short_trade_id(saved_trade)}",
            f"Pair: {signal.symbol}",
            f"Timeframe: {signal.interval}",
            f"Confidence: {signal.confidence}%",
            f"Entry: {signal.entry if signal.entry is not None else 'N/A'}",
            f"Stop Loss: {signal.stop_loss if signal.stop_loss is not None else 'N/A'}",
            f"Take Profit: {signal.take_profit if signal.take_profit is not None else 'N/A'}",
            f"Position Size: {signal.position_size if signal.position_size is not None else 'N/A'}",
            f"Expires: {expires_at.astimezone(timezone.utc).isoformat()}",
            "",
            f"Confirm with /live_confirm {self._short_trade_id(saved_trade)}",
            f"Cancel with /live_cancel {self._short_trade_id(saved_trade)}",
        ]
        if coach_message:
            lines.extend(["", coach_message])
        self.telegram_service.send_message("\n".join(lines))
        return saved_trade

    def _sync_pending_live_confirmation(
        self,
        *,
        signal: Signal,
        assessment: RiskAssessment,
        trade: dict[str, Any],
        mode_trades: list[dict[str, Any]],
    ) -> None:
        trade_id = trade.get("id")
        metadata = self._trade_metadata(trade)

        if self._live_confirmation_expired(trade):
            cancelled_trade = dict(trade)
            cancelled_trade.update(
                {
                    "status": "CANCELLED",
                    "notes": "Live confirmation expired before the order was approved.",
                }
            )
            cancelled_trade["metadata"] = metadata
            if trade_id is not None:
                self.supabase_service.update_trade(trade_id, cancelled_trade)
            self._replace_trade(mode_trades, cancelled_trade)
            self.supabase_service.save_journal_entry(
                self.paper_trading_service.build_journal_entry(
                    title="Live confirmation expired",
                    body=f"The live trade request for {signal.symbol} on {signal.interval} expired before confirmation.",
                    symbol=signal.symbol,
                    related_trade_id=str(trade_id) if trade_id is not None else None,
                    tags=["live", "confirmation-expired", signal.interval.lower()],
                ).to_dict()
            )
            self.telegram_service.send_message(
                f"Live confirmation expired for {signal.symbol} {signal.interval}. No order was sent to Bybit."
            )
            return

        if not assessment.accepted or assessment.execution_intent != "OPEN_LONG" or assessment.risk_profile is None:
            cancelled_trade = dict(trade)
            cancelled_trade.update(
                {
                    "status": "CANCELLED",
                    "notes": "Live confirmation was cancelled because the setup no longer qualified before confirmation.",
                }
            )
            cancelled_trade["metadata"] = metadata
            if trade_id is not None:
                self.supabase_service.update_trade(trade_id, cancelled_trade)
            self._replace_trade(mode_trades, cancelled_trade)
            self.supabase_service.save_journal_entry(
                self.paper_trading_service.build_journal_entry(
                    title="Live confirmation cancelled",
                    body=f"The live trade request for {signal.symbol} on {signal.interval} was cancelled because the setup no longer qualified.",
                    symbol=signal.symbol,
                    related_trade_id=str(trade_id) if trade_id is not None else None,
                    tags=["live", "confirmation-cancelled", signal.interval.lower()],
                ).to_dict()
            )
            self.telegram_service.send_message(
                f"Live confirmation cancelled for {signal.symbol} {signal.interval} because the setup no longer qualified."
            )
            return

        refreshed_trade = dict(trade)
        refreshed_trade.update(
            {
                "entry": assessment.risk_profile.entry,
                "stop_loss": assessment.risk_profile.stop_loss,
                "take_profit": assessment.risk_profile.take_profit,
                "position_size": assessment.risk_profile.position_size,
                "confidence": signal.confidence,
                "risk_reward_ratio": assessment.risk_profile.risk_reward_ratio,
                "notional_value": assessment.risk_profile.notional_value,
                "notes": "Awaiting manual confirmation before sending a live Bybit order.",
                "metadata": metadata,
            }
        )
        if trade_id is not None:
            self.supabase_service.update_trade(trade_id, refreshed_trade)
        self._replace_trade(mode_trades, refreshed_trade)

    def _submit_live_entry_order(self, trade: dict[str, Any], *, confirmation_source: str) -> str:
        trade_id = trade.get("id")
        metadata = self._trade_metadata(trade)
        order_link_id = f"live-entry-{str(trade_id or uuid4())[:8]}-{uuid4().hex[:8]}"

        response = self.bybit_service.place_spot_order(
            symbol=str(trade.get("symbol") or ""),
            side="Buy",
            quantity=float(trade.get("position_size") or 0),
            confirmed=True,
            order_type="Market",
            order_link_id=order_link_id,
        )
        order_id = str(response.get("result", {}).get("orderId") or "")
        order_snapshot = self._wait_for_order_snapshot(order_id=order_id or None, order_link_id=order_link_id)

        metadata.update(
            {
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "confirmed_via": confirmation_source,
                "entry_order": self._build_order_metadata(order_snapshot, order_id=order_id, order_link_id=order_link_id),
            }
        )

        if order_snapshot is not None and self._is_order_filled(order_snapshot):
            filled_trade = self._apply_live_entry_fill(trade, metadata, order_snapshot)
            if trade_id is not None:
                self.supabase_service.update_trade(trade_id, filled_trade)
            self.supabase_service.save_journal_entry(
                self.paper_trading_service.build_journal_entry(
                    title="Live trade opened",
                    body=(
                        f"Opened a live Bybit spot trade for {trade.get('symbol')} on {trade.get('interval')}.\n"
                        f"Entry Price: {filled_trade.get('entry')}\n"
                        f"Position Size: {filled_trade.get('position_size')}"
                    ),
                    symbol=str(trade.get("symbol") or ""),
                    related_trade_id=str(trade_id) if trade_id is not None else None,
                    tags=["live", "entry", "filled", str(trade.get("interval") or "").lower()],
                ).to_dict()
            )
            return (
                f"Live order filled for {trade.get('symbol')} {trade.get('interval')}.\n"
                f"Entry: {filled_trade.get('entry')}\n"
                f"Position Size: {filled_trade.get('position_size')}\n"
                f"Order ID: {order_id or 'N/A'}"
            )

        if order_snapshot is not None and self._is_order_failed(order_snapshot):
            cancelled_trade = dict(trade)
            cancelled_trade.update(
                {
                    "status": "CANCELLED",
                    "notes": f"Live entry order failed with status {order_snapshot.get('orderStatus')}.",
                    "metadata": metadata,
                }
            )
            if trade_id is not None:
                self.supabase_service.update_trade(trade_id, cancelled_trade)
            self.supabase_service.save_journal_entry(
                self.paper_trading_service.build_journal_entry(
                    title="Live entry failed",
                    body=f"Bybit rejected or cancelled the live entry order for {trade.get('symbol')} on {trade.get('interval')}.",
                    symbol=str(trade.get("symbol") or ""),
                    related_trade_id=str(trade_id) if trade_id is not None else None,
                    tags=["live", "entry-failed", str(trade.get("interval") or "").lower()],
                ).to_dict()
            )
            return (
                f"Live order failed for {trade.get('symbol')} {trade.get('interval')}.\n"
                f"Bybit status: {order_snapshot.get('orderStatus')}\n"
                f"Order ID: {order_id or 'N/A'}"
            )

        submitted_trade = dict(trade)
        submitted_trade.update(
            {
                "status": LIVE_ENTRY_SUBMITTED_STATUS,
                "notes": "Live entry order submitted to Bybit and waiting for fill confirmation.",
                "metadata": metadata,
            }
        )
        if trade_id is not None:
            self.supabase_service.update_trade(trade_id, submitted_trade)
        self.supabase_service.save_journal_entry(
            self.paper_trading_service.build_journal_entry(
                title="Live entry submitted",
                body=f"Submitted a live entry order for {trade.get('symbol')} on {trade.get('interval')} and are waiting for fill confirmation.",
                symbol=str(trade.get("symbol") or ""),
                related_trade_id=str(trade_id) if trade_id is not None else None,
                tags=["live", "entry-submitted", str(trade.get("interval") or "").lower()],
            ).to_dict()
        )
        return (
            f"Live order submitted for {trade.get('symbol')} {trade.get('interval')}.\n"
            f"Waiting for Bybit fill confirmation.\n"
            f"Order ID: {order_id or 'N/A'}"
        )

    def _submit_live_exit_order(
        self,
        *,
        trade: dict[str, Any],
        reason: str,
        price_hint: float,
        mode_trades: list[dict[str, Any]],
        closed_trades: list[dict[str, Any]],
    ) -> str:
        if not self.config.bybit_allow_order_placement:
            self.logger.warning("Live exit was needed for %s %s, but order placement is disabled.", trade.get("symbol"), trade.get("interval"))
            return (
                f"Live exit needed for {trade.get('symbol')} {trade.get('interval')}, but BYBIT_ALLOW_ORDER_PLACEMENT is disabled. "
                "No order was sent."
            )

        trade_id = trade.get("id")
        metadata = self._trade_metadata(trade)
        order_link_id = f"live-exit-{str(trade_id or uuid4())[:8]}-{uuid4().hex[:8]}"
        response = self.bybit_service.place_spot_order(
            symbol=str(trade.get("symbol") or ""),
            side="Sell",
            quantity=float(trade.get("position_size") or 0),
            confirmed=True,
            order_type="Market",
            order_link_id=order_link_id,
        )
        order_id = str(response.get("result", {}).get("orderId") or "")
        order_snapshot = self._wait_for_order_snapshot(order_id=order_id or None, order_link_id=order_link_id)

        metadata["exit_order"] = self._build_order_metadata(order_snapshot, order_id=order_id, order_link_id=order_link_id)

        if order_snapshot is not None and self._is_order_filled(order_snapshot):
            exit_price = self._extract_order_average_price(order_snapshot, fallback=price_hint)
            closed_trade = self.paper_trading_service.close_trade(
                trade,
                exit_price=exit_price,
                note=f"Live Bybit spot trade closed. Reason: {reason}",
            )
            closed_trade["metadata"] = metadata
            if trade_id is not None:
                self.supabase_service.update_trade(trade_id, closed_trade)
            self._replace_trade(mode_trades, closed_trade)
            closed_trades.append(closed_trade)
            self.supabase_service.save_journal_entry(
                self.paper_trading_service.build_journal_entry(
                    title="Live trade closed",
                    body=(
                        f"Closed a live Bybit spot trade for {trade.get('symbol')} on {trade.get('interval')}.\n"
                        f"Exit Price: {closed_trade.get('exit_price')}\n"
                        f"Reason: {reason}"
                    ),
                    symbol=str(trade.get("symbol") or ""),
                    related_trade_id=str(trade_id) if trade_id is not None else None,
                    tags=["live", "exit", str(trade.get("interval") or "").lower()],
                ).to_dict()
            )
            return (
                f"Live exit filled for {trade.get('symbol')} {trade.get('interval')}.\n"
                f"Exit Price: {closed_trade.get('exit_price')}\n"
                f"PnL: {closed_trade.get('pnl')}"
            )

        if order_snapshot is not None and self._is_order_failed(order_snapshot):
            reopened_trade = dict(trade)
            reopened_trade["metadata"] = metadata
            reopened_trade["notes"] = f"Live exit order failed with status {order_snapshot.get('orderStatus')}."
            if trade_id is not None:
                self.supabase_service.update_trade(trade_id, reopened_trade)
            self._replace_trade(mode_trades, reopened_trade)
            self.supabase_service.save_journal_entry(
                self.paper_trading_service.build_journal_entry(
                    title="Live exit failed",
                    body=f"Bybit rejected or cancelled the live exit order for {trade.get('symbol')} on {trade.get('interval')}.",
                    symbol=str(trade.get("symbol") or ""),
                    related_trade_id=str(trade_id) if trade_id is not None else None,
                    tags=["live", "exit-failed", str(trade.get("interval") or "").lower()],
                ).to_dict()
            )
            return (
                f"Live exit failed for {trade.get('symbol')} {trade.get('interval')}.\n"
                f"Bybit status: {order_snapshot.get('orderStatus')}"
            )

        submitted_trade = dict(trade)
        submitted_trade.update(
            {
                "status": LIVE_EXIT_SUBMITTED_STATUS,
                "notes": f"Live exit order submitted to Bybit and waiting for fill confirmation. Reason: {reason}",
                "metadata": metadata,
            }
        )
        if trade_id is not None:
            self.supabase_service.update_trade(trade_id, submitted_trade)
        self._replace_trade(mode_trades, submitted_trade)
        self.supabase_service.save_journal_entry(
            self.paper_trading_service.build_journal_entry(
                title="Live exit submitted",
                body=f"Submitted a live exit order for {trade.get('symbol')} on {trade.get('interval')} and are waiting for Bybit fill confirmation.",
                symbol=str(trade.get("symbol") or ""),
                related_trade_id=str(trade_id) if trade_id is not None else None,
                tags=["live", "exit-submitted", str(trade.get("interval") or "").lower()],
            ).to_dict()
        )
        return (
            f"Live exit submitted for {trade.get('symbol')} {trade.get('interval')}.\n"
            f"Waiting for Bybit fill confirmation.\n"
            f"Reason: {reason}"
        )

    def _reconcile_live_orders(self, mode_trades: list[dict[str, Any]], closed_trades: list[dict[str, Any]]) -> None:
        for trade in list(mode_trades):
            status = str(trade.get("status") or "")
            if status not in {LIVE_ENTRY_SUBMITTED_STATUS, LIVE_EXIT_SUBMITTED_STATUS}:
                continue

            metadata = self._trade_metadata(trade)
            order_key = "entry_order" if status == LIVE_ENTRY_SUBMITTED_STATUS else "exit_order"
            order_state = metadata.get(order_key) or {}
            order_id = str(order_state.get("order_id") or "")
            order_link_id = str(order_state.get("order_link_id") or "")
            if not order_id and not order_link_id:
                continue

            order_snapshot = self._wait_for_order_snapshot(order_id=order_id or None, order_link_id=order_link_id or None)
            if order_snapshot is None:
                continue

            metadata[order_key] = self._build_order_metadata(order_snapshot, order_id=order_id, order_link_id=order_link_id)
            trade_id = trade.get("id")

            if status == LIVE_ENTRY_SUBMITTED_STATUS:
                if self._is_order_filled(order_snapshot):
                    filled_trade = self._apply_live_entry_fill(trade, metadata, order_snapshot)
                    if trade_id is not None:
                        self.supabase_service.update_trade(trade_id, filled_trade)
                    self.supabase_service.save_journal_entry(
                        self.paper_trading_service.build_journal_entry(
                            title="Live trade opened",
                            body=(
                                f"Bybit confirmed the live entry fill for {trade.get('symbol')} on {trade.get('interval')}.\n"
                                f"Entry Price: {filled_trade.get('entry')}\n"
                                f"Position Size: {filled_trade.get('position_size')}"
                            ),
                            symbol=str(trade.get("symbol") or ""),
                            related_trade_id=str(trade_id) if trade_id is not None else None,
                            tags=["live", "entry", "filled", str(trade.get("interval") or "").lower()],
                        ).to_dict()
                    )
                    self.telegram_service.send_message(
                        f"Bybit confirmed the live entry fill for {trade.get('symbol')} {trade.get('interval')} at {filled_trade.get('entry')}."
                    )
                elif self._is_order_failed(order_snapshot):
                    cancelled_trade = dict(trade)
                    cancelled_trade.update(
                        {
                            "status": "CANCELLED",
                            "notes": f"Bybit cancelled or rejected the live entry order with status {order_snapshot.get('orderStatus')}.",
                            "metadata": metadata,
                        }
                    )
                    if trade_id is not None:
                        self.supabase_service.update_trade(trade_id, cancelled_trade)
                    self.supabase_service.save_journal_entry(
                        self.paper_trading_service.build_journal_entry(
                            title="Live entry failed",
                            body=f"Bybit cancelled or rejected the live entry order for {trade.get('symbol')} on {trade.get('interval')}.",
                            symbol=str(trade.get("symbol") or ""),
                            related_trade_id=str(trade_id) if trade_id is not None else None,
                            tags=["live", "entry-failed", str(trade.get("interval") or "").lower()],
                        ).to_dict()
                    )
                    self.telegram_service.send_message(
                        f"Bybit rejected the live entry order for {trade.get('symbol')} {trade.get('interval')}."
                    )
            else:
                if self._is_order_filled(order_snapshot):
                    exit_price = self._extract_order_average_price(order_snapshot, fallback=float(trade.get("entry") or 0))
                    closed_trade = self.paper_trading_service.close_trade(
                        trade,
                        exit_price=exit_price,
                        note="Bybit confirmed the live exit order fill.",
                    )
                    closed_trade["metadata"] = metadata
                    if trade_id is not None:
                        self.supabase_service.update_trade(trade_id, closed_trade)
                    closed_trades.append(closed_trade)
                    self.supabase_service.save_journal_entry(
                        self.paper_trading_service.build_journal_entry(
                            title="Live trade closed",
                            body=f"Bybit confirmed the live exit fill for {trade.get('symbol')} on {trade.get('interval')} at {closed_trade.get('exit_price')}.",
                            symbol=str(trade.get("symbol") or ""),
                            related_trade_id=str(trade_id) if trade_id is not None else None,
                            tags=["live", "exit", str(trade.get("interval") or "").lower()],
                        ).to_dict()
                    )
                    self.telegram_service.send_message(
                        f"Bybit confirmed the live exit fill for {trade.get('symbol')} {trade.get('interval')} at {closed_trade.get('exit_price')}."
                    )
                elif self._is_order_failed(order_snapshot):
                    reopened_trade = dict(trade)
                    reopened_trade.update(
                        {
                            "status": "OPEN",
                            "notes": f"Bybit rejected the exit order with status {order_snapshot.get('orderStatus')}; position remains open.",
                            "metadata": metadata,
                        }
                    )
                    if trade_id is not None:
                        self.supabase_service.update_trade(trade_id, reopened_trade)
                    self.supabase_service.save_journal_entry(
                        self.paper_trading_service.build_journal_entry(
                            title="Live exit failed",
                            body=f"Bybit rejected the live exit order for {trade.get('symbol')} on {trade.get('interval')}. The position remains open.",
                            symbol=str(trade.get("symbol") or ""),
                            related_trade_id=str(trade_id) if trade_id is not None else None,
                            tags=["live", "exit-failed", str(trade.get("interval") or "").lower()],
                        ).to_dict()
                    )
                    self.telegram_service.send_message(
                        f"Bybit rejected the live exit order for {trade.get('symbol')} {trade.get('interval')}. The position remains open."
                    )

    def _apply_live_safety_overrides(self, assessment: RiskAssessment, mode_trades: list[dict[str, Any]]) -> RiskAssessment:
        if not assessment.accepted or assessment.execution_intent != "OPEN_LONG" or assessment.risk_profile is None:
            return assessment

        rejection_reasons = list(assessment.rejection_reasons)
        warnings = list(assessment.warnings)

        live_entries_today = self._count_live_entries_today(mode_trades)
        if live_entries_today >= self.config.live_max_daily_entries:
            rejection_reasons.append(
                f"Live daily entry cap reached: {live_entries_today} entries today versus the configured cap of {self.config.live_max_daily_entries}."
            )

        profile = assessment.risk_profile
        scaled_size = profile.position_size * self.config.live_tiny_size_scale
        rollout_cap_size = self.config.live_max_order_notional / max(profile.entry, 1e-9)
        adjusted_size = min(profile.position_size, scaled_size, rollout_cap_size)

        if adjusted_size <= 0:
            rejection_reasons.append("Live rollout clamp reduced the position size to zero, so no live order should be sent.")

        adjusted_notional = profile.entry * adjusted_size
        if adjusted_notional < self.config.min_order_notional:
            rejection_reasons.append(
                f"Live rollout clamp reduced the notional to {adjusted_notional:.2f}, below the minimum tradable notional of {self.config.min_order_notional:.2f}."
            )

        if rejection_reasons:
            return RiskAssessment(
                accepted=False,
                execution_intent="NO_ACTION",
                rejection_reasons=rejection_reasons,
                warnings=warnings,
                market_volatility_pct=assessment.market_volatility_pct,
                daily_loss_used_pct=assessment.daily_loss_used_pct,
            )

        size_ratio = adjusted_size / max(profile.position_size, 1e-9)
        adjusted_profile = replace(
            profile,
            position_size=round(adjusted_size, 6),
            notional_value=round(profile.entry * adjusted_size, 6),
            risk_amount=round(profile.risk_amount * size_ratio, 6),
            estimated_loss=round(profile.estimated_loss * size_ratio, 6),
            estimated_reward=round(profile.estimated_reward * size_ratio, 6),
        )
        warnings.append(
            f"Live rollout clamp applied: size scaled to {self.config.live_tiny_size_scale:.0%} with a max live notional cap of {self.config.live_max_order_notional:.2f}."
        )
        return RiskAssessment(
            accepted=True,
            execution_intent=assessment.execution_intent,
            warnings=warnings,
            market_volatility_pct=assessment.market_volatility_pct,
            daily_loss_used_pct=assessment.daily_loss_used_pct,
            risk_profile=adjusted_profile,
        )

    def _build_live_portfolio_snapshot(self, mode_trades: list[dict[str, Any]], closed_trades: list[dict[str, Any]]) -> PortfolioSnapshot:
        total_realized_pnl = sum(float(trade.get("pnl") or 0) for trade in closed_trades)
        realized_pnl_today = sum(
            float(trade.get("pnl") or 0)
            for trade in closed_trades
            if self._is_today(trade.get("closed_at"))
        )
        wins = sum(1 for trade in closed_trades if float(trade.get("pnl") or 0) > 0)
        win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0
        open_positions = len(self._filter_trades(mode_trades, {"OPEN", LIVE_ENTRY_SUBMITTED_STATUS, LIVE_EXIT_SUBMITTED_STATUS}))

        try:
            balance = self.bybit_service.get_account_balance()
            total_equity = float(balance.get("total_equity") or 0)
            available_cash = float(balance.get("total_available_balance") or 0)
        except Exception as exc:  # pragma: no cover - depends on live API access
            self.logger.error("Failed to read live Bybit balance for portfolio snapshot: %s", exc)
            total_equity = self.latest_portfolio_snapshot.total_equity if self.latest_portfolio_snapshot is not None else self.config.account_balance
            available_cash = 0.0

        return PortfolioSnapshot(
            execution_mode="live",
            total_equity=round(total_equity, 6),
            available_cash=round(available_cash, 6),
            realized_pnl_today=round(realized_pnl_today, 6),
            total_realized_pnl=round(total_realized_pnl, 6),
            unrealized_pnl=0.0,
            open_positions=open_positions,
            win_rate=round(win_rate, 2),
        )

    def _build_live_trade_record(
        self,
        *,
        signal: Signal,
        assessment: RiskAssessment,
        status: str,
        notes: str,
        metadata: dict[str, Any],
    ) -> TradeRecord:
        profile = assessment.risk_profile
        if profile is None:
            raise ValueError("A live trade record cannot be created without an accepted risk profile.")

        return TradeRecord(
            symbol=signal.symbol,
            interval=signal.interval,
            action=signal.action,
            execution_mode="live",
            entry=profile.entry,
            stop_loss=profile.stop_loss,
            take_profit=profile.take_profit,
            position_size=profile.position_size,
            confidence=signal.confidence,
            risk_reward_ratio=profile.risk_reward_ratio,
            notional_value=profile.notional_value,
            status=status,
            notes=notes,
            metadata=metadata,
        )

    def _save_post_close_coach_note(
        self,
        *,
        mode: str,
        closed_trade: dict[str, Any],
        mode_trades: list[dict[str, Any]],
        portfolio_snapshot: PortfolioSnapshot,
    ) -> None:
        if not self.ai_coach_service.is_available():
            return

        symbol = str(closed_trade.get("symbol") or "")
        interval = str(closed_trade.get("interval") or "")
        trade_id = closed_trade.get("id")
        open_trades = self._filter_trades(mode_trades, {"OPEN"})

        settings = {
            "execution_mode": mode,
            "bybit_environment": "testnet" if self.config.bybit_use_testnet else "mainnet",
            "traded_symbols": self.config.traded_symbols,
            "market_intervals": self.config.market_intervals,
            "risk_per_trade": self.config.risk_per_trade,
            "daily_loss_limit": self.config.daily_loss_limit,
            "confidence_threshold": self.config.confidence_threshold,
            "max_open_trades": self.config.max_open_trades,
            "min_order_notional": self.config.min_order_notional,
            "spot_only": True,
            "long_only": True,
            "paper_mode": mode == "paper",
            "order_placement_enabled": self.config.bybit_allow_order_placement,
            "manual_confirmation_required": self.config.bybit_require_manual_confirmation,
            "live_rollout": {
                "size_scale": self.config.live_tiny_size_scale,
                "max_order_notional": self.config.live_max_order_notional,
                "max_daily_entries": self.config.live_max_daily_entries,
            },
        }

        try:
            recommendation = self.ai_coach_service.generate_recommendation(
                signals=self.supabase_service.get_recent_signals(limit=6),
                open_trades=open_trades,
                portfolio_snapshot=portfolio_snapshot.to_dict(),
                recent_journal_entries=self.supabase_service.get_recent_journal_entries(limit=3),
                settings=settings,
            )
        except Exception as exc:
            self.logger.error("Post-close AI coach review failed for %s %s: %s", symbol, interval, exc)
            return

        journal_entry = self.paper_trading_service.build_journal_entry(
            title=f"AI coach (post-close): {recommendation.verdict}",
            body=self.ai_coach_service.format_for_journal(recommendation),
            symbol=symbol,
            related_trade_id=str(trade_id) if trade_id is not None else None,
            tags=[
                "ai-coach",
                "anthropic",
                "post-close",
                mode,
                recommendation.verdict.lower().replace("_", "-"),
            ],
        )
        self.supabase_service.save_journal_entry(journal_entry.to_dict())

        pnl = closed_trade.get("pnl")
        pnl_pct = closed_trade.get("pnl_pct")
        exit_price = closed_trade.get("exit_price")
        entry_price = closed_trade.get("entry")
        close_reason = str(closed_trade.get("notes") or "").split(".")[0]

        pnl_str = (
            f"{'+' if float(pnl) >= 0 else ''}{float(pnl):.2f}"
            f" ({'+' if float(pnl_pct) >= 0 else ''}{float(pnl_pct):.2f}%)"
            if pnl is not None and pnl_pct is not None
            else "N/A"
        )

        lines = [
            f"{mode.upper()} trade closed: {symbol} {interval}",
            f"Entry: {entry_price} -> Exit: {exit_price} | PnL: {pnl_str}",
        ]
        if close_reason:
            lines.append(f"Reason: {close_reason}")
        lines.append("")
        lines.append(self.ai_coach_service.format_for_telegram(recommendation))
        self.telegram_service.send_message("\n".join(lines))

    def _build_coach_note(
        self,
        *,
        mode: str,
        signal: Signal,
        open_trades: list[dict[str, Any]],
        portfolio_snapshot: PortfolioSnapshot,
    ):
        if not self.ai_coach_service.is_available():
            return None

        signals = [signal.to_dict()]
        for stored_signal in self.supabase_service.get_recent_signals(limit=5):
            if stored_signal.get("symbol") == signal.symbol and stored_signal.get("interval") == signal.interval:
                continue
            signals.append(stored_signal)

        settings = {
            "execution_mode": mode,
            "bybit_environment": "testnet" if self.config.bybit_use_testnet else "mainnet",
            "traded_symbols": self.config.traded_symbols,
            "market_intervals": self.config.market_intervals,
            "risk_per_trade": self.config.risk_per_trade,
            "daily_loss_limit": self.config.daily_loss_limit,
            "confidence_threshold": self.config.confidence_threshold,
            "max_open_trades": self.config.max_open_trades,
            "min_order_notional": self.config.min_order_notional,
            "spot_only": True,
            "long_only": True,
            "paper_mode": mode == "paper",
            "order_placement_enabled": self.config.bybit_allow_order_placement,
            "manual_confirmation_required": self.config.bybit_require_manual_confirmation,
            "live_rollout": {
                "size_scale": self.config.live_tiny_size_scale,
                "max_order_notional": self.config.live_max_order_notional,
                "max_daily_entries": self.config.live_max_daily_entries,
            },
        }

        try:
            return self.ai_coach_service.generate_recommendation(
                signals=signals,
                open_trades=open_trades,
                portfolio_snapshot=portfolio_snapshot.to_dict(),
                recent_journal_entries=self.supabase_service.get_recent_journal_entries(limit=3),
                settings=settings,
            )
        except Exception as exc:
            self.logger.error("AI coach review failed for %s %s: %s", signal.symbol, signal.interval, exc)
            return None

    @staticmethod
    def _trade_metadata(trade: dict[str, Any]) -> dict[str, Any]:
        metadata = trade.get("metadata")
        if isinstance(metadata, dict):
            return dict(metadata)
        return {}

    @staticmethod
    def _build_live_trade_metadata(**extra: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "lifecycle": "live_spot",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata.update(extra)
        return metadata

    def _apply_live_entry_fill(self, trade: dict[str, Any], metadata: dict[str, Any], order_snapshot: dict[str, Any]) -> dict[str, Any]:
        filled_trade = dict(trade)
        filled_quantity = self._coerce_float(order_snapshot.get("cumExecQty")) or float(trade.get("position_size") or 0)
        average_price = self._extract_order_average_price(order_snapshot, fallback=float(trade.get("entry") or 0))
        notional_value = self._coerce_float(order_snapshot.get("cumExecValue")) or round(filled_quantity * average_price, 6)

        filled_trade.update(
            {
                "status": "OPEN",
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "entry": round(average_price, 6),
                "position_size": round(filled_quantity, 6),
                "notional_value": round(notional_value, 6),
                "notes": "Live Bybit entry order filled.",
                "metadata": metadata,
            }
        )
        return filled_trade

    @staticmethod
    def _extract_order_average_price(order_snapshot: dict[str, Any], *, fallback: float) -> float:
        average_price = float(order_snapshot.get("avgPrice") or 0)
        if average_price > 0:
            return average_price

        executed_value = float(order_snapshot.get("cumExecValue") or 0)
        executed_qty = float(order_snapshot.get("cumExecQty") or 0)
        if executed_value > 0 and executed_qty > 0:
            return executed_value / executed_qty
        return fallback

    @staticmethod
    def _build_order_metadata(
        order_snapshot: dict[str, Any] | None,
        *,
        order_id: str,
        order_link_id: str,
    ) -> dict[str, Any]:
        if order_snapshot is None:
            return {
                "order_id": order_id,
                "order_link_id": order_link_id,
                "status": "UNKNOWN",
            }

        return {
            "order_id": order_id or str(order_snapshot.get("orderId") or ""),
            "order_link_id": order_link_id or str(order_snapshot.get("orderLinkId") or ""),
            "status": str(order_snapshot.get("orderStatus") or "UNKNOWN"),
            "avg_price": order_snapshot.get("avgPrice"),
            "cum_exec_qty": order_snapshot.get("cumExecQty"),
            "cum_exec_value": order_snapshot.get("cumExecValue"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _wait_for_order_snapshot(self, *, order_id: str | None, order_link_id: str | None) -> dict[str, Any] | None:
        if not order_id and not order_link_id:
            return None

        for attempt in range(3):
            try:
                snapshot = self.bybit_service.get_spot_order(order_id=order_id, order_link_id=order_link_id)
            except Exception as exc:  # pragma: no cover - live network timing
                self.logger.error("Failed to query Bybit order status for %s / %s: %s", order_id, order_link_id, exc)
                snapshot = None

            if snapshot is not None:
                return snapshot

            if attempt < 2:
                time.sleep(1.0)
        return None

    @staticmethod
    def _normalized_order_status(order_snapshot: dict[str, Any]) -> str:
        return str(order_snapshot.get("orderStatus") or "").strip().lower()

    def _is_order_filled(self, order_snapshot: dict[str, Any]) -> bool:
        return self._normalized_order_status(order_snapshot) in LIVE_FILLED_ORDER_STATUSES

    def _is_order_pending(self, order_snapshot: dict[str, Any]) -> bool:
        return self._normalized_order_status(order_snapshot) in LIVE_PENDING_ORDER_STATUSES

    def _is_order_failed(self, order_snapshot: dict[str, Any]) -> bool:
        return self._normalized_order_status(order_snapshot) in LIVE_FAILED_ORDER_STATUSES

    def _live_confirmation_expired(self, trade: dict[str, Any]) -> bool:
        metadata = self._trade_metadata(trade)
        expires_at = metadata.get("confirmation_expires_at")
        if not expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        return expiry <= datetime.now(timezone.utc)

    def _count_live_entries_today(self, mode_trades: list[dict[str, Any]]) -> int:
        count = 0
        for trade in mode_trades:
            if str(trade.get("execution_mode") or "") != "live":
                continue
            if str(trade.get("status") or "") == "CANCELLED":
                continue
            if self._is_today(trade.get("opened_at")):
                count += 1
        return count

    def _telegram_live_pending_response(self) -> str:
        if not self.config.live_trading_enabled:
            return "The live lane is disabled right now."

        pending_trades = self._filter_trades(
            self.supabase_service.get_recent_trades(limit=100, execution_mode="live"),
            {LIVE_PENDING_CONFIRMATION_STATUS},
        )
        if not pending_trades:
            return "There are no live trades waiting for confirmation."

        lines = ["Pending live confirmations:"]
        for trade in pending_trades[:5]:
            lines.append(
                f"- {self._short_trade_id(trade)} | {trade.get('symbol')} {trade.get('interval')} | "
                f"entry={trade.get('entry')} | notional={trade.get('notional_value')}"
            )
        if len(pending_trades) > 5:
            lines.append(f"...and {len(pending_trades) - 5} more.")
        lines.append("Use /live_confirm <id> or /live_cancel <id>.")
        return "\n".join(lines)

    def _telegram_live_confirm_response(self, arguments: str) -> str:
        if not self.config.live_trading_enabled:
            return "The live lane is disabled right now."
        if not self.config.bybit_allow_order_placement:
            return "BYBIT_ALLOW_ORDER_PLACEMENT is false, so live confirmations cannot submit orders."

        trade, error = self._resolve_live_confirmation_trade(arguments)
        if trade is None:
            return error
        if self._live_confirmation_expired(trade):
            return "That live confirmation has already expired. No order was sent."

        return self._submit_live_entry_order(trade, confirmation_source="telegram")

    def _telegram_live_cancel_response(self, arguments: str) -> str:
        if not self.config.live_trading_enabled:
            return "The live lane is disabled right now."

        trade, error = self._resolve_live_confirmation_trade(arguments)
        if trade is None:
            return error

        cancelled_trade = dict(trade)
        cancelled_trade.update(
            {
                "status": "CANCELLED",
                "notes": "Cancelled manually before sending the live Bybit order.",
                "metadata": self._trade_metadata(trade),
            }
        )
        trade_id = cancelled_trade.get("id")
        if trade_id is None:
            return "Pending live confirmation is missing an id and cannot be cancelled safely."

        if not self.supabase_service.update_trade(trade_id, cancelled_trade):
            return "Failed to cancel the live confirmation. Check the engine logs."

        self.supabase_service.save_journal_entry(
            self.paper_trading_service.build_journal_entry(
                title="Live confirmation cancelled",
                body=f"Cancelled the pending live trade for {trade.get('symbol')} on {trade.get('interval')} before any Bybit order was sent.",
                symbol=str(trade.get("symbol") or ""),
                related_trade_id=str(trade_id),
                tags=["live", "confirmation-cancelled", str(trade.get("interval") or "").lower()],
            ).to_dict()
        )
        return f"Cancelled the live confirmation for {trade.get('symbol')} {trade.get('interval')}. No order was sent."

    def _resolve_live_confirmation_trade(self, arguments: str) -> tuple[dict[str, Any] | None, str]:
        pending_trades = self._filter_trades(
            self.supabase_service.get_recent_trades(limit=100, execution_mode="live"),
            {LIVE_PENDING_CONFIRMATION_STATUS},
        )
        if not pending_trades:
            return None, "There are no live trades waiting for confirmation."

        lookup = arguments.strip().lower()
        if not lookup:
            if len(pending_trades) == 1:
                return pending_trades[0], ""
            return None, "Multiple live confirmations exist. Use /live_pending and then /live_confirm <id> or /live_cancel <id>."

        matches = [
            trade
            for trade in pending_trades
            if str(trade.get("id") or "").lower().startswith(lookup)
        ]
        if not matches:
            return None, f"No live confirmation matched '{lookup}'. Use /live_pending to see the available ids."
        if len(matches) > 1:
            return None, f"'{lookup}' matched multiple live confirmations. Provide a longer id prefix."
        return matches[0], ""

    @staticmethod
    def _filter_trades(trades: list[dict[str, Any]], statuses: set[str]) -> list[dict[str, Any]]:
        return [trade for trade in trades if str(trade.get("status") or "") in statuses]

    @staticmethod
    def _find_trade(
        trades: list[dict[str, Any]],
        *,
        symbol: str,
        interval: str,
        statuses: set[str],
    ) -> dict[str, Any] | None:
        for trade in trades:
            if trade.get("symbol") == symbol and trade.get("interval") == interval and str(trade.get("status") or "") in statuses:
                return trade
        return None

    def _active_trade_statuses(self, mode: str) -> set[str]:
        if mode == "live":
            return set(LIVE_ACTIVE_STATUSES)
        return {"OPEN"}

    def _risk_blocking_trade_statuses(self, mode: str) -> set[str]:
        if mode == "live":
            return set(LIVE_RISK_BLOCKING_STATUSES)
        return {"OPEN"}

    def _risk_open_trade_count(self, mode: str, mode_trades: list[dict[str, Any]]) -> int:
        return len(self._filter_trades(mode_trades, self._risk_blocking_trade_statuses(mode)))

    @staticmethod
    def _replace_trade(trades: list[dict[str, Any]], updated_trade: dict[str, Any]) -> None:
        updated_id = updated_trade.get("id")
        replaced = False
        for index, trade in enumerate(trades):
            if trade.get("id") == updated_id:
                trades[index] = updated_trade
                replaced = True
                break
        if not replaced:
            trades.append(updated_trade)

    @staticmethod
    def _short_trade_id(trade: dict[str, Any]) -> str:
        trade_id = str(trade.get("id") or "pending")
        return trade_id[:8]

    @staticmethod
    def _is_today(value: object) -> bool:
        if not value:
            return False
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
        return timestamp.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()

    def _telegram_signal_response(self) -> str:
        signals = self.supabase_service.get_recent_signals(limit=3)
        if not signals:
            return "No signals have been stored yet."
        lines = ["Latest signals:"]
        for signal in signals:
            lines.append(
                f"- {signal.get('symbol')} {signal.get('interval')} | {signal.get('action')} | {signal.get('confidence')}% | eligible={signal.get('eligible')}"
            )
        return "\n".join(lines)

    def _telegram_performance_response(self) -> str:
        summaries: list[str] = []
        for mode in self._enabled_execution_modes():
            latest = self._latest_performance_log_for_mode(mode)
            if latest is None:
                continue
            summaries.append(
                (
                    f"{mode.upper()} lane:\n"
                    f"Total Equity: {latest.get('total_equity')}\n"
                    f"Available Cash: {latest.get('available_cash')}\n"
                    f"Today PnL: {latest.get('realized_pnl_today')}\n"
                    f"Open Positions: {latest.get('open_positions')}\n"
                    f"Win Rate: {latest.get('win_rate')}%"
                )
            )

        if not summaries:
            return "No performance logs are available yet."
        return "Performance snapshot:\n" + "\n\n".join(summaries)

    def _telegram_status_response(self, snapshot: PortfolioSnapshot) -> str:
        bybit_status = "Not checked"
        if self.config.bybit_api_key and self.config.bybit_api_secret:
            try:
                balance = self.bybit_service.get_account_balance()
                bybit_status = (
                    f"Bybit {balance.get('account_type')} | Equity {balance.get('total_equity')} | "
                    f"Available {balance.get('total_available_balance')}"
                )
            except Exception as exc:  # pragma: no cover - live status probe
                bybit_status = f"Bybit balance unavailable: {exc}"

        posture = "Read-only"
        if self.config.bybit_allow_order_placement and self.config.bybit_require_manual_confirmation:
            posture = "Manual live confirmations"
        elif self.config.bybit_allow_order_placement:
            posture = "Live order placement enabled"

        lane_status = ", ".join(mode.upper() for mode in self._enabled_execution_modes())
        paper_snapshot = self._snapshot_for_mode("paper") if self.config.paper_trading_enabled else None
        live_snapshot = self._snapshot_for_mode("live") if self.config.live_trading_enabled else None
        lines = [
            "Engine status:",
            f"Execution Mode: {self.config.execution_mode}",
            f"Enabled Lanes: {lane_status}",
            f"Bybit Environment: {'testnet' if self.config.bybit_use_testnet else 'mainnet'}",
            f"Live Posture: {posture}",
            f"Tracked Symbols: {', '.join(self.config.traded_symbols)}",
        ]
        if paper_snapshot is not None:
            lines.extend(
                [
                    f"PAPER Equity: {paper_snapshot.total_equity}",
                    f"PAPER Available Cash: {paper_snapshot.available_cash}",
                    f"PAPER Daily PnL: {paper_snapshot.realized_pnl_today}",
                    f"PAPER Open Positions: {paper_snapshot.open_positions}",
                ]
            )
        if live_snapshot is not None:
            lines.extend(
                [
                    f"LIVE Equity: {live_snapshot.total_equity}",
                    f"LIVE Available Cash: {live_snapshot.available_cash}",
                    f"LIVE Open Positions: {live_snapshot.open_positions}",
                ]
            )
        if paper_snapshot is None and live_snapshot is None:
            lines.extend(
                [
                    f"Snapshot Equity: {snapshot.total_equity}",
                    f"Snapshot Available Cash: {snapshot.available_cash}",
                    f"Snapshot Daily PnL: {snapshot.realized_pnl_today}",
                    f"Snapshot Open Positions: {snapshot.open_positions}",
                ]
            )
        lines.extend(
            [
                f"Live Rollout: {self.config.live_tiny_size_scale:.0%} size scale | max notional {self.config.live_max_order_notional:.2f} | max daily entries {self.config.live_max_daily_entries}",
                f"Exchange Status: {bybit_status}",
            ]
        )
        return "\n".join(lines)

    def _telegram_journal_response(self) -> str:
        entries = self.supabase_service.get_recent_journal_entries(limit=3)
        if not entries:
            return "No journal entries have been saved yet."
        lines = ["Recent journal entries:"]
        for entry in entries:
            lines.append(f"- {entry.get('timestamp')}: {entry.get('title')}")
        return "\n".join(lines)

    def _sync_telegram_commands_if_needed(self) -> None:
        if not self.config.telegram_polling_enabled or self.telegram_commands_synced:
            return
        self.telegram_commands_synced = self.telegram_service.sync_commands()

    def _poll_telegram_updates(self) -> None:
        if not self.config.telegram_polling_enabled:
            return
        self._sync_telegram_commands_if_needed()
        self.telegram_service.process_updates(self._build_telegram_command_handlers())

    def _build_telegram_command_handlers(self) -> dict[str, Callable[[str], str]]:
        coach_mode = self._coach_mode()
        return {
            "signal": lambda _args: self._telegram_signal_response(),
            "live_pending": lambda _args: self._telegram_live_pending_response(),
            "live_confirm": self._telegram_live_confirm_response,
            "live_cancel": self._telegram_live_cancel_response,
            "performance": lambda _args: self._telegram_performance_response(),
            "status": lambda _args: self._telegram_status_response(self._telegram_snapshot()),
            "journal": lambda _args: self._telegram_journal_response(),
            "coach": lambda _args: self._telegram_coach_response(
                self._snapshot_for_mode(coach_mode) or self._telegram_snapshot(),
                self.supabase_service.get_open_trades(coach_mode),
            ),
        }

    def _telegram_snapshot(self) -> PortfolioSnapshot:
        if self.latest_portfolio_snapshot is not None:
            return self.latest_portfolio_snapshot

        preferred_mode = self._preferred_signal_mode(self._enabled_execution_modes())
        latest_log = self._latest_performance_log_for_mode(preferred_mode)
        if latest_log is None:
            starting_balance = self.config.paper_starting_balance if preferred_mode == "paper" else self.config.account_balance
            return PortfolioSnapshot(
                execution_mode=preferred_mode,  # type: ignore[arg-type]
                total_equity=starting_balance,
                available_cash=starting_balance if preferred_mode == "paper" else 0.0,
                realized_pnl_today=0.0,
                total_realized_pnl=0.0,
                unrealized_pnl=0.0,
                open_positions=0,
                win_rate=0.0,
            )

        return PortfolioSnapshot(
            execution_mode=preferred_mode,  # type: ignore[arg-type]
            total_equity=self._coerce_float(latest_log.get("total_equity")),
            available_cash=self._coerce_float(latest_log.get("available_cash")),
            realized_pnl_today=self._coerce_float(latest_log.get("realized_pnl_today")),
            total_realized_pnl=self._coerce_float(latest_log.get("total_realized_pnl")),
            unrealized_pnl=self._coerce_float(latest_log.get("unrealized_pnl")),
            open_positions=int(latest_log.get("open_positions") or 0),
            win_rate=self._coerce_float(latest_log.get("win_rate")),
        )

    def _latest_performance_log_for_mode(self, mode: str) -> dict[str, Any] | None:
        return self.supabase_service.get_latest_performance_log_for_mode(mode)

    def _snapshot_for_mode(self, mode: str) -> PortfolioSnapshot | None:
        latest_log = self._latest_performance_log_for_mode(mode)
        if latest_log is None:
            return None
        return PortfolioSnapshot(
            execution_mode=mode,  # type: ignore[arg-type]
            total_equity=self._coerce_float(latest_log.get("total_equity")),
            available_cash=self._coerce_float(latest_log.get("available_cash")),
            realized_pnl_today=self._coerce_float(latest_log.get("realized_pnl_today")),
            total_realized_pnl=self._coerce_float(latest_log.get("total_realized_pnl")),
            unrealized_pnl=self._coerce_float(latest_log.get("unrealized_pnl")),
            open_positions=int(latest_log.get("open_positions") or 0),
            win_rate=self._coerce_float(latest_log.get("win_rate")),
        )

    def _coach_mode(self) -> str:
        if self.config.paper_trading_enabled:
            return "paper"
        if self.config.live_trading_enabled:
            return "live"
        return self.config.execution_mode

    @staticmethod
    def _coerce_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _coerce_indicator_float(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if pd.isna(numeric) else numeric

    def _telegram_coach_response(self, snapshot: PortfolioSnapshot, open_trades: list[dict[str, Any]]) -> str:
        if not self.ai_coach_service.is_available():
            return self.ai_coach_service.unavailable_reason()

        signals = self.supabase_service.get_recent_signals(limit=6)
        journal_entries = self.supabase_service.get_recent_journal_entries(limit=3)
        coach_mode = str(snapshot.execution_mode)
        settings = {
            "execution_mode": coach_mode,
            "bybit_environment": "testnet" if self.config.bybit_use_testnet else "mainnet",
            "traded_symbols": self.config.traded_symbols,
            "market_intervals": self.config.market_intervals,
            "risk_per_trade": self.config.risk_per_trade,
            "daily_loss_limit": self.config.daily_loss_limit,
            "confidence_threshold": self.config.confidence_threshold,
            "max_open_trades": self.config.max_open_trades,
            "min_order_notional": self.config.min_order_notional,
            "spot_only": True,
            "long_only": True,
            "paper_mode": coach_mode == "paper",
            "order_placement_enabled": self.config.bybit_allow_order_placement,
            "manual_confirmation_required": self.config.bybit_require_manual_confirmation,
            "live_rollout": {
                "size_scale": self.config.live_tiny_size_scale,
                "max_order_notional": self.config.live_max_order_notional,
                "max_daily_entries": self.config.live_max_daily_entries,
            },
        }

        try:
            recommendation = self.ai_coach_service.generate_recommendation(
                signals=signals,
                open_trades=open_trades,
                portfolio_snapshot=snapshot.to_dict(),
                recent_journal_entries=journal_entries,
                settings=settings,
            )
        except Exception as exc:
            self.logger.error("AI coach request failed: %s", exc)
            return "AI coach is unavailable right now. Check the engine logs and confirm the Anthropic key and network access."

        journal_entry = self.paper_trading_service.build_journal_entry(
            title=f"AI coach: {recommendation.verdict}",
            body=self.ai_coach_service.format_for_journal(recommendation),
            symbol=recommendation.focus_symbol,
            tags=[
                "ai-coach",
                "anthropic",
                coach_mode,
                recommendation.verdict.lower().replace("_", "-"),
            ],
        )
        self.supabase_service.save_journal_entry(journal_entry.to_dict())
        return self.ai_coach_service.format_for_telegram(recommendation)


def main() -> None:
    bootstrap_logger = logging.getLogger("trading_engine_bootstrap")
    bootstrap_logger.setLevel(logging.INFO)
    bootstrap_logger.handlers.clear()
    bootstrap_logger.addHandler(logging.StreamHandler())

    try:
        config = load_config()
        logger = configure_logging(config.log_level, config.log_file_path)
        engine = TradingEngine(
            config=config,
            logger=logger,
            strategy=TrendRsiStrategy(
                breakout_lookback=config.breakout_lookback,
                volume_confirmation_ratio=config.volume_confirmation_ratio,
            ),
        )
        engine.run()
    except KeyboardInterrupt:
        bootstrap_logger.info("Trading engine stopped by user.")
    except Exception as exc:
        bootstrap_logger.exception("Trading engine failed to start: %s", exc)
        raise SystemExit(1) from exc
