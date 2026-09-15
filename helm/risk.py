"""Risk rails and position sizing.

This module has veto power over every other module and takes instructions from none of
them. The decision layer can decline a trade it permits and can shrink a size it computes,
but nothing in this system can widen a limit set here. That asymmetry is deliberate and it
is the main safety property of the whole design.

Every rejection carries a human-readable reason, because "no trade" without an explanation
is indistinguishable from a bug.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import doctrine
from .models import Candidate, Position, clean

log = logging.getLogger("helm.risk")


@dataclass(slots=True)
class AccountState:
    """Everything the rails need to know, recomputed from source every cycle."""

    equity: float
    cash: float
    positions: list[Position] = field(default_factory=list)
    realized_today: float = 0.0
    realized_week: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    entries_today: int = 0
    cooldowns: dict[str, int] = field(default_factory=dict)   # symbol -> unix ms
    loss_cooldown_until: int = 0
    halted: bool = False
    halt_reason: str = ""

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def open_risk(self) -> float:
        """Portfolio heat: what it costs if every open stop is hit.

        Uses the *current* stop, so a position that has trailed to breakeven correctly
        stops consuming heat and frees capacity for a new idea.
        """
        total = 0.0
        for position in self.positions:
            risk = (position.entry_price - position.stop) * position.qty
            total += max(0.0, risk)
        return total


@dataclass(slots=True)
class Sizing:
    quote_amount: float
    qty: float
    risk_amount: float
    stop: float
    stop_distance: float
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RiskVerdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    sizing: Sizing | None = None

    def to_dict(self) -> dict:
        return clean(
            {
                "allowed": self.allowed,
                "reasons": self.reasons,
                "sizing": {
                    "quote_amount": self.sizing.quote_amount,
                    "qty": self.sizing.qty,
                    "risk_amount": self.sizing.risk_amount,
                    "stop": self.sizing.stop,
                    "stop_distance": self.sizing.stop_distance,
                    "notes": self.sizing.notes,
                }
                if self.sizing
                else None,
            }
        )


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def utc_week_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return utc_day_start(now) - timedelta(days=now.weekday())


class RiskEngine:
    """Stateless with respect to the account — everything comes in via `AccountState`."""

    def gate_entries(self, state: AccountState, now_ms_value: int) -> list[str]:
        """Reasons new entries are currently forbidden.

        Open positions keep being managed regardless of what trips here. Halting entry is a
        circuit breaker; abandoning a live trade would be a different and worse decision.
        """
        blocks: list[str] = []

        if state.halted:
            blocks.append(state.halt_reason or "engine halted")

        if state.drawdown >= doctrine.MAX_DRAWDOWN_HALT:
            blocks.append(
                f"drawdown {state.drawdown*100:.1f}% is at or past the "
                f"{doctrine.MAX_DRAWDOWN_HALT*100:.0f}% hard stop — needs a human to restart"
            )

        if state.equity > 0:
            daily_loss = max(0.0, -state.realized_today) / state.equity
            if daily_loss >= doctrine.DAILY_LOSS_HALT:
                blocks.append(
                    f"down {daily_loss*100:.1f}% today — no new entries until 00:00 UTC"
                )
            weekly_loss = max(0.0, -state.realized_week) / state.equity
            if weekly_loss >= doctrine.WEEKLY_LOSS_HALT:
                blocks.append(f"down {weekly_loss*100:.1f}% this week — entries paused")

        if state.consecutive_losses >= doctrine.CONSECUTIVE_LOSS_LIMIT and now_ms_value < state.loss_cooldown_until:
            remaining = (state.loss_cooldown_until - now_ms_value) / 3_600_000
            blocks.append(
                f"{state.consecutive_losses} losses in a row — cooling off for {remaining:.1f}h"
            )

        if len(state.positions) >= doctrine.MAX_OPEN_POSITIONS:
            blocks.append(f"already holding the maximum of {doctrine.MAX_OPEN_POSITIONS} positions")

        if state.entries_today >= doctrine.MAX_ENTRIES_PER_DAY:
            blocks.append(f"{state.entries_today} entries today is the daily cap")

        if state.open_risk >= doctrine.MAX_PORTFOLIO_HEAT * state.equity:
            blocks.append(
                f"portfolio heat {state.open_risk:.2f} is at the "
                f"{doctrine.MAX_PORTFOLIO_HEAT*100:.1f}% ceiling"
            )

        return blocks

    def evaluate(
        self,
        candidate: Candidate,
        state: AccountState,
        now_ms_value: int,
        *,
        size_multiplier: float = 1.0,
        stop_override: float | None = None,
        instrument=None,
    ) -> RiskVerdict:
        """Decide whether this specific candidate may be taken, and at what size."""
        reasons = self.gate_entries(state, now_ms_value)

        if any(position.symbol == candidate.symbol for position in state.positions):
            reasons.append(f"already holding {candidate.symbol} — no adding to an open position")

        cooldown_until = state.cooldowns.get(candidate.symbol, 0)
        if now_ms_value < cooldown_until:
            remaining = (cooldown_until - now_ms_value) / 3_600_000
            reasons.append(
                f"{candidate.symbol} is on a {remaining:.1f}h cooldown after its last stop-out"
            )

        if candidate.score < doctrine.MIN_SETUP_SCORE:
            reasons.append(
                f"score {candidate.score:.1f} is below the {doctrine.MIN_SETUP_SCORE:.0f} threshold"
            )

        if candidate.regime not in doctrine.TRADEABLE_REGIMES:
            reasons.append(f"regime {candidate.regime} is not tradeable")

        # The stop may only ever move closer to price. A "wider stop" from any source is a
        # bigger loss, and this is the only place that could let one through.
        stop = candidate.stop
        if stop_override is not None and candidate.stop < stop_override < candidate.price:
            stop = stop_override

        if stop >= candidate.price:
            reasons.append("stop is not below entry — the setup is malformed")
            return RiskVerdict(allowed=False, reasons=reasons)

        stop_distance = (candidate.price - stop) / candidate.price
        reward_risk = (candidate.target - candidate.price) / (candidate.price - stop)
        if reward_risk < doctrine.MIN_REWARD_RISK:
            reasons.append(
                f"reward:risk {reward_risk:.2f} is under the {doctrine.MIN_REWARD_RISK} minimum"
            )

        sizing = self._size(candidate, state, stop, stop_distance, size_multiplier, instrument)
        if sizing is None:
            reasons.append("position size rounds below the exchange minimum — not tradeable here")
            return RiskVerdict(allowed=False, reasons=reasons)

        if sizing.risk_amount + state.open_risk > doctrine.MAX_PORTFOLIO_HEAT * state.equity:
            reasons.append("this trade would push portfolio heat past the ceiling")

        if sizing.quote_amount > state.cash:
            reasons.append(
                f"needs {sizing.quote_amount:.2f} but only {state.cash:.2f} is free"
            )

        return RiskVerdict(allowed=not reasons, reasons=reasons, sizing=sizing)

    def _size(
        self,
        candidate: Candidate,
        state: AccountState,
        stop: float,
        stop_distance: float,
        size_multiplier: float,
        instrument,
    ) -> Sizing | None:
        """Size is derived from risk, never chosen.

        notional = (equity x risk%) / stop distance. A tighter stop buys a larger position
        for the same money at risk; a wider one shrinks it. The trader never picks a number.
        """
        notes: list[str] = []

        multiplier = max(doctrine.MIN_SIZE_MULTIPLIER, min(doctrine.MAX_SIZE_MULTIPLIER, size_multiplier))
        if multiplier != size_multiplier:
            notes.append(f"size multiplier clamped to {multiplier:.2f}")

        risk_fraction = min(doctrine.RISK_PER_TRADE, doctrine.MAX_RISK_PER_TRADE) * multiplier
        risk_amount = state.equity * risk_fraction
        quote_amount = risk_amount / stop_distance if stop_distance > 0 else 0.0

        notional_cap = state.equity * doctrine.MAX_NOTIONAL_FRACTION
        if quote_amount > notional_cap:
            quote_amount = notional_cap
            risk_amount = quote_amount * stop_distance
            notes.append(
                f"capped at {doctrine.MAX_NOTIONAL_FRACTION*100:.0f}% of equity "
                f"({notional_cap:.2f}), risking {risk_amount:.2f}"
            )

        # Never spend money that isn't there. Leave a sliver for fees.
        spendable = max(0.0, state.cash * 0.995)
        if quote_amount > spendable:
            quote_amount = spendable
            risk_amount = quote_amount * stop_distance
            notes.append(f"limited by available cash to {quote_amount:.2f}")

        floor = max(doctrine.MIN_NOTIONAL, getattr(instrument, "min_notional", 0.0) or 0.0)
        if quote_amount < floor:
            return None

        qty = quote_amount / candidate.price if candidate.price > 0 else 0.0
        if instrument is not None:
            rounded = instrument.round_qty(qty)
            if rounded > 0:
                qty = rounded
            if getattr(instrument, "min_qty", 0.0) and qty < instrument.min_qty:
                return None

        return Sizing(
            quote_amount=round(quote_amount, 2),
            qty=qty,
            risk_amount=risk_amount,
            stop=stop,
            stop_distance=stop_distance,
            notes=notes,
        )
