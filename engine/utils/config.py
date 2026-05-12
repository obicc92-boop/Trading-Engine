from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from engine.models import ExecutionMode


SUPPORTED_INTERVALS = {"1m", "5m", "15m", "1h"}
SUPPORTED_EXECUTION_MODES: set[ExecutionMode] = {"paper", "manual", "live"}


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


@dataclass(slots=True)
class AppConfig:
    bybit_api_key: str | None
    bybit_api_secret: str | None
    bybit_use_testnet: bool
    bybit_base_url: str
    bybit_account_type: str
    bybit_recv_window: int
    bybit_allow_order_placement: bool
    bybit_require_manual_confirmation: bool
    live_confirmation_ttl_minutes: int
    live_tiny_size_scale: float
    live_max_order_notional: float
    live_max_daily_entries: int
    supabase_url: str
    supabase_key: str
    anthropic_api_key: str | None
    anthropic_model: str
    ai_coach_enabled: bool
    ai_coach_max_tokens: int
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_allowed_user_id: str | None
    telegram_polling_enabled: bool
    traded_symbols: list[str]
    market_intervals: list[str]
    higher_timeframe_interval: str
    execution_mode: ExecutionMode
    paper_trading_enabled: bool
    live_trading_enabled: bool
    paper_starting_balance: float
    paper_fee_rate: float
    paper_slippage_bps: float
    account_balance: float
    risk_per_trade: float
    daily_loss_limit: float
    swing_lookback: int
    risk_reward_ratio: float
    breakout_lookback: int
    volume_confirmation_ratio: float
    symbol_top_n: int
    symbol_min_strength_score: int
    stop_atr_multiplier: float
    trail_atr_multiplier: float
    partial_profit_rr: float
    confidence_threshold: int
    max_volatility_pct: float
    max_open_trades: int
    min_order_notional: float
    poll_interval_seconds: int
    log_level: str
    log_file_path: Path
    request_timeout_seconds: int
    market_data_limit: int


def load_config() -> AppConfig:
    load_dotenv()

    traded_symbols = _split_csv(os.getenv("TRADED_SYMBOLS")) or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    market_intervals = _split_csv(os.getenv("MARKET_INTERVALS")) or ["1m", "5m", "15m", "1h"]
    unsupported_intervals = set(market_intervals) - SUPPORTED_INTERVALS
    if unsupported_intervals:
        raise ValueError(f"Unsupported intervals configured: {sorted(unsupported_intervals)}")
    higher_timeframe_interval = (_get_env("HIGHER_TIMEFRAME_INTERVAL", "1h") or "1h").strip()
    if higher_timeframe_interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"HIGHER_TIMEFRAME_INTERVAL must be one of {sorted(SUPPORTED_INTERVALS)}.")

    execution_mode = (_get_env("EXECUTION_MODE", "paper") or "paper").lower()
    if execution_mode not in SUPPORTED_EXECUTION_MODES:
        raise ValueError(f"EXECUTION_MODE must be one of {sorted(SUPPORTED_EXECUTION_MODES)}.")

    paper_trading_enabled = _parse_bool(os.getenv("PAPER_TRADING_ENABLED"), default=execution_mode == "paper")
    live_trading_enabled = _parse_bool(os.getenv("LIVE_TRADING_ENABLED"), default=execution_mode == "live")

    bybit_use_testnet = _parse_bool(os.getenv("BYBIT_USE_TESTNET"), default=True)
    bybit_base_url = _get_env(
        "BYBIT_BASE_URL",
        "https://api-testnet.bybit.com" if bybit_use_testnet else "https://api.bybit.com",
    ) or "https://api-testnet.bybit.com"

    config = AppConfig(
        bybit_api_key=_get_env("BYBIT_API_KEY"),
        bybit_api_secret=_get_env("BYBIT_API_SECRET"),
        bybit_use_testnet=bybit_use_testnet,
        bybit_base_url=bybit_base_url,
        bybit_account_type=_get_env("BYBIT_ACCOUNT_TYPE", "UNIFIED") or "UNIFIED",
        bybit_recv_window=int(_get_env("BYBIT_RECV_WINDOW", "5000") or 5000),
        bybit_allow_order_placement=_parse_bool(os.getenv("BYBIT_ALLOW_ORDER_PLACEMENT"), default=False),
        bybit_require_manual_confirmation=_parse_bool(os.getenv("BYBIT_REQUIRE_MANUAL_CONFIRMATION"), default=True),
        live_confirmation_ttl_minutes=int(_get_env("LIVE_CONFIRMATION_TTL_MINUTES", "15") or 15),
        live_tiny_size_scale=float(_get_env("LIVE_TINY_SIZE_SCALE", "0.10") or 0.10),
        live_max_order_notional=float(_get_env("LIVE_MAX_ORDER_NOTIONAL", "25") or 25),
        live_max_daily_entries=int(_get_env("LIVE_MAX_DAILY_ENTRIES", "1") or 1),
        supabase_url=_get_env("SUPABASE_URL", required=True) or "",
        supabase_key=_get_env("SUPABASE_KEY", required=True) or "",
        anthropic_api_key=_get_env("ANTHROPIC_API_KEY"),
        anthropic_model=_get_env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514") or "claude-sonnet-4-20250514",
        ai_coach_enabled=_parse_bool(os.getenv("AI_COACH_ENABLED"), default=True),
        ai_coach_max_tokens=int(_get_env("AI_COACH_MAX_TOKENS", "700") or 700),
        telegram_bot_token=_get_env("TELEGRAM_BOT_TOKEN", required=True) or "",
        telegram_chat_id=_get_env("TELEGRAM_CHAT_ID", required=True) or "",
        telegram_allowed_user_id=_get_env("TELEGRAM_ALLOWED_USER_ID"),
        telegram_polling_enabled=_parse_bool(os.getenv("TELEGRAM_POLLING_ENABLED"), default=True),
        traded_symbols=traded_symbols,
        market_intervals=market_intervals,
        higher_timeframe_interval=higher_timeframe_interval,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        paper_trading_enabled=paper_trading_enabled,
        live_trading_enabled=live_trading_enabled,
        paper_starting_balance=float(_get_env("PAPER_STARTING_BALANCE", "10000") or 10000),
        paper_fee_rate=float(_get_env("PAPER_FEE_RATE", "0.001") or 0.001),
        paper_slippage_bps=float(_get_env("PAPER_SLIPPAGE_BPS", "5") or 5),
        account_balance=float(_get_env("ACCOUNT_BALANCE", "10000") or 10000),
        risk_per_trade=float(_get_env("RISK_PER_TRADE", "0.01") or 0.01),
        daily_loss_limit=float(_get_env("DAILY_LOSS_LIMIT", "0.03") or 0.03),
        swing_lookback=int(_get_env("SWING_LOOKBACK", "20") or 20),
        risk_reward_ratio=float(_get_env("RISK_REWARD_RATIO", "2.0") or 2.0),
        breakout_lookback=int(_get_env("BREAKOUT_LOOKBACK", "20") or 20),
        volume_confirmation_ratio=float(_get_env("VOLUME_CONFIRMATION_RATIO", "1.10") or 1.10),
        symbol_top_n=int(_get_env("SYMBOL_TOP_N", "2") or 2),
        symbol_min_strength_score=int(_get_env("SYMBOL_MIN_STRENGTH_SCORE", "60") or 60),
        stop_atr_multiplier=float(_get_env("STOP_ATR_MULTIPLIER", "1.35") or 1.35),
        trail_atr_multiplier=float(_get_env("TRAIL_ATR_MULTIPLIER", "1.75") or 1.75),
        partial_profit_rr=float(_get_env("PARTIAL_PROFIT_RR", "1.0") or 1.0),
        confidence_threshold=int(_get_env("SIGNAL_CONFIDENCE_THRESHOLD", "70") or 70),
        max_volatility_pct=float(_get_env("MAX_VOLATILITY_PCT", "0.03") or 0.03),
        max_open_trades=int(_get_env("MAX_OPEN_TRADES", "3") or 3),
        min_order_notional=float(_get_env("MIN_ORDER_NOTIONAL", "10") or 10),
        poll_interval_seconds=int(_get_env("POLL_INTERVAL_SECONDS", "60") or 60),
        log_level=(_get_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        log_file_path=Path(_get_env("LOG_FILE_PATH", "logs/trading_engine.log") or "logs/trading_engine.log"),
        request_timeout_seconds=int(_get_env("REQUEST_TIMEOUT_SECONDS", "15") or 15),
        market_data_limit=int(_get_env("MARKET_DATA_LIMIT", "500") or 500),
    )

    if not config.traded_symbols:
        raise ValueError("At least one symbol must be configured in TRADED_SYMBOLS.")
    if config.paper_starting_balance <= 0:
        raise ValueError("PAPER_STARTING_BALANCE must be greater than zero.")
    if config.paper_fee_rate < 0 or config.paper_fee_rate >= 1:
        raise ValueError("PAPER_FEE_RATE must be a decimal between 0 and 1.")
    if config.paper_slippage_bps < 0:
        raise ValueError("PAPER_SLIPPAGE_BPS must be zero or greater.")
    if config.account_balance <= 0:
        raise ValueError("ACCOUNT_BALANCE must be greater than zero.")
    if not 0 < config.risk_per_trade <= 1:
        raise ValueError("RISK_PER_TRADE must be a positive decimal between 0 and 1.")
    if not 0 < config.daily_loss_limit <= 1:
        raise ValueError("DAILY_LOSS_LIMIT must be a positive decimal between 0 and 1.")
    if config.risk_reward_ratio < 2:
        raise ValueError("RISK_REWARD_RATIO must be at least 2.0.")
    if config.breakout_lookback < 10:
        raise ValueError("BREAKOUT_LOOKBACK must be at least 10 candles.")
    if config.volume_confirmation_ratio <= 0:
        raise ValueError("VOLUME_CONFIRMATION_RATIO must be greater than zero.")
    if config.symbol_top_n <= 0:
        raise ValueError("SYMBOL_TOP_N must be greater than zero.")
    if not 0 <= config.symbol_min_strength_score <= 100:
        raise ValueError("SYMBOL_MIN_STRENGTH_SCORE must be between 0 and 100.")
    if config.stop_atr_multiplier <= 0:
        raise ValueError("STOP_ATR_MULTIPLIER must be greater than zero.")
    if config.trail_atr_multiplier <= 0:
        raise ValueError("TRAIL_ATR_MULTIPLIER must be greater than zero.")
    if config.partial_profit_rr <= 0:
        raise ValueError("PARTIAL_PROFIT_RR must be greater than zero.")
    if not 0 <= config.confidence_threshold <= 100:
        raise ValueError("SIGNAL_CONFIDENCE_THRESHOLD must be between 0 and 100.")
    if config.max_volatility_pct <= 0:
        raise ValueError("MAX_VOLATILITY_PCT must be greater than zero.")
    if config.max_open_trades <= 0:
        raise ValueError("MAX_OPEN_TRADES must be greater than zero.")
    if config.min_order_notional <= 0:
        raise ValueError("MIN_ORDER_NOTIONAL must be greater than zero.")
    if config.poll_interval_seconds <= 0:
        raise ValueError("POLL_INTERVAL_SECONDS must be greater than zero.")
    if config.request_timeout_seconds <= 0:
        raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than zero.")
    if config.market_data_limit < 200:
        raise ValueError("MARKET_DATA_LIMIT must be at least 200 so MA200 can be computed reliably.")
    if config.ai_coach_max_tokens <= 0:
        raise ValueError("AI_COACH_MAX_TOKENS must be greater than zero.")
    if config.live_confirmation_ttl_minutes <= 0:
        raise ValueError("LIVE_CONFIRMATION_TTL_MINUTES must be greater than zero.")
    if not 0 < config.live_tiny_size_scale <= 1:
        raise ValueError("LIVE_TINY_SIZE_SCALE must be a positive decimal between 0 and 1.")
    if config.live_max_order_notional <= 0:
        raise ValueError("LIVE_MAX_ORDER_NOTIONAL must be greater than zero.")
    if config.live_max_daily_entries <= 0:
        raise ValueError("LIVE_MAX_DAILY_ENTRIES must be greater than zero.")
    if not config.paper_trading_enabled and not config.live_trading_enabled and config.execution_mode != "manual":
        raise ValueError("At least one execution lane must be enabled unless EXECUTION_MODE=manual.")
    if config.bybit_allow_order_placement and (not config.bybit_api_key or not config.bybit_api_secret):
        raise ValueError("BYBIT_API_KEY and BYBIT_API_SECRET are required if BYBIT_ALLOW_ORDER_PLACEMENT is enabled.")

    return config
