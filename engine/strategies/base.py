from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from engine.models import Signal


class BaseStrategy(ABC):
    name = "base"

    @abstractmethod
    def run_strategy(
        self,
        df: pd.DataFrame,
        *,
        symbol: str = "",
        interval: str = "",
        market: str = "spot",
        higher_timeframe_context: dict[str, Any] | None = None,
    ) -> Signal:
        """Generate a signal from an indicator-enriched market frame."""

    def build_market_context(
        self,
        df: pd.DataFrame,
        *,
        symbol: str = "",
        interval: str = "",
        market: str = "spot",
    ) -> dict[str, Any]:
        """Describe the broader market context used by the strategy."""
        raise NotImplementedError("This strategy does not expose market-context helpers.")
