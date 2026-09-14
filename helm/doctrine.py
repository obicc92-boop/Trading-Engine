"""The rulebook, as numbers.

Every constant here has a matching paragraph in DOCTRINE.md. This module is the single
source of truth: the risk engine, the position manager and the prompt sent to the decision
model all read from here, so there is exactly one place a rule can live.

Nothing in this file is configurable at runtime. Changing a rule is a code change, which
means it gets reviewed and versioned. That is the entire point.
"""

from __future__ import annotations

from typing import Final

VERSION: Final = "1.0"

# -- Section 2: size and risk ------------------------------------------------------------
# Risk is what a stop-out costs. Notional is what sits in the trade. They are very
# different numbers and conflating them is how small accounts die.
RISK_PER_TRADE: Final = 0.015          # 1.5% of equity is the working level
MAX_RISK_PER_TRADE: Final = 0.020      # 2.0% is the ceiling, never crossed
MAX_PORTFOLIO_HEAT: Final = 0.045      # sum of open risk across all positions
MAX_OPEN_POSITIONS: Final = 3
MAX_ENTRIES_PER_DAY: Final = 3
MAX_NOTIONAL_FRACTION: Final = 0.40    # single position vs total equity
MIN_NOTIONAL: Final = 10.0             # quote currency; below this fees dominate

# -- Section 3: circuit breakers ---------------------------------------------------------
DAILY_LOSS_HALT: Final = 0.04          # halts new entries until 00:00 UTC
WEEKLY_LOSS_HALT: Final = 0.08         # halts new entries until the week rolls
MAX_DRAWDOWN_HALT: Final = 0.20        # full stop, needs a human to restart
CONSECUTIVE_LOSS_LIMIT: Final = 3
CONSECUTIVE_LOSS_COOLDOWN_HOURS: Final = 6
SYMBOL_COOLDOWN_HOURS: Final = 6       # after a stop-out on that symbol

# -- Section 1: universe -----------------------------------------------------------------
MIN_QUOTE_VOLUME_24H: Final = 50_000_000.0
MIN_LISTING_AGE_DAYS: Final = 90
MAX_UNIVERSE_SIZE: Final = 12

# -- Section 4: entry gates --------------------------------------------------------------
MIN_SETUP_SCORE: Final = 62.0
TRADEABLE_REGIMES: Final = ("TRENDING_UP", "RANGING")

# Setup scoring weights. These sum to 100 and each maps to a named, inspectable factor,
# so a score can always be explained rather than just asserted.
SCORE_WEIGHTS: Final = {
    "trend_alignment": 25.0,
    "momentum_quality": 20.0,
    "volume_confirmation": 15.0,
    "volatility_fit": 15.0,
    "structure_clarity": 15.0,
    "relative_strength": 10.0,
}

# -- Section 5: the decision layer -------------------------------------------------------
# The model can shrink a position but never grow one. The ceiling of 1.0 is the whole
# safety property: conviction can only ever cost me less than the rules already allow.
MIN_SIZE_MULTIPLIER: Final = 0.25
MAX_SIZE_MULTIPLIER: Final = 1.0

# -- Section 6: exits --------------------------------------------------------------------
STOP_ATR_MULTIPLIER: Final = 2.2
STRUCTURE_STOP_ATR_BUFFER: Final = 0.3
MIN_STOP_DISTANCE: Final = 0.025       # 2.5% — tighter than this is noise, not a stop
MAX_STOP_DISTANCE: Final = 0.060       # 6.0% — wider than this and sizing breaks down
MIN_REWARD_RISK: Final = 1.8

SCALE_OUT_R: Final = 1.0               # take half off at +1R
SCALE_OUT_FRACTION: Final = 0.5
BREAKEVEN_FEE_BUFFER: Final = 0.0025   # breakeven means breakeven *after* fees

TRAIL_ATR_MULTIPLIER: Final = 3.0      # chandelier distance from highest close
REGIME_BREAK_CLOSES: Final = 2         # consecutive 1h closes below EMA21

TIME_STOP_HOURS: Final = 24
TIME_STOP_MIN_R: Final = 0.5           # must be past +0.5R by then to earn its slot
CATASTROPHE_STOP: Final = 0.08         # -8% from entry, unconditional market-out

MIN_HOLD_MINUTES: Final = 15           # anti-whipsaw; does not apply to the guard

# -- Timeframes --------------------------------------------------------------------------
REGIME_TF: Final = "4h"
STRUCTURE_TF: Final = "1h"
TRIGGER_TF: Final = "15m"


def summary() -> dict[str, object]:
    """The doctrine as data, for the API and the UI.

    Grouped the way a person reads it rather than the way the code consumes it.
    """
    return {
        "version": VERSION,
        "sizing": {
            "risk_per_trade": RISK_PER_TRADE,
            "max_risk_per_trade": MAX_RISK_PER_TRADE,
            "max_portfolio_heat": MAX_PORTFOLIO_HEAT,
            "max_open_positions": MAX_OPEN_POSITIONS,
            "max_entries_per_day": MAX_ENTRIES_PER_DAY,
            "max_notional_fraction": MAX_NOTIONAL_FRACTION,
            "min_notional": MIN_NOTIONAL,
        },
        "halts": {
            "daily_loss": DAILY_LOSS_HALT,
            "weekly_loss": WEEKLY_LOSS_HALT,
            "max_drawdown": MAX_DRAWDOWN_HALT,
            "consecutive_losses": CONSECUTIVE_LOSS_LIMIT,
            "cooldown_hours": CONSECUTIVE_LOSS_COOLDOWN_HOURS,
        },
        "entries": {
            "min_score": MIN_SETUP_SCORE,
            "regimes": list(TRADEABLE_REGIMES),
            "weights": dict(SCORE_WEIGHTS),
            "min_reward_risk": MIN_REWARD_RISK,
        },
        "exits": {
            "stop_atr_multiplier": STOP_ATR_MULTIPLIER,
            "stop_band": [MIN_STOP_DISTANCE, MAX_STOP_DISTANCE],
            "scale_out_r": SCALE_OUT_R,
            "scale_out_fraction": SCALE_OUT_FRACTION,
            "trail_atr_multiplier": TRAIL_ATR_MULTIPLIER,
            "time_stop_hours": TIME_STOP_HOURS,
            "catastrophe_stop": CATASTROPHE_STOP,
        },
        "timeframes": {
            "regime": REGIME_TF,
            "structure": STRUCTURE_TF,
            "trigger": TRIGGER_TF,
        },
    }
