"""
strategy_loop/feedback_generator.py
--------------------------------------
백테스트 결과를 LLM에게 전달해 구조화된 피드백을 생성한다.

반환값 예시:
{
    "issues": ["Fill rate is near zero — entry threshold may be too strict."],
    "suggestions": ["Lower order_imbalance threshold from 0.15 to 0.08."],
    "verdict": "retry"   # "pass" | "retry" | "fail"
}
"""
from __future__ import annotations

import logging
from typing import Any

from strategy_loop.openai_client import OpenAIClient
from strategy_loop.prompt_builder import build_feedback_messages

logger = logging.getLogger(__name__)


class FeedbackGenerator:
    def __init__(self, client: OpenAIClient) -> None:
        self._client = client

    def generate(
        self,
        spec: dict[str, Any],
        backtest_summary: dict[str, Any],
        memory_insights: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate structured feedback for a (spec, backtest) pair."""
        messages = build_feedback_messages(spec, backtest_summary, memory_insights)
        feedback = self._client.chat_json(messages)

        # Normalise: ensure required fields exist
        feedback.setdefault("issues", [])
        feedback.setdefault("suggestions", [])
        if feedback.get("verdict") not in ("pass", "retry", "fail"):
            logger.warning("LLM returned unexpected verdict %r, defaulting to 'retry'", feedback.get("verdict"))
            feedback["verdict"] = "retry"

        return feedback
