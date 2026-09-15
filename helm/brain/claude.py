"""The decision layer.

Candidates arrive here having already passed deterministic detection and the risk rails.
This is where the judgement happens — and where it is recorded, so that a losing month can
be read back rather than argued about.

Two invariants hold no matter what comes back from the API:

1. A failure is a NO-GO. Unreachable API, malformed JSON, a timeout, a refusal — all of them
   mean "don't trade". Silence is never consent.
2. The response can only ever reduce risk. `size_multiplier` is clamped to at most 1.0 and a
   stop can only be tightened. Those clamps are applied here *and* again in `risk.py`,
   because a safety property enforced in one place is a safety property one edit from gone.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import doctrine
from ..models import Candidate, Verdict
from . import prompts

log = logging.getLogger("helm.brain")

REFUSAL_THESIS = "No verdict could be obtained, so the answer is no."


class Brain:
    def __init__(self, config) -> None:
        self.config = config
        self.model = config.model
        self.effort = config.model_effort
        self._client = None
        self._calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_reads = 0
        # A setup that hasn't changed doesn't need re-judging every cycle. This is the main
        # cost control: without it a persistent candidate would be re-asked every 60s.
        self._asked: dict[str, tuple[float, float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.anthropic_key)

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=self.config.anthropic_key, timeout=90.0, max_retries=2
            )
        return self._client

    def stats(self) -> dict[str, Any]:
        """Token spend, surfaced in the UI. Running an opinion isn't free either."""
        cost = (self._input_tokens / 1e6) * 5.0 + (self._output_tokens / 1e6) * 25.0
        return {
            "calls": self._calls,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "cache_reads": self._cache_reads,
            "estimated_cost_usd": round(cost, 4),
        }

    def should_ask(self, candidate: Candidate, min_interval: float = 900.0) -> bool:
        """Skip re-asking about a candidate that is materially unchanged."""
        previous = self._asked.get(candidate.symbol)
        if previous is None:
            return True
        asked_at, asked_score = previous
        if time.time() - asked_at > min_interval:
            return True
        # A meaningfully better score is a different question.
        return candidate.score > asked_score + 5.0

    def judge_entry(self, candidate: Candidate, state, recent_trades, scan) -> Verdict:
        if not self.enabled:
            return self._fallback(
                "No ANTHROPIC_API_KEY is configured, so there is no one to approve this trade."
            )

        self._asked[candidate.symbol] = (time.time(), candidate.score)
        brief = prompts.entry_brief(candidate, state, self.config.quote, recent_trades, scan)

        try:
            data, raw = self._ask(brief, prompts.ENTRY_SCHEMA)
        except Exception as exc:
            log.warning("entry verdict failed for %s: %s", candidate.symbol, exc)
            return self._fallback(f"The decision call failed ({type(exc).__name__}), so: no trade.")

        go = bool(data.get("go"))
        conviction = int(max(0, min(100, data.get("conviction", 0))))

        multiplier = float(data.get("size_multiplier", doctrine.MAX_SIZE_MULTIPLIER))
        multiplier = max(doctrine.MIN_SIZE_MULTIPLIER, min(doctrine.MAX_SIZE_MULTIPLIER, multiplier))

        # Only a tighter stop survives. Anything at or below the proposed stop — i.e. wider —
        # is dropped here rather than quietly widening the risk.
        stop_override = data.get("tighten_stop_to")
        if stop_override is not None:
            try:
                value = float(stop_override)
                stop_override = value if candidate.stop < value < candidate.price else None
            except (TypeError, ValueError):
                stop_override = None

        thesis = str(data.get("thesis", "")).strip()
        invalidation = str(data.get("invalidation", "")).strip()
        if key_risk := str(data.get("key_risk", "")).strip():
            invalidation = f"{invalidation} Key risk: {key_risk}" if invalidation else key_risk

        return Verdict(
            go=go,
            conviction=conviction,
            size_multiplier=multiplier,
            stop_override=stop_override,
            thesis=thesis,
            invalidation=invalidation,
            source="claude",
            raw=raw,
        )

    def review_position(self, position, analysis, price: float, reasons: list[str]) -> dict[str, Any]:
        """A second opinion on an open position. Can only bring an exit forward.

        The mechanical exits in `positions.py` run first and are unconditional; this is
        asked afterwards and only its EXIT answer is ever acted on.
        """
        if not self.enabled:
            return {"action": "HOLD", "reason": "no decision layer configured", "confidence": 0}
        brief = prompts.exit_brief(position, analysis, self.config.quote, price, reasons)
        try:
            data, _ = self._ask(brief, prompts.EXIT_SCHEMA, effort="medium")
        except Exception as exc:
            log.warning("exit review failed for %s: %s", position.symbol, exc)
            # Failing to get an opinion is not a reason to close a managed position.
            return {"action": "HOLD", "reason": f"review unavailable ({type(exc).__name__})", "confidence": 0}
        return {
            "action": "EXIT" if data.get("action") == "EXIT" else "HOLD",
            "reason": str(data.get("reason", "")).strip(),
            "confidence": int(max(0, min(100, data.get("confidence", 0)))),
        }

    def _ask(self, brief: str, schema: dict, effort: str | None = None) -> tuple[dict, dict]:
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            # The system prompt is byte-identical across calls, so it caches; the brief that
            # follows it is what changes.
            system=[{"type": "text", "text": prompts.SYSTEM, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort or self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": brief}],
        )

        usage = response.usage
        self._calls += 1
        self._input_tokens += getattr(usage, "input_tokens", 0) or 0
        self._output_tokens += getattr(usage, "output_tokens", 0) or 0
        self._cache_reads += getattr(usage, "cache_read_input_tokens", 0) or 0

        if response.stop_reason == "refusal":
            raise RuntimeError("model declined to answer")

        text = next((block.text for block in response.content if block.type == "text"), "")
        if not text:
            raise ValueError("empty response")
        data = json.loads(text)
        return data, {
            "model": response.model,
            "stop_reason": response.stop_reason,
            "raw": data,
        }

    @staticmethod
    def _fallback(reason: str) -> Verdict:
        return Verdict(
            go=False,
            conviction=0,
            size_multiplier=doctrine.MIN_SIZE_MULTIPLIER,
            stop_override=None,
            thesis=REFUSAL_THESIS,
            invalidation=reason,
            source="fallback",
        )
