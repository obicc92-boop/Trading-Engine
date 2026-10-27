"""The rulebook, as numbers.

Every constant here has a matching paragraph in DOCTRINE.md. This module is the single
source of truth: the strategy, the engine and the dashboard all read from here, so there is
exactly one place a rule can live.

Nothing in this file is configurable at runtime. Changing a rule is a code change, which
means it gets reviewed and versioned. That is the entire point.
"""

from __future__ import annotations

from typing import Final

VERSION: Final = "2.0"

# -- Section 1: what I trade -------------------------------------------------------------
# Fixed. Adding or removing a coin is a doctrine change, tested first.
QUOTE: Final = "EUR"
SYMBOLS: Final = ("BTCEUR", "ETHEUR", "SOLEUR", "XRPEUR")

# -- Section 2: the rule -----------------------------------------------------------------
# Hold a coin while its daily close is above its simple moving average of this many closed
# days. Decided once a day, on the candle that closed at 00:00 UTC.
TREND_TF: Final = "1d"
SMA_DAYS: Final = 200

# -- Section 3: how much -----------------------------------------------------------------
# When a coin turns on, buy this fraction of the account's value at the decision — or all
# the remaining cash, if there is less. One quarter, with four coins.
ENTRY_FRACTION: Final = 0.25
MIN_NOTIONAL: Final = 10.0             # quote currency; below this fees dominate

# -- Section 6: when I stop --------------------------------------------------------------
# Measured once a day, at the decision, against the highest equity any decision has seen.
# Beyond the worst drop in eight years of testing (63%). Blocks purchases; sales continue.
MAX_DRAWDOWN_HALT: Final = 0.70


def summary() -> dict[str, object]:
    """The doctrine as data, for the API and the UI.

    Grouped the way a person reads it rather than the way the code consumes it.
    """
    return {
        "version": VERSION,
        "universe": {"quote": QUOTE, "symbols": list(SYMBOLS)},
        "rule": {"timeframe": TREND_TF, "sma_days": SMA_DAYS},
        "sizing": {"entry_fraction": ENTRY_FRACTION, "min_notional": MIN_NOTIONAL},
        "halts": {"max_drawdown": MAX_DRAWDOWN_HALT},
    }
