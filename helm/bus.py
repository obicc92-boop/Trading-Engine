"""A one-way event bus from the engine thread to connected browsers.

The engine runs in a plain thread; the API runs in an asyncio loop. Events cross that
boundary exactly once, here, via `call_soon_threadsafe`. Keeping the crossing in a single
place is what stops the rest of the codebase from having to think about threads.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any

from .models import clean, now_ms


class EventBus:
    def __init__(self, history: int = 400) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once from the API layer when the server loop is up."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        # Bounded: a browser tab that stops reading must not grow memory without limit.
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def recent(self, limit: int = 120) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)[-limit:]

    def publish(self, kind: str, payload: Any = None, ts: int | None = None) -> dict[str, Any]:
        """Safe to call from any thread."""
        event = {"kind": kind, "ts": ts or now_ms(), "data": clean(payload)}
        with self._lock:
            self._history.append(event)
            targets = list(self._subscribers)
            loop = self._loop
        if loop is not None and targets:
            loop.call_soon_threadsafe(self._fanout, event, targets)
        return event

    def _fanout(self, event: dict[str, Any], targets: list[asyncio.Queue]) -> None:
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow consumer loses the oldest event rather than stalling the engine.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    pass


bus = EventBus()
