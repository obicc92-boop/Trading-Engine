"""The rails — doctrine section 6.

Small on purpose. The rule itself carries the risk management version 1 needed a module
for: there is no stop to size against, no heat to add up, no streak to count. What is left
is the set of reasons purchases may not happen, and none of them ever stops a sale — a coin
held against a broken trend is exactly what the rule exists to prevent.

Every block carries a human-readable reason, because "no trade" without an explanation is
indistinguishable from a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Position


@dataclass(slots=True)
class AccountState:
    """Everything the rails need to know, recomputed from source every cycle."""

    equity: float
    cash: float
    positions: list[Position] = field(default_factory=list)
    realized_today: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    synced: bool = True       # False when the balances could not be read: cash is unknown

    @property
    def invested(self) -> float:
        return self.equity - self.cash

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.peak_equity)


def buy_blocks(state: AccountState, paused: bool) -> list[str]:
    """Reasons purchases are currently forbidden. Sales are never blocked."""
    blocks: list[str] = []
    if state.halted:
        blocks.append(state.halt_reason or "engine halted")
    if paused:
        blocks.append("paused by hand")
    return blocks


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
