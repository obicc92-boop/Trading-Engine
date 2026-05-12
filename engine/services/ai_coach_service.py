from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import requests

from engine.models import AICoachRecommendation


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


@dataclass(slots=True)
class AICoachService:
    api_key: str | None
    model: str
    enabled: bool
    max_tokens: int
    timeout_seconds: int
    logger: logging.Logger
    session: requests.Session = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.session = requests.Session()

    def is_available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def unavailable_reason(self) -> str:
        if not self.enabled:
            return "AI coach is disabled in config."
        if not self.api_key:
            return "AI coach is unavailable. Add ANTHROPIC_API_KEY to .env and restart the engine."
        return "AI coach is unavailable."

    def generate_recommendation(
        self,
        *,
        signals: list[dict[str, Any]],
        open_trades: list[dict[str, Any]],
        portfolio_snapshot: dict[str, Any],
        recent_journal_entries: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> AICoachRecommendation:
        if not self.is_available():
            raise ValueError(self.unavailable_reason())

        context = {
            "settings": settings,
            "portfolio_snapshot": portfolio_snapshot,
            "recent_signals": [self._summarize_signal(signal) for signal in signals[:6]],
            "open_trades": [self._summarize_trade(trade) for trade in open_trades[:5]],
            "recent_journal_entries": [self._summarize_journal_entry(entry) for entry in recent_journal_entries[:3]],
        }

        system_prompt = (
            "You are a conservative AI trading coach inside a private paper-trading application. "
            "You are not an autonomous trader and you must never instruct the user to bypass risk controls. "
            "This system is spot-only, long-only, and educational. SELL signals in this app mean exit or de-risk, not shorting. "
            "Use only the supplied facts. If the context is mixed, uncertain, or risk is elevated, prefer WAIT or NO_TRADE. "
            "Do not recommend leverage, revenge trading, averaging down blindly, removing stops, or trading outside the stated rules. "
            "Return JSON only with this exact schema: "
            "{\"verdict\":\"TRADE|WAIT|NO_TRADE\",\"focus_symbol\":string|null,\"focus_interval\":string|null,"
            "\"confidence\":integer|null,\"summary\":string,\"reasons\":string[],\"risks\":string[],"
            "\"checklist\":string[],\"journal_prompt\":string}."
        )
        user_prompt = (
            "Review the following trading context and provide a coaching response for what the user should do next. "
            "If there is no valid long setup, prefer WAIT or NO_TRADE.\n\n"
            f"{json.dumps(context, ensure_ascii=True, indent=2)}"
        )

        response = self.session.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": self.api_key or "",
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": 0.2,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        response_json = response.json()
        recommendation = self._parse_recommendation(self._extract_text(response_json))
        recommendation.model = str(response_json.get("model") or self.model)
        return recommendation

    @staticmethod
    def format_for_telegram(recommendation: AICoachRecommendation) -> str:
        lines = [
            "AI Coach",
            f"Verdict: {recommendation.verdict}",
        ]
        if recommendation.focus_symbol:
            focus = recommendation.focus_symbol
            if recommendation.focus_interval:
                focus = f"{focus} {recommendation.focus_interval}"
            lines.append(f"Focus: {focus}")
        if recommendation.confidence is not None:
            lines.append(f"Coach Confidence: {recommendation.confidence}%")
        lines.extend(["", recommendation.summary])

        if recommendation.reasons:
            lines.extend(["", "Why:"])
            lines.extend(f"- {reason}" for reason in recommendation.reasons)
        if recommendation.risks:
            lines.extend(["", "Risks:"])
            lines.extend(f"- {risk}" for risk in recommendation.risks)
        if recommendation.checklist:
            lines.extend(["", "Checklist:"])
            lines.extend(f"- {item}" for item in recommendation.checklist)
        if recommendation.journal_prompt:
            lines.extend(["", f"Journal Prompt: {recommendation.journal_prompt}"])

        return "\n".join(lines)

    @staticmethod
    def format_for_journal(recommendation: AICoachRecommendation) -> str:
        sections = [recommendation.summary]
        if recommendation.reasons:
            sections.append("Why:\n" + "\n".join(f"- {reason}" for reason in recommendation.reasons))
        if recommendation.risks:
            sections.append("Risks:\n" + "\n".join(f"- {risk}" for risk in recommendation.risks))
        if recommendation.checklist:
            sections.append("Checklist:\n" + "\n".join(f"- {item}" for item in recommendation.checklist))
        if recommendation.journal_prompt:
            sections.append(f"Journal Prompt:\n- {recommendation.journal_prompt}")
        return "\n\n".join(sections)

    @staticmethod
    def _extract_text(response_json: dict[str, Any]) -> str:
        content = response_json.get("content", [])
        text_parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in text_parts if part).strip()

    def _parse_recommendation(self, raw_text: str) -> AICoachRecommendation:
        cleaned_text = raw_text.strip()
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text.strip("`")
            cleaned_text = cleaned_text.replace("json", "", 1).strip()

        try:
            payload = json.loads(cleaned_text)
        except json.JSONDecodeError:
            start = cleaned_text.find("{")
            end = cleaned_text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                self.logger.error("AI coach returned non-JSON content: %s", raw_text)
                raise ValueError("AI coach returned an unreadable response.") from None
            payload = json.loads(cleaned_text[start : end + 1])

        verdict = str(payload.get("verdict", "NO_TRADE")).upper().replace(" ", "_")
        if verdict not in {"TRADE", "WAIT", "NO_TRADE"}:
            verdict = "NO_TRADE"

        confidence = payload.get("confidence")
        try:
            parsed_confidence = int(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            parsed_confidence = None
        if parsed_confidence is not None and not 0 <= parsed_confidence <= 100:
            parsed_confidence = None

        return AICoachRecommendation(
            verdict=verdict,  # type: ignore[arg-type]
            focus_symbol=self._coerce_optional_text(payload.get("focus_symbol")),
            focus_interval=self._coerce_optional_text(payload.get("focus_interval")),
            confidence=parsed_confidence,
            summary=self._coerce_text(payload.get("summary"), fallback="No coaching summary was returned."),
            reasons=self._coerce_text_list(payload.get("reasons")),
            risks=self._coerce_text_list(payload.get("risks")),
            checklist=self._coerce_text_list(payload.get("checklist")),
            journal_prompt=self._coerce_text(payload.get("journal_prompt"), fallback="What did you learn from this setup?"),
        )

    @staticmethod
    def _coerce_optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_text(value: Any, *, fallback: str) -> str:
        text = str(value).strip() if value is not None else ""
        return text or fallback

    @staticmethod
    def _coerce_text_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _summarize_signal(signal: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": signal.get("symbol"),
            "interval": signal.get("interval"),
            "action": signal.get("action"),
            "confidence": signal.get("confidence"),
            "eligible": signal.get("eligible"),
            "execution_intent": signal.get("execution_intent"),
            "entry": signal.get("entry"),
            "stop_loss": signal.get("stop_loss"),
            "take_profit": signal.get("take_profit"),
            "reason": signal.get("reason"),
            "rejection_reasons": signal.get("rejection_reasons"),
            "warnings": signal.get("warnings"),
            "timestamp": signal.get("timestamp"),
        }

    @staticmethod
    def _summarize_trade(trade: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": trade.get("symbol"),
            "interval": trade.get("interval"),
            "status": trade.get("status"),
            "entry": trade.get("entry"),
            "stop_loss": trade.get("stop_loss"),
            "take_profit": trade.get("take_profit"),
            "position_size": trade.get("position_size"),
            "opened_at": trade.get("opened_at"),
            "notes": trade.get("notes"),
        }

    @staticmethod
    def _summarize_journal_entry(entry: dict[str, Any]) -> dict[str, Any]:
        body = str(entry.get("body", "") or "")
        return {
            "title": entry.get("title"),
            "body": body[:280],
            "symbol": entry.get("symbol"),
            "tags": entry.get("tags"),
            "timestamp": entry.get("timestamp"),
        }
