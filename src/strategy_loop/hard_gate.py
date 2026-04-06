"""
strategy_loop/hard_gate.py
---------------------------
Hard Gate: LLM이 생성한 Python 전략 코드의 구조·안전성을 검증한다.

검증 함수:
  validate_code(code) — Python 코드 검증 (AST 안전성 포함)

반환값: HardGateResult(passed, errors)
errors 가 비어 있으면 passed=True.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HardGateResult:
    passed: bool
    errors: list[str] = field(default_factory=list)


def validate_code(code: str) -> HardGateResult:
    """LLM이 생성한 Python 전략 코드를 검증한다.

    검사 항목:
      1. 코드가 비어 있지 않은지
      2. AST 안전성 (금지된 import/이름 없는지)
      3. 실행 가능한지 (sandbox exec 성공)
      4. generate_signal(features, position) 함수가 정의되어 있는지

    Returns:
        HardGateResult — errors가 비어 있으면 passed=True
    """
    from strategy_loop.code_sandbox import CodeSandboxError, exec_strategy_code, validate_ast

    if not code or not code.strip():
        return HardGateResult(passed=False, errors=["code is empty"])

    # AST 검증 (실행 전에 먼저 — 더 빠르고 명확한 오류 메시지)
    ast_errors = validate_ast(code)
    if ast_errors:
        return HardGateResult(passed=False, errors=ast_errors)

    # Sandbox exec — 실행 오류 감지
    try:
        ns = exec_strategy_code(code)
    except CodeSandboxError as exc:
        return HardGateResult(passed=False, errors=[str(exc)])

    # generate_signal 함수 존재 여부
    if "generate_signal" not in ns:
        return HardGateResult(
            passed=False,
            errors=["Code must define 'generate_signal(features, position)' function"],
        )

    # generate_signal이 callable인지
    if not callable(ns["generate_signal"]):
        return HardGateResult(
            passed=False,
            errors=["'generate_signal' must be a callable function, not a variable"],
        )

    return HardGateResult(passed=True, errors=[])
