"""Position management — the exit ladder.

Entries get all the attention; exits determine the outcome. So this module is deliberately
the most mechanical part of the system: `plan` is a pure function of (position, price,
analysis, time) with no I/O, which means the rules that decide when money comes off the
table can be tested exhaustively rather than hoped about.

The ladder is ordered by urgency, and the first rule that fires wins. Protective exits run
before opportunistic ones, and the catastrophe guard runs before everything — including
before the minimum-hold check, because a position down 8% is not being whipsawed, it is
being wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from . import doctrine
from .models import Analysis, Candles, Position, now_ms

log = logging.getLogger("helm.positions")


@dataclass(slots=True)
class ExitPlan:
    kind: str                 # CATASTROPHE | STOP | TARGET | SCALE_OUT | TIME_STOP | REGIME_BREAK
    fraction: float           # portion of the current quantity to sell
    reason: str
    urgent: bool = False      # bypasses the minimum-hold rule


@dataclass(slots=True)
class StopUpdate:
    stop: float
    reason: str


@dataclass(slots=True)
class ManagePlan:
    exit: ExitPlan | None = None
    stop_update: StopUpdate | None = None
    notes: list[str] = field(default_factory=list)


def update_highest_close(position: Position, candles: Candles | None, price: float) -> None:
    """Track the high-water close, which anchors the trailing stop.

    Closes rather than highs: a single wick should not ratchet a stop up to a level price
    never actually held.
    """
    highest = position.highest_close or position.entry_price
    if candles is not None and len(candles) > 0:
        since_entry = candles.close[candles.open_time >= position.opened_at]
        if since_entry.size:
            highest = max(highest, float(np.max(since_entry)))
    position.highest_close = max(highest, price)


def count_regime_break(position: Position, candles: Candles | None, ema21: float) -> None:
    """Count consecutive structure *closes* below EMA21.

    Once per bar, not once per cycle. This runs every cycle — every 60 seconds in
    production — while the structure timeframe is hourly, so counting on each call would
    reach "two consecutive 1h closes" in two minutes and knife every position before it
    had a chance to work. The bar's open-time is the guard: the same bar is never counted
    twice, however often this is called.

    Resets on any close back above, so it takes a genuine run of weakness to trip the exit.
    """
    if candles is None or len(candles) < 2 or not ema21:
        return

    bar_time = float(candles.open_time[-1])
    if bar_time <= position.regime_bar_time:
        return                                  # this bar has already been judged
    position.regime_bar_time = bar_time

    if float(candles.close[-1]) < ema21:
        position.regime_break_closes += 1
    else:
        position.regime_break_closes = 0


def plan(position: Position, price: float, analysis: Analysis | None, ts: int | None = None) -> ManagePlan:
    """Decide what, if anything, to do with this position right now. Pure function."""
    ts = ts if ts is not None else now_ms()
    result = ManagePlan()
    if position.qty <= 0 or price <= 0:
        return result

    entry = position.entry_price
    r_multiple = position.r_multiple(price)
    age_hours = max(0.0, (ts - position.opened_at) / 3_600_000)
    drawdown_from_entry = (price / entry - 1) if entry else 0.0
    atr = (analysis.indicators.get("atr", 0.0) if analysis else 0.0) or 0.0

    # 1. Catastrophe guard — unconditional, ignores every other consideration.
    if drawdown_from_entry <= -doctrine.CATASTROPHE_STOP:
        result.exit = ExitPlan(
            kind="CATASTROPHE",
            fraction=1.0,
            reason=f"down {drawdown_from_entry*100:.1f}% from entry — past the "
                   f"{doctrine.CATASTROPHE_STOP*100:.0f}% hard floor, out now",
            urgent=True,
        )
        return result

    # 2. The stop, named for whatever it has become.
    if price <= position.stop:
        if position.breakeven_set and position.stop >= entry:
            kind, reason = "TRAIL", f"trailing stop hit at {position.stop:.6f} — runner banked"
        elif position.scaled_out:
            kind, reason = "TRAIL", f"stop hit at {position.stop:.6f} after scaling out"
        else:
            kind, reason = "STOP", f"stop hit at {position.stop:.6f} — thesis was wrong, {r_multiple:.2f}R"
        result.exit = ExitPlan(kind=kind, fraction=1.0, reason=reason, urgent=True)
        return result

    # 3. Target on the remainder.
    if position.target > 0 and price >= position.target:
        result.exit = ExitPlan(
            kind="TARGET",
            fraction=1.0,
            reason=f"target {position.target:.6f} reached at {r_multiple:+.2f}R",
            urgent=True,
        )
        return result

    minimum_hold_met = age_hours * 60 >= doctrine.MIN_HOLD_MINUTES

    # 4. Scale out at +1R and make the trade free.
    if not position.scaled_out and r_multiple >= doctrine.SCALE_OUT_R:
        result.exit = ExitPlan(
            kind="SCALE_OUT",
            fraction=doctrine.SCALE_OUT_FRACTION,
            reason=f"+{r_multiple:.2f}R — taking half off and moving the stop to breakeven",
            urgent=True,
        )
        result.stop_update = StopUpdate(
            stop=entry * (1 + doctrine.BREAKEVEN_FEE_BUFFER),
            reason="breakeven plus fees — this trade can no longer lose money",
        )
        return result

    # 5. Trail the runner. Only ratchets up, never down.
    if position.scaled_out and atr > 0:
        chandelier = position.highest_close - doctrine.TRAIL_ATR_MULTIPLIER * atr
        floor = entry * (1 + doctrine.BREAKEVEN_FEE_BUFFER)
        candidate_stop = max(chandelier, floor)
        if candidate_stop > position.stop and candidate_stop < price:
            result.stop_update = StopUpdate(
                stop=candidate_stop,
                reason=f"trailing stop raised to {candidate_stop:.6f} "
                       f"({doctrine.TRAIL_ATR_MULTIPLIER:.1f}x ATR below the high)",
            )

    # 6a. Regime break on the runner — the thesis was the trend, and the trend is gone.
    #
    # Runners only, deliberately. A TREND_PULLBACK entry buys *into* the EMA21 band, so
    # before the trade has proved anything this trigger sits millimetres from the entry and
    # ordinary noise closes the position at a small loss every time. Until +1R the initial
    # stop is the protection — that is its entire job. After the scale-out the trade is free
    # and cutting a runner on a lost trend is the right call.
    if (
        position.scaled_out
        and minimum_hold_met
        and position.regime_break_closes >= doctrine.REGIME_BREAK_CLOSES
    ):
        result.exit = ExitPlan(
            kind="REGIME_BREAK",
            fraction=1.0,
            reason=f"{position.regime_break_closes} consecutive closes below the 1h EMA21 — "
                   f"the trend that justified this is over",
        )
        return result

    # 6b. A 4h flip into a downtrend applies to every position, runner or not. It is a far
    # rarer and more decisive signal than a couple of hourly closes, and being long into a
    # confirmed higher-timeframe downtrend is the one thing the doctrine refuses outright.
    if minimum_hold_met and analysis is not None and analysis.regime == "TRENDING_DOWN":
        result.exit = ExitPlan(
            kind="REGIME_BREAK",
            fraction=1.0,
            reason="4h regime flipped to TRENDING_DOWN — no longer a market to be long in",
        )
        return result

    # 7. Time stop — dead money has an opportunity cost, and there are only three slots.
    if age_hours >= doctrine.TIME_STOP_HOURS and r_multiple < doctrine.TIME_STOP_MIN_R:
        result.exit = ExitPlan(
            kind="TIME_STOP",
            fraction=1.0,
            reason=f"{age_hours:.0f}h in and still only {r_multiple:+.2f}R — "
                   f"freeing the slot for a better idea",
        )
        return result

    if result.stop_update is None:
        result.notes.append(
            f"holding at {r_multiple:+.2f}R, {age_hours:.1f}h in, stop {position.stop:.6f}"
        )
    return result


def realise(position: Position, qty: float, price: float, fee: float) -> float:
    """Book a partial or full exit against the position and return the realised P&L.

    Fees on both legs are attributed here so that `realized_pnl` is what actually landed in
    the account, not a gross number that flatters the record.
    """
    share = (qty / position.initial_qty) if position.initial_qty else 0.0
    entry_fee_share = position.entry_fee * share
    gross = (price - position.entry_price) * qty
    net = gross - fee - entry_fee_share
    position.realized_pnl += net
    position.fees_paid += fee
    position.qty = max(0.0, position.qty - qty)
    return net


def write_off(position: Position) -> float:
    """Charge whatever a final exit could not sell to the trade, and close its books.

    Exchanges sell in whole lot steps, so a final exit floors its quantity and leaves a
    sliver of coin behind. `realise` charges only for what actually left, so without this
    the booked result would sit above the cash in the account by that sliver's cost — trade
    after trade, until a drawdown measured against it described money that was never lost.
    The sliver itself stays in the account, and the next final exit in the same coin sells
    it (`book_swept`).
    """
    if position.qty <= 0:
        return 0.0
    share = (position.qty / position.initial_qty) if position.initial_qty else 0.0
    cost = position.entry_price * position.qty + position.entry_fee * share
    position.realized_pnl -= cost
    position.qty = 0.0
    return -cost


def book_swept(position: Position, qty: float, price: float, fee: float) -> float:
    """Book dust left by earlier trades, sold alongside this trade's final exit.

    Its cost was charged to the trade that left it behind (`write_off`), so everything it
    fetches is result.
    """
    net = qty * price - fee
    position.realized_pnl += net
    position.fees_paid += fee
    return net
