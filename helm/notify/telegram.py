"""Telegram notifications and remote control.

Two jobs, deliberately separated because they fail differently.

Outbound alerts go through a queue drained by a daemon thread. Nothing in the trading loop
ever waits on Telegram: if the API hangs for thirty seconds, or the token is wrong, or the
network is down, the engine keeps trading and the messages are dropped. A notifier that can
stall the cycle is worse than no notifier at all — the whole point is to watch a system that
is managing real money without you.

Inbound commands are polled on a second thread and are locked to one Telegram user. They
can pause, flatten and kill, but they cannot buy or loosen a limit. The worst a leaked token
can do is sell early, which costs opportunity rather than capital — an acceptable price for
being able to hit the brakes from a phone.
"""

from __future__ import annotations

import html
import logging
import queue
import re
import threading
from typing import Any, Callable

import httpx

log = logging.getLogger("helm.telegram")

API = "https://api.telegram.org"

# Which narration levels are worth a phone buzz. `info` is the engine talking to itself —
# forwarding it would train you to ignore the channel within a day.
ALERT_LEVELS = {"entry", "win", "loss", "warn", "error"}

ICONS = {
    "entry": "🟦", "win": "🟩", "loss": "🟥",
    "warn": "🟨", "error": "🔴", "info": "▫️",
}


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        allowed_user_id: str = "",
        commands_enabled: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.token = token.strip()
        self.chat_id = str(chat_id).strip()
        # Defaults to the configured chat, so a private chat is locked to its owner without
        # any extra configuration.
        self.allowed_user_id = str(allowed_user_id or chat_id).strip()
        self.commands_enabled = commands_enabled
        self.timeout = timeout

        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._sender: threading.Thread | None = None
        self._poller: threading.Thread | None = None
        self._offset = 0
        self._poll_refused = False
        self._handlers: dict[str, Callable[[], str]] = {}
        self._client = httpx.Client(timeout=timeout)
        self.sent = 0
        self.dropped = 0
        self.failures = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    # -- lifecycle -----------------------------------------------------------------------
    def start(
        self,
        handlers: dict[str, Callable[[], str]] | None = None,
        menu: dict[str, str] | None = None,
    ) -> None:
        """Start sending, and answering commands. `menu` maps each command to the one-line
        description Telegram shows when "/" is typed."""
        if not self.enabled:
            log.info("telegram not configured — no alerts will be sent")
            return
        self._handlers = handlers or {}
        self._stop.clear()

        self._sender = threading.Thread(target=self._drain, name="helm-tg-send", daemon=True)
        self._sender.start()

        if self.commands_enabled and self._handlers:
            # Skip whatever arrived while the engine was down. Acting on a `/flatten` sent
            # six hours ago, for a position that no longer exists, is worse than ignoring it.
            self._discard_backlog()
            if menu:
                self._set_menu(menu)
            self._poller = threading.Thread(target=self._poll, name="helm-tg-poll", daemon=True)
            self._poller.start()

    def _set_menu(self, menu: dict[str, str]) -> None:
        """Register the command menu from the code, so it cannot drift from what is answered."""
        try:
            self._client.post(
                f"{API}/bot{self.token}/setMyCommands",
                json={"commands": [{"command": name, "description": text} for name, text in menu.items()]},
            )
        except Exception as exc:
            log.debug("could not set the telegram command menu: %s", exc)

    def stop(self) -> None:
        self._stop.set()

    # -- outbound ------------------------------------------------------------------------
    def notify(self, message: str, level: str = "info") -> None:
        """Queue an alert. Never blocks, never raises, never waits on the network."""
        if not self.enabled or level not in ALERT_LEVELS:
            return
        try:
            self._queue.put_nowait((message, level))
        except queue.Full:
            # A backed-up queue means Telegram is unreachable. Dropping is correct: the
            # alternative is an unbounded backlog that eventually delivers a burst of stale
            # messages about trades that closed hours ago.
            self.dropped += 1

    def send_now(self, message: str, level: str = "info") -> bool:
        """Send synchronously. Only for command replies, which are already off the hot path."""
        if not self.enabled:
            return False
        return self._post(self._format(message, level))

    def _drain(self) -> None:
        while not self._stop.is_set():
            self._drain_once(timeout=1.0)

    def _drain_once(self, timeout: float = 0.0) -> bool:
        """Send at most one queued alert. Returns False if there was nothing to send.

        Split out from the loop so the send path can be exercised synchronously in tests —
        the thread and the test run exactly the same code, including the swallow-everything
        guard that keeps a Telegram failure from killing the sender.
        """
        try:
            message, level = self._queue.get(timeout=timeout) if timeout else self._queue.get_nowait()
        except queue.Empty:
            return False
        try:
            self._post(self._format(message, level))
        except Exception as exc:                      # never kill the sender thread
            self.failures += 1
            log.debug("telegram send failed: %s", exc)
        return True

    # Telegram's hard limit is 4096 characters; leave room for the icon and an ellipsis.
    MAX_TEXT = 4000

    def _format(self, message: str, level: str) -> str:
        """Escape, then trim to Telegram's limit without splitting an HTML entity.

        Order matters. Escaping expands text — one `&` becomes five characters — so
        trimming the raw message first does not bound the result. Trimming afterwards can
        land mid-entity and leave a dangling `&l`, which Telegram rejects as bad markup, so
        any trailing partial entity is removed after the cut.
        """
        body = html.escape(message)
        if len(body) > self.MAX_TEXT:
            body = re.sub(r"&[a-zA-Z#][a-zA-Z0-9]*$", "", body[: self.MAX_TEXT]) + "…"
        return f"{ICONS.get(level, '▫️')} {body}"

    def _post(self, text: str) -> bool:
        try:
            response = self._client.post(
                f"{API}/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,             # already bounded by _format
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            self.failures += 1
            log.debug("telegram unreachable: %s", exc)
            return False
        if response.status_code >= 400:
            self.failures += 1
            log.warning("telegram rejected a message: %s %s", response.status_code, response.text[:200])
            return False
        self.sent += 1
        return True

    # -- inbound -------------------------------------------------------------------------
    def _discard_backlog(self) -> None:
        try:
            data = self._client.get(
                f"{API}/bot{self.token}/getUpdates", params={"timeout": 0, "offset": -1}
            ).json()
            for update in data.get("result", []):
                self._offset = max(self._offset, int(update["update_id"]) + 1)
        except Exception as exc:
            log.debug("could not clear telegram backlog: %s", exc)

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                response = self._client.get(
                    f"{API}/bot{self.token}/getUpdates",
                    params={"timeout": 25, "offset": self._offset},
                    timeout=35.0,
                )
                data = response.json()
            except Exception as exc:
                log.debug("telegram poll failed: %s", exc)
                # Back off rather than hammer a dead endpoint; the loop is a daemon and
                # must not spin hot if Telegram is down for hours.
                self._stop.wait(10.0)
                continue

            if not data.get("ok", False):
                # Usually a 409: another process is polling with the same token, and every
                # command it reads first never reaches this engine. Telegram answers that
                # instantly, so without the pause this loop spins hot for as long as the other
                # process runs. Warn once per streak rather than once per refusal.
                if not self._poll_refused:
                    log.warning("telegram refused the command poll: %s", data.get("description", ""))
                self._poll_refused = True
                self._stop.wait(10.0)
                continue
            self._poll_refused = False

            for update in data.get("result", []):
                self._offset = max(self._offset, int(update["update_id"]) + 1)
                try:
                    self._handle(update)
                except Exception as exc:
                    log.warning("telegram command failed: %s", exc)

    def _handle(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        sender = str((message.get("from") or {}).get("id", ""))
        chat = str((message.get("chat") or {}).get("id", ""))
        if sender != self.allowed_user_id and chat != self.chat_id:
            log.warning("ignoring telegram command from unauthorised id %s", sender)
            return

        # "/flatten@MyBot arg" -> "flatten"
        command = text.split()[0].lstrip("/").split("@")[0].lower()
        handler = self._handlers.get(command)
        if handler is None:
            known = ", ".join(f"/{name}" for name in sorted(self._handlers))
            self.send_now(f"Unknown command. Try: {known}")
            return
        self.send_now(handler())

    def stats(self) -> dict[str, int]:
        return {"sent": self.sent, "dropped": self.dropped, "failures": self.failures}
