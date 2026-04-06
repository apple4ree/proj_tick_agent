"""
strategy_loop/openai_client.py
--------------------------------
얇은 OpenAI 클라이언트 래퍼.

mode="mock" 이면 실제 API 호출 없이 미리 정의된 응답을 반환한다 (테스트용).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parents[2] / "outputs" / "llm_logs"

_MOCK_FEEDBACK = {
    "evidence": [
        "Mock evidence: no real backtest analysis was performed.",
    ],
    "primary_issue": "Mock feedback — no real analysis performed.",
    "issues": [],
    "suggestions": [
        "Try a higher order_imbalance threshold for fewer but higher-quality signals.",
    ],
}

_MOCK_CODE = """\
ORDER_IMBALANCE_THRESHOLD = 0.30
OI_EMA_THRESHOLD = 0.20
SPREAD_MAX_BPS = 50.0
HOLDING_TICKS_EXIT = 20
REVERSAL_THRESHOLD = -0.05

def generate_signal(features, position):
    holding = position["holding_ticks"]
    in_pos = position["in_position"]

    if in_pos:
        if holding >= HOLDING_TICKS_EXIT:
            return -1
        if features.get("order_imbalance", 0.0) < REVERSAL_THRESHOLD:
            return -1
        return None

    oi = features.get("order_imbalance", 0.0)
    oi_ema = features.get("order_imbalance_ema", 0.0)
    spread = features.get("spread_bps", 999.0)

    if (oi > ORDER_IMBALANCE_THRESHOLD
            and oi_ema > OI_EMA_THRESHOLD
            and spread < SPREAD_MAX_BPS):
        return 1

    return None
"""


def _strip_code_fence(s: str) -> str:
    """LLM이 ```python ... ``` 형태로 감싼 응답에서 코드 펜스를 제거한다."""
    text = s.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _save_llm_log(context: str, messages: list[dict], raw: str) -> None:
    """raw LLM 응답을 outputs/llm_logs/ 에 저장한다."""
    import datetime
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = _LOG_DIR / f"{context}_{ts}.json"
        path.write_text(
            json.dumps({"context": context, "messages": messages, "raw_response": raw},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("LLM log write failed: %s", exc)


class OpenAIClient:
    """Thin wrapper around OpenAI chat completions."""

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
            if response_format == "text":
                return _MOCK_CODE
            return json.dumps(_MOCK_FEEDBACK)

        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": response_format},
            temperature=0.7,
        )
        return resp.choices[0].message.content

    def chat_code(self, messages: list[dict]) -> str:
        """코드 생성 전용: 텍스트 모드로 응답을 받고 코드 펜스를 제거한다."""
        raw = self.chat(messages, response_format="text")
        _save_llm_log("code_generation", messages, raw)
        return _strip_code_fence(raw)

    def chat_json(self, messages: list[dict]) -> Any:
        """Send messages and parse the response as JSON."""
        raw = self.chat(messages, response_format="json_object")
        _save_llm_log("feedback", messages, raw)
        return json.loads(raw)
