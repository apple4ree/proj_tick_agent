"""OpenAI client wrapper for strategy generation.

Supports three modes:
- live:   real API calls to OpenAI
- replay: return previously saved responses
- mock:   return deterministic test fixtures

Environment variables:
- OPENAI_API_KEY:  API key (required for live mode)
- OPENAI_MODEL:    model name (default: gpt-4o)
- OPENAI_BASE_URL: custom base URL (optional)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "gpt-4o"
DEFAULT_TEMPERATURE = 0.2
MAX_RETRIES = 2
RETRY_DELAY_S = 1.0


def _get_env_model() -> str:
    return os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)


def _get_env_base_url() -> str | None:
    return os.environ.get("OPENAI_BASE_URL")


def is_openai_available() -> bool:
    """Check if OPENAI_API_KEY is set."""
    return bool(os.environ.get("OPENAI_API_KEY"))


class OpenAIStrategyGenClient:
    """Thin wrapper around OpenAI API for strategy generation.

    Parameters
    ----------
    mode : str
        "live", "replay", or "mock".
    model : str
        Model name for live calls.
    temperature : float
        Sampling temperature.
    replay_path : Path | None
        Path to replay log for replay mode.
    """

    def __init__(
        self,
        *,
        mode: str = "live",
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        replay_path: Path | str | None = None,
    ) -> None:
        self.mode = mode
        self.model = model or _get_env_model()
        self.temperature = temperature
        self._replay_log: list[dict[str, Any]] = []
        self._replay_path = Path(replay_path) if replay_path else None
        self._replay_cursor = 0
        self._client: Any = None

        if mode == "live":
            self._init_live_client()
        elif mode == "replay":
            self._load_replay()

    def _init_live_client(self) -> None:
        if not is_openai_available():
            logger.warning("OPENAI_API_KEY not set — live mode unavailable")
            return
        try:
            from openai import OpenAI
            kwargs: dict[str, Any] = {"api_key": os.environ["OPENAI_API_KEY"]}
            base_url = _get_env_base_url()
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)
        except ImportError:
            logger.warning("openai package not installed — live mode unavailable")

    def _load_replay(self) -> None:
        if self._replay_path and self._replay_path.exists():
            data = json.loads(self._replay_path.read_text(encoding="utf-8"))
            self._replay_log = data if isinstance(data, list) else []
            self._replay_cursor = 0
            logger.info("Loaded %d replay entries from %s", len(self._replay_log), self._replay_path)

    @property
    def is_available(self) -> bool:
        """Whether this client can make real API calls."""
        return self._client is not None

    def query_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
    ) -> T | None:
        """Query the LLM and parse response into a Pydantic model.

        Returns None on failure after retries.
        """
        if self.mode == "mock":
            return None  # agents handle mock fallback themselves

        if self.mode == "replay":
            return self._replay_next(schema)

        # live mode
        if not self.is_available:
            return None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=schema,
                    temperature=self.temperature,
                )
                parsed = response.choices[0].message.parsed
                if parsed is not None:
                    self._record_replay(system_prompt, user_prompt, schema.__name__, parsed)
                    return parsed
                logger.warning("LLM returned null parsed response (attempt %d)", attempt + 1)
            except Exception as e:
                logger.warning("OpenAI call failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES + 1, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_S)
        return None

    def _replay_next(self, schema: type[T]) -> T | None:
        if self._replay_cursor >= len(self._replay_log):
            logger.warning("Replay log exhausted at cursor %d", self._replay_cursor)
            return None
        entry = self._replay_log[self._replay_cursor]
        self._replay_cursor += 1
        try:
            return schema.model_validate(entry.get("response", {}))
        except Exception as e:
            logger.warning("Replay parse failed: %s", e)
            return None

    def _record_replay(self, system: str, user: str, schema_name: str, result: BaseModel) -> None:
        self._replay_log.append({
            "schema": schema_name,
            "system_prompt": system[:200],
            "user_prompt": user[:500],
            "response": result.model_dump(),
        })

    def save_replay_log(self, path: Path | str) -> None:
        """Save replay log to disk for later replay mode."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._replay_log, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("Saved %d replay entries to %s", len(self._replay_log), path)

    def reset(self) -> None:
        """Reset replay log and cursor."""
        self._replay_log.clear()
        self._replay_cursor = 0
