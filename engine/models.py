from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal


SignalAction = Literal["BUY", "SELL", "HOLD"]
ExecutionIntent = Literal["OPEN_LONG", "CLOSE_LONG", "NO_ACTION", "AWAITING_APPROVAL"]
ExecutionMode = Literal["paper", "manual", "live"]
CoachVerdict = Literal["TRADE", "WAIT", "NO_TRADE"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_dataclass(instance: Any) -> dict[str, Any]:
    if not is_dataclass(instance):
        raise TypeError("serialize_dataclass expects a dataclass instance.")
    return {field.name: _serialize_value(getattr(instance, field.name)) for field in fields(instance)}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return serialize_dataclass(value)
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


@dataclass(slots=True)
class RiskProfile:
    entry: float
    stop_loss: float
    take_profit: float
    position_size: float
    risk_reward_ratio: float
    risk_amount: float
    estimated_loss: float
    estimated_reward: float
    notional_value: float
    partial_take_profit: float | None = None
    breakeven_trigger: float | None = None
    trailing_stop_buffer: float | None = None
    atr_reference: float | None = None
    stop_source: str = "swing_plus_atr"
    setup_type: str | None = None
    exit_style: str = "adaptive"

    def to_dict(self) -> dict[str, Any]:
        return serialize_dataclass(self)


@dataclass(slots=True)
class RiskAssessment:
    accepted: bool
    execution_intent: ExecutionIntent
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    market_volatility_pct: float = 0.0
    daily_loss_used_pct: float = 0.0
    risk_profile: RiskProfile | None = None

    def to_dict(self) -> dict[str, Any]:
        return serialize_dataclass(self)


@dataclass(slots=True)
class Signal:
    symbol: str
    interval: str
    action: SignalAction
    confidence: int
    reason: str
    timestamp: datetime = field(default_factory=utc_now)
    market: str = "spot"
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    position_size: float | None = None
    indicators: dict[str, Any] = field(default_factory=dict)
    eligible: bool = False
    execution_intent: ExecutionIntent = "NO_ACTION"
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def apply_assessment(self, assessment: RiskAssessment) -> None:
        self.eligible = assessment.accepted
        self.execution_intent = assessment.execution_intent
        self.rejection_reasons = list(assessment.rejection_reasons)
        self.warnings = list(assessment.warnings)

        if assessment.risk_profile is not None:
            self.entry = assessment.risk_profile.entry
            self.stop_loss = assessment.risk_profile.stop_loss
            self.take_profit = assessment.risk_profile.take_profit
            self.position_size = assessment.risk_profile.position_size

    def to_dict(self) -> dict[str, Any]:
        return serialize_dataclass(self)


@dataclass(slots=True)
class TradeRecord:
    symbol: str
    interval: str
    action: SignalAction
    execution_mode: ExecutionMode
    entry: float
    stop_loss: float
    take_profit: float
    position_size: float
    confidence: int
    risk_reward_ratio: float
    notional_value: float
    opened_at: datetime = field(default_factory=utc_now)
    status: str = "OPEN"
    market: str = "spot"
    exit_price: float | None = None
    closed_at: datetime | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return serialize_dataclass(self)


@dataclass(slots=True)
class JournalEntry:
    title: str
    body: str
    timestamp: datetime = field(default_factory=utc_now)
    symbol: str | None = None
    related_trade_id: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return serialize_dataclass(self)


@dataclass(slots=True)
class PortfolioSnapshot:
    execution_mode: ExecutionMode
    total_equity: float
    available_cash: float
    realized_pnl_today: float
    total_realized_pnl: float
    unrealized_pnl: float
    open_positions: int
    win_rate: float
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return serialize_dataclass(self)


@dataclass(slots=True)
class AICoachRecommendation:
    verdict: CoachVerdict
    summary: str
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    journal_prompt: str = ""
    focus_symbol: str | None = None
    focus_interval: str | None = None
    confidence: int | None = None
    model: str = ""
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return serialize_dataclass(self)
