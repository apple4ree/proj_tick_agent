"""
strategy_loop/openai_client.py
--------------------------------
Live OpenAI API client wrapper.

Log files written on every call:
  outputs/llm_logs/planner_*.json         — chat_json(context="planner")
  outputs/llm_logs/feedback_*.json        — chat_json(context="feedback")
  outputs/llm_logs/code_generation_*.json — chat_code()
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parents[2] / "outputs" / "llm_logs"


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
    """raw LLM 응답을 outputs/llm_logs/{context}_*.json 에 저장한다."""
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
    """Thin wrapper around the OpenAI chat completions API (live calls only).

    Inject a fake/stub client for tests — see tests/fakes/fake_llm_client.py.
    """

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        try:
            import openai
            self._client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        except ImportError as e:
            raise ImportError(
                "openai package required: pip install openai"
            ) from e

    def chat(self, messages: list[dict], response_format: str = "json_object") -> str:
        """Send messages to OpenAI and return the assistant content string."""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": response_format},
            temperature=0.7,
        )
        return resp.choices[0].message.content

    def chat_code(self, messages: list[dict]) -> str:
        """Code generation: text mode response with code fence stripped."""
        raw = self.chat(messages, response_format="text")
        _save_llm_log("code_generation", messages, raw)
        return _strip_code_fence(raw)

    def chat_json(self, messages: list[dict], context: str = "feedback") -> Any:
        """Send messages and parse the JSON response.

        Args:
            messages: Chat messages list.
            context: Log file prefix — "planner" or "feedback".
                     Controls the saved log filename.
        """
        raw = self.chat(messages, response_format="json_object")
        _save_llm_log(context, messages, raw)
        return json.loads(raw)
