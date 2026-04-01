"""
strategy_loop/hard_gate.py
---------------------------
Hard Gate: 스펙 JSON의 구조·의미적 유효성을 검증한다.
LLM이 생성한 스펙이 백테스트에 넘어가기 전에 반드시 통과해야 하는 관문.

반환값: HardGateResult(passed, errors)
errors 가 비어 있으면 passed=True.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
from strategy_loop.spec_simple import VALID_OPS, VALID_SIDES, VALID_POSITION_ATTRS


@dataclass
class HardGateResult:
    passed: bool
    errors: list[str] = field(default_factory=list)


def validate(spec: dict[str, Any]) -> HardGateResult:
    """Validate a simple strategy spec. Returns HardGateResult."""
    errors: list[str] = []

    # ── top-level fields ──────────────────────────────────────────────
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        errors.append("spec.name must be a non-empty string")

    # ── entry ─────────────────────────────────────────────────────────
    entry = spec.get("entry")
    if not isinstance(entry, dict):
        errors.append("spec.entry must be a dict")
    else:
        if entry.get("side") not in VALID_SIDES:
            errors.append(f"spec.entry.side must be one of {sorted(VALID_SIDES)}")
        size = entry.get("size")
        if not isinstance(size, (int, float)) or size <= 0:
            errors.append("spec.entry.size must be a positive number")
        cond = entry.get("condition")
        if cond is None:
            errors.append("spec.entry.condition is required")
        else:
            errors.extend(_validate_bool_expr(cond, path="entry.condition"))

    # ── exit ──────────────────────────────────────────────────────────
    exit_ = spec.get("exit")
    if not isinstance(exit_, dict):
        errors.append("spec.exit must be a dict")
    else:
        cond = exit_.get("condition")
        if cond is None:
            errors.append("spec.exit.condition is required")
        else:
            errors.extend(_validate_bool_expr(cond, path="exit.condition"))

    # ── risk ──────────────────────────────────────────────────────────
    risk = spec.get("risk")
    if not isinstance(risk, dict):
        errors.append("spec.risk must be a dict")
    else:
        mp = risk.get("max_position")
        if not isinstance(mp, (int, float)) or mp <= 0:
            errors.append("spec.risk.max_position must be a positive number")

    return HardGateResult(passed=len(errors) == 0, errors=errors)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _validate_bool_expr(cond: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(cond, dict):
        return [f"{path}: expected a dict, got {type(cond).__name__}"]

    t = cond.get("type")
    if t == "comparison":
        errors.extend(_validate_comparison(cond, path))
    elif t == "all":
        children = cond.get("conditions")
        if not isinstance(children, list) or len(children) == 0:
            errors.append(f"{path}.conditions must be a non-empty list")
        else:
            for i, c in enumerate(children):
                errors.extend(_validate_bool_expr(c, f"{path}.conditions[{i}]"))
    elif t == "any":
        children = cond.get("conditions")
        if not isinstance(children, list) or len(children) == 0:
            errors.append(f"{path}.conditions must be a non-empty list")
        else:
            for i, c in enumerate(children):
                errors.extend(_validate_bool_expr(c, f"{path}.conditions[{i}]"))
    elif t == "not":
        inner = cond.get("condition")
        if inner is None:
            errors.append(f"{path}.condition is required for 'not' node")
        else:
            errors.extend(_validate_bool_expr(inner, f"{path}.condition"))
    else:
        errors.append(f"{path}.type must be one of comparison/all/any/not, got {t!r}")

    return errors


def _validate_comparison(cond: dict, path: str) -> list[str]:
    errors: list[str] = []
    op = cond.get("op")
    if op not in VALID_OPS:
        errors.append(f"{path}.op must be one of {sorted(VALID_OPS)}, got {op!r}")

    if "feature" in cond:
        # shorthand: feature vs threshold
        feat = cond["feature"]
        if feat not in BUILTIN_FEATURES:
            errors.append(
                f"{path}.feature {feat!r} is not a known builtin feature. "
                f"Valid features: {sorted(BUILTIN_FEATURES)}"
            )
        thr = cond.get("threshold")
        if not isinstance(thr, (int, float)):
            errors.append(f"{path}.threshold must be a number")
    else:
        # full form: left vs right
        for side in ("left", "right"):
            v = cond.get(side)
            if v is None:
                errors.append(f"{path}.{side} is required in full comparison form")
            else:
                errors.extend(_validate_value_expr(v, f"{path}.{side}"))

    return errors


def _validate_value_expr(v: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(v, dict):
        return [f"{path}: expected a dict, got {type(v).__name__}"]
    t = v.get("type")
    if t == "feature":
        name = v.get("name")
        if name not in BUILTIN_FEATURES:
            errors.append(
                f"{path}.name {name!r} is not a known builtin feature. "
                f"Valid features: {sorted(BUILTIN_FEATURES)}"
            )
    elif t == "const":
        if not isinstance(v.get("value"), (int, float)):
            errors.append(f"{path}.value must be a number")
    elif t == "position_attr":
        name = v.get("name")
        if name not in VALID_POSITION_ATTRS:
            errors.append(
                f"{path}.name {name!r} is not a valid position_attr. "
                f"Valid attrs: {sorted(VALID_POSITION_ATTRS)}"
            )
    else:
        errors.append(f"{path}.type must be feature/const/position_attr, got {t!r}")
    return errors
