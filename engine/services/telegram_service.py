from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import requests

from engine.models import Signal
from engine.utils.retry import retry


CommandHandler = Callable[[str], str]


@dataclass(slots=True)
class TelegramService:
    bot_token: str
    chat_id: str
    logger: logging.Logger
    timeout_seconds: int
    allowed_user_id: str | None = None
    session: requests.Session = field(init=False, repr=False)
    last_update_id: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.session = requests.Session()

    def sync_commands(self) -> bool:
        commands = [
            {"command": "signal", "description": "Show the latest approved trading signals"},
            {"command": "live_pending", "description": "List live trades waiting for manual confirmation"},
            {"command": "live_confirm", "description": "Confirm a pending live trade by id or id prefix"},
            {"command": "live_cancel", "description": "Cancel a pending live trade by id or id prefix"},
            {"command": "performance", "description": "Show paper trading performance summary"},
            {"command": "status", "description": "Show engine status and safety mode"},
            {"command": "journal", "description": "Show the latest journal entries"},
            {"command": "coach", "description": "Ask the AI coach for a conservative next-step review"},
        ]

        try:
            self._post_api_call("setMyCommands", {"commands": commands})
            self.logger.info("Telegram bot commands synced.")
            return True
        except requests.RequestException as exc:
            self.logger.error("Failed to sync Telegram commands: %s", exc)
            return False

    def send_message(self, message: str, *, chat_id: str | None = None) -> bool:
        payload = {"chat_id": chat_id or self.chat_id, "text": message}
        try:
            self._post_api_call("sendMessage", payload)
            return True
        except requests.RequestException as exc:
            self.logger.error("Failed to send Telegram message: %s", exc)
            return False

    def send_signal_alert(self, signal: Signal, *, lane_label: str | None = None) -> bool:
        message = self._format_signal_message(signal, lane_label=lane_label)
        sent = self.send_message(message)
        if sent:
            self.logger.info("Telegram alert sent for %s %s.", signal.symbol, signal.interval)
        return sent

    def process_updates(self, command_handlers: dict[str, CommandHandler]) -> None:
        try:
            updates = self._get_updates()
        except requests.RequestException as exc:
            self.logger.error("Failed to fetch Telegram updates: %s", exc)
            return

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self.last_update_id = update_id + 1

            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            if not text.startswith("/"):
                continue

            if self.allowed_user_id is not None:
                from_user = message.get("from") or {}
                if str(from_user.get("id")) != self.allowed_user_id:
                    self.logger.warning("Ignoring Telegram command from unauthorized user id %s.", from_user.get("id"))
                    continue

            command = text.split()[0][1:].split("@")[0].lower()
            arguments = text.partition(" ")[2].strip()
            handler = command_handlers.get(command)
            self.logger.info(
                "Received Telegram command '%s' from chat %s.",
                command,
                message.get("chat", {}).get("id", self.chat_id),
            )
            if handler is None:
                self.send_message(
                    "Unknown command. Try /signal, /live_pending, /live_confirm, /live_cancel, /performance, /status, /journal, or /coach.",
                    chat_id=str(message.get("chat", {}).get("id", self.chat_id)),
                )
                continue

            try:
                response_text = handler(arguments)
            except Exception as exc:  # pragma: no cover - runtime command guard
                self.logger.exception("Telegram command '%s' failed: %s", command, exc)
                response_text = "The command failed. Check the engine logs for details."
            self.send_message(response_text, chat_id=str(message.get("chat", {}).get("id", self.chat_id)))

    @retry(attempts=3, delay_seconds=1.0, backoff=2.0, exceptions=(requests.RequestException,))
    def _post_api_call(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        response = self.session.post(
            f"https://api.telegram.org/bot{self.bot_token}/{method}",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        response_json = response.json()
        if not response_json.get("ok", False):
            raise requests.RequestException(str(response_json))
        return response_json

    @retry(attempts=3, delay_seconds=1.0, backoff=2.0, exceptions=(requests.RequestException,))
    def _get_updates(self) -> list[dict[str, object]]:
        params: dict[str, object] = {"timeout": 0}
        if self.last_update_id is not None:
            params["offset"] = self.last_update_id

        response = self.session.get(
            f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        response_json = response.json()
        if not response_json.get("ok", False):
            raise requests.RequestException(str(response_json))
        return list(response_json.get("result", []))

    @staticmethod
    def _format_signal_message(signal: Signal, *, lane_label: str | None = None) -> str:
        rejection_lines = "\n".join(f"- {reason}" for reason in signal.rejection_reasons)
        warning_lines = "\n".join(f"- {warning}" for warning in signal.warnings)
        header = "\U0001F680 TRADE SIGNAL"
        if lane_label:
            header = f"\U0001F680 {lane_label.upper()} TRADE SIGNAL"

        sections = [
            header,
            "",
            f"Pair: {signal.symbol}",
            f"Timeframe: {signal.interval}",
            f"Action: {signal.action}",
            f"Confidence: {signal.confidence}%",
            "",
            f"Entry: {signal.entry if signal.entry is not None else 'N/A'}",
            f"Stop Loss: {signal.stop_loss if signal.stop_loss is not None else 'N/A'}",
            f"Take Profit: {signal.take_profit if signal.take_profit is not None else 'N/A'}",
            f"Position Size: {signal.position_size if signal.position_size is not None else 'N/A'}",
            "",
            "Reason:",
            signal.reason,
        ]

        if warning_lines:
            sections.extend(["", "Warnings:", warning_lines])
        if rejection_lines:
            sections.extend(["", "Rejected Because:", rejection_lines])

        return "\n".join(sections)
