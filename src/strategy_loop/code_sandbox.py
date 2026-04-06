"""
strategy_loop/code_sandbox.py
--------------------------------
AST-based safety check + restricted exec sandbox for LLM-generated strategy code.

두 단계 안전장치:
  1. AST 검증 — 금지된 import/이름 사용을 AST 순회로 감지
  2. 제한된 exec — __builtins__를 최소 화이트리스트로 교체

사용 예:
    from strategy_loop.code_sandbox import exec_strategy_code, CodeSandboxError

    try:
        ns = exec_strategy_code(code)
        result = ns["generate_signal"](features, position)
    except CodeSandboxError as e:
        print("Unsafe code:", e)
"""
from __future__ import annotations

import ast

# ── 허용된 import 모듈 화이트리스트 ────────────────────────────────────────────
# 이 외의 모든 import는 거부된다.
_ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "math",
    "statistics",
    "functools",
    "itertools",
    "collections",
    "operator",
})

# ── 금지된 내장 이름 ─────────────────────────────────────────────────────────
_BANNED_NAMES: frozenset[str] = frozenset({
    "__import__", "exec", "eval", "compile",
    "open", "input", "__loader__",
    "globals", "locals", "vars", "dir",
    "setattr", "delattr",
    "breakpoint", "__builtins__",
    "__spec__", "__file__", "__name__",
})

# ── 안전한 __builtins__ 화이트리스트 ─────────────────────────────────────────
_SAFE_BUILTINS: dict = {
    # 수치 연산
    "abs": abs, "min": min, "max": max, "sum": sum,
    "round": round, "pow": pow, "divmod": divmod,
    # 타입 변환
    "int": int, "float": float, "bool": bool, "str": str, "complex": complex,
    # 컨테이너
    "list": list, "dict": dict, "tuple": tuple,
    "set": set, "frozenset": frozenset,
    # 이터레이션
    "range": range, "enumerate": enumerate, "zip": zip,
    "map": map, "filter": filter,
    "sorted": sorted, "reversed": reversed,
    "len": len, "iter": iter, "next": next,
    "any": any, "all": all,
    # 타입 검사
    "isinstance": isinstance, "issubclass": issubclass,
    "callable": callable, "hasattr": hasattr, "type": type,
    # 기타
    "print": print,    # 디버그 출력 허용
    "repr": repr, "hash": hash, "id": id,
    # 상수
    "True": True, "False": False, "None": None,
    # 예외
    "Exception": Exception, "ValueError": ValueError,
    "TypeError": TypeError, "KeyError": KeyError,
    "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
    "RuntimeError": RuntimeError, "StopIteration": StopIteration,
    "AttributeError": AttributeError,
}


class CodeSandboxError(Exception):
    """코드가 AST 검증에 실패하거나 실행 중 오류가 발생했을 때."""


def validate_ast(code: str) -> list[str]:
    """AST를 순회해 안전성 위반 항목을 반환한다.

    반환값이 빈 리스트면 검증 통과.
    """
    errors: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError: {exc}"]

    for node in ast.walk(tree):
        # ── import 검사 ────────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in _ALLOWED_IMPORTS:
                    errors.append(
                        f"Disallowed import: {alias.name!r}. "
                        f"Only these modules are allowed: {sorted(_ALLOWED_IMPORTS)}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top not in _ALLOWED_IMPORTS:
                    errors.append(
                        f"Disallowed from-import: {node.module!r}. "
                        f"Only these modules are allowed: {sorted(_ALLOWED_IMPORTS)}"
                    )

        # ── 금지된 이름 검사 ───────────────────────────────────────
        elif isinstance(node, ast.Name):
            if node.id in _BANNED_NAMES:
                errors.append(f"Banned name used: {node.id!r}")

        # ── 3인자 type() 동적 클래스 생성 차단 ────────────────────
        elif isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "type"
                and len(node.args) == 3
            ):
                errors.append("Dynamic 3-argument type() call is not allowed")

    return errors


def exec_strategy_code(code: str) -> dict:
    """제한된 샌드박스에서 코드를 실행하고 모듈 네임스페이스를 반환한다.

    Args:
        code: LLM이 생성한 Python 전략 코드

    Returns:
        모듈 수준 네임스페이스 dict (generate_signal 등 포함)

    Raises:
        CodeSandboxError: AST 검증 실패 또는 실행 오류
    """
    errors = validate_ast(code)
    if errors:
        raise CodeSandboxError(
            "AST validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # globals와 locals를 동일한 dict로 사용해야 한다.
    #
    # exec(code, globals, locals)를 분리하면:
    #   - 모듈 수준 상수 (ORDER_IMBALANCE_THRESHOLD = 0.3) → locals에 저장
    #   - 함수 객체의 __globals__ → globals를 가리킴
    #   - 결과: generate_signal() 호출 시 상수를 globals에서 찾지 못해 NameError
    #
    # globals/locals를 동일한 dict로 사용하면:
    #   - 상수도 namespace에 저장
    #   - generate_signal.__globals__ = namespace → 상수 참조 성공
    namespace: dict = {"__builtins__": _SAFE_BUILTINS}

    try:
        exec(compile(code, "<strategy>", "exec"), namespace, namespace)  # noqa: S102
    except CodeSandboxError:
        raise
    except Exception as exc:
        raise CodeSandboxError(f"Runtime error during code execution: {exc}") from exc

    namespace.pop("__builtins__", None)  # 반환 전 정리
    return namespace
