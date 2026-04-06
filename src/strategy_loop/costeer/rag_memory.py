"""
strategy_loop/costeer/rag_memory.py
-------------------------------------
V1 RAG 메모리 — 임베딩 없이 최근 K개 실패/성공을 LLM 프롬프트에 직접 주입.

AlphaAgent CoSTEERRAGStrategyV1을 tick 환경에 맞게 단순화했다:
  - 벡터 DB / 임베딩 없음
  - 최근 K개 실패 + 최근 K개 성공을 슬라이딩 윈도우로 유지
  - format_for_prompt() 로 프롬프트 문자열 생성

세션 스코프 (LoopRunner.run() 안에서만 유지됨).
영속성이 필요하면 MemoryStore와 연계해 파일로 직렬화할 수 있다.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strategy_loop.costeer.knowledge import CodeKnowledge

_DEFAULT_MAX_FAILURES: int = 5
_DEFAULT_MAX_SUCCESSES: int = 3


class RagMemoryV1:
    """슬라이딩 윈도우 V1 RAG 메모리."""

    def __init__(self, max_failures: int = _DEFAULT_MAX_FAILURES, max_successes: int = _DEFAULT_MAX_SUCCESSES) -> None:
        self._max_failures = max(1, int(max_failures))
        self._max_successes = max(1, int(max_successes))
        self._failures: deque[CodeKnowledge] = deque(maxlen=self._max_failures)
        self._successes: deque[CodeKnowledge] = deque(maxlen=self._max_successes)

    def add(self, knowledge: "CodeKnowledge") -> None:
        """knowledge를 verdict에 따라 실패/성공 큐에 추가한다."""
        if knowledge.verdict == "pass":
            self._successes.append(knowledge)
        else:
            self._failures.append(knowledge)

    def format_for_prompt(self) -> str:
        """LLM 프롬프트에 삽입할 텍스트 블록을 생성한다.

        성공 사례 → 따라야 할 패턴.
        실패 사례 → 반복하지 말아야 할 패턴.
        """
        parts: list[str] = []

        if self._successes:
            parts.append(
                "=== SUCCESSFUL CODE STRATEGIES (study these — follow their patterns) ==="
            )
            for k in self._successes:
                parts.append(k.get_implementation_and_feedback_str())
                parts.append("")

        if self._failures:
            parts.append(
                "=== RECENT CODE FAILURES (do NOT repeat these patterns) ==="
            )
            for k in self._failures:
                parts.append(k.get_implementation_and_feedback_str())
                parts.append("")

        return "\n".join(parts).strip()

    def is_empty(self) -> bool:
        return not self._failures and not self._successes

    def __len__(self) -> int:
        return len(self._failures) + len(self._successes)
