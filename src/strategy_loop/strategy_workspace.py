"""
strategy_loop/strategy_workspace.py
--------------------------------------
Tick-adapted in-memory workspace for code-based strategy generation.

CoSTEER의 FBWorkspace(파일 기반 디렉토리)를 대체한다.
파일 I/O 없음 — 코드는 순수하게 Python 문자열로 메모리 내에서만 유지된다.
"""
from __future__ import annotations

import uuid


class StrategyWorkspace:
    """In-memory holder for a LLM-generated Python strategy code string."""

    def __init__(self) -> None:
        self.code: str | None = None
        self.run_id: str = uuid.uuid4().hex

    def inject_code(self, code: str) -> None:
        """Store the generated code string."""
        self.code = code

    def copy(self) -> "StrategyWorkspace":
        """Return a shallow copy with the same code (CoSTEER .copy() interface)."""
        new = StrategyWorkspace()
        new.code = self.code
        return new

    def clear(self) -> None:
        """Discard the code (strategy was rejected by evaluator)."""
        self.code = None

    def __bool__(self) -> bool:
        return self.code is not None

    def __repr__(self) -> str:
        snippet = (self.code[:60].replace("\n", "\\n") + "...") if self.code else "None"
        return f"StrategyWorkspace(run_id={self.run_id!r}, code={snippet!r})"
