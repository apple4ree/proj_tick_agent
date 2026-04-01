"""
strategy_loop/openai_client.py
--------------------------------
얇은 OpenAI 클라이언트 래퍼.

mode="mock" 이면 실제 API 호출 없이 미리 정의된 응답을 반환한다 (테스트용).
"""
from __future__ import annotations

import json
import os
from typing import Any

_MOCK_SPEC = {
    "name": "mock_order_imbalance_strategy",
    "entry": {
        "side": "long",
        "condition": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.1},
        "size": 10,
    },
    "exit": {
        "condition": {
            "type": "any",
            "conditions": [
                {
                    "type": "comparison",
                    "left": {"type": "position_attr", "name": "holding_ticks"},
                    "op": ">=",
                    "right": {"type": "const", "value": 5},
                },
                {"type": "comparison", "feature": "order_imbalance", "op": "<", "threshold": -0.05},
            ],
        }
    },
    "risk": {"max_position": 100},
}

_MOCK_FEEDBACK = {
    "issues": [],
    "suggestions": ["Try a higher order_imbalance threshold for fewer but higher-quality signals."],
    "verdict": "retry",
}


class OpenAIClient:
    """Thin wrapper around openai chat completions."""

    def __init__(self, model: str = "gpt-4o-mini", mode: str = "live") -> None:
        self.model = model
        self.mode = mode
        if mode == "live":
            try:
                import openai
                self._client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            except ImportError as e:
                raise ImportError("openai package required for live mode: pip install openai") from e

    def chat(self, messages: list[dict], response_format: str = "json_object") -> str:
        """Send messages and return the assistant content string."""
        if self.mode == "mock":
            # Decide mock response based on system prompt content
            system = next((m["content"] for m in messages if m.get("role") == "system"), "")
            if "verdict" in system and "issues" in system:
                return json.dumps(_MOCK_FEEDBACK)
            return json.dumps(_MOCK_SPEC)

        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": response_format},
            temperature=0.7,
        )
        return resp.choices[0].message.content

    def chat_json(self, messages: list[dict]) -> Any:
        """Send messages and parse the response as JSON."""
        raw = self.chat(messages, response_format="json_object")
        return json.loads(raw)
