"""Generate merged feedback with deterministic controller + LLM narrative."""
from __future__ import annotations

import logging
from typing import Any

from strategy_loop.feedback_controller import (
    compute_controller_decision,
    compute_derived_metrics,
)
from strategy_loop.openai_client import OpenAIClient
from strategy_loop.prompt_builder import build_code_feedback_messages

logger = logging.getLogger(__name__)


class FeedbackGenerator:
    def __init__(self, client: OpenAIClient) -> None:
        self._client = client

    def generate(
        self,
        code: str,
        backtest_summary: dict[str, Any],
        memory_insights: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate structured feedback for a (code, backtest) pair."""
        derived_metrics = compute_derived_metrics(backtest_summary)
        controller_decision = compute_controller_decision(derived_metrics)

        messages = build_code_feedback_messages(
            code=code,
            backtest_summary=backtest_summary,
            derived_metrics=derived_metrics,
            controller_decision=controller_decision,
            memory_insights=memory_insights,
        )
        narrative_raw = self._client.chat_json(messages)
        if not isinstance(narrative_raw, dict):
            logger.warning(
                "LLM returned non-dict feedback payload (%s). Falling back to empty narrative.",
                type(narrative_raw).__name__,
            )
            narrative_raw = {}

        def _as_text(v: Any) -> str:
            return v.strip() if isinstance(v, str) else ""

        def _as_list(v: Any) -> list[str]:
            if isinstance(v, list):
                return [str(item) for item in v if str(item).strip()]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
            return []

        # controller-owned fields are always authoritative.
        return {
            "diagnosis_code": controller_decision["diagnosis_code"],
            "severity": controller_decision["severity"],
            "control_mode": controller_decision["control_mode"],
            "structural_change_required": controller_decision["structural_change_required"],
            "verdict": controller_decision["verdict"],
            "controller_reasons": controller_decision["controller_reasons"],
            "derived_metrics": derived_metrics,
            "evidence": _as_list(narrative_raw.get("evidence")),
            "primary_issue": _as_text(narrative_raw.get("primary_issue")),
            "issues": _as_list(narrative_raw.get("issues")),
            "suggestions": _as_list(narrative_raw.get("suggestions")),
        }
