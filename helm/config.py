"""Runtime configuration.

Anything here is an *operational* setting — which venue, which keys, how often to wake up.
Trading rules live in `doctrine.py` and are deliberately not reachable from the environment,
so that no amount of env twiddling can loosen a risk limit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional: handy locally, unnecessary in a container with real env vars
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - the app must boot without python-dotenv
    pass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default) or ""
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


@dataclass(slots=True)
class Config:
    # -- execution posture ---------------------------------------------------------------
    # `dry` runs the identical code path against a simulated broker. `live` sends real
    # orders. Going live takes two independent switches on purpose: one of them is a
    # sentence you have to type, so it cannot be flipped by accident or by a stray default.
    mode: str = field(default_factory=lambda: os.getenv("HELM_MODE", "dry").strip().lower())
    live_ack: bool = field(default_factory=lambda: _bool("I_UNDERSTAND_REAL_MONEY"))

    venue: str = field(default_factory=lambda: os.getenv("HELM_VENUE", "binance").strip().lower())
    quote: str = field(default_factory=lambda: os.getenv("HELM_QUOTE", "EUR").strip().upper())

    # -- credentials ---------------------------------------------------------------------
    binance_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", "").strip())
    binance_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", "").strip())
    binance_base: str = field(
        default_factory=lambda: os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")
    )
    alpaca_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", "").strip())
    alpaca_secret: str = field(default_factory=lambda: os.getenv("ALPACA_API_SECRET", "").strip())
    alpaca_base: str = field(
        default_factory=lambda: os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets").rstrip("/")
    )
    alpaca_data_base: str = field(
        default_factory=lambda: os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
    )

    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    model: str = field(default_factory=lambda: os.getenv("HELM_MODEL", "claude-opus-5").strip())
    model_effort: str = field(default_factory=lambda: os.getenv("HELM_MODEL_EFFORT", "high").strip())

    # -- universe ------------------------------------------------------------------------
    # Empty means "rank the venue's own liquid symbols". Setting it pins the universe,
    # which is what you want while you're still watching it closely.
    symbols: list[str] = field(default_factory=lambda: _csv("HELM_SYMBOLS"))

    # -- loop ----------------------------------------------------------------------------
    cycle_seconds: int = field(default_factory=lambda: _int("HELM_CYCLE_SECONDS", 60))
    scan_every_cycles: int = field(default_factory=lambda: _int("HELM_SCAN_EVERY", 5))
    starting_equity: float = field(default_factory=lambda: _float("HELM_STARTING_EQUITY", 100.0))
    taker_fee: float = field(default_factory=lambda: _float("HELM_TAKER_FEE", 0.001))

    # -- plumbing ------------------------------------------------------------------------
    db_path: str = field(default_factory=lambda: os.getenv("HELM_DB", "data/helm.db"))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    ui_token: str = field(default_factory=lambda: os.getenv("HELM_UI_TOKEN", "").strip())
    request_timeout: float = field(default_factory=lambda: _float("HELM_HTTP_TIMEOUT", 15.0))

    @property
    def live(self) -> bool:
        """Live only when both switches agree.

        `mode=live` without the acknowledgement runs dry and says so loudly at boot —
        failing closed is the only acceptable default when the failure mode is money.
        """
        return self.mode == "live" and self.live_ack

    @property
    def brain_enabled(self) -> bool:
        return bool(self.anthropic_key)

    def kill_file(self) -> Path:
        """A kill switch that works when everything else doesn't.

        `touch` this path and the engine flattens and stops on its next cycle — no API, no
        UI, no network required.
        """
        return Path(self.db_path).parent / "KILL"

    def describe(self) -> dict[str, object]:
        """Config for the UI, with every secret reduced to a boolean."""
        return {
            "mode": "live" if self.live else "dry",
            "requested_mode": self.mode,
            "venue": self.venue,
            "quote": self.quote,
            "cycle_seconds": self.cycle_seconds,
            "brain": self.brain_enabled,
            "model": self.model if self.brain_enabled else None,
            "keys_present": {
                "binance": bool(self.binance_key and self.binance_secret),
                "alpaca": bool(self.alpaca_key and self.alpaca_secret),
                "anthropic": bool(self.anthropic_key),
            },
            "pinned_symbols": self.symbols,
        }


def load() -> Config:
    return Config()
