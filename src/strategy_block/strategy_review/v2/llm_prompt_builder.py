"""Prompt builder for LLM review + constrained repair (v2)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from strategy_block.strategy_review.review_common import ReviewResult
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import build_backtest_constraint_summary

from .backtest_feedback import build_backtest_feedback_summary
from .contracts import BacktestFeedbackSummary, LLMReviewReport


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _to_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _build_spec_summary(spec: StrategySpecV2) -> dict[str, Any]:
    execution = spec.execution_policy
    risk = spec.risk_policy
    return {
        "name": spec.name,
        "version": spec.version,
        "spec_format": spec.spec_format,
        "n_preconditions": len(spec.preconditions),
        "n_entry_policies": len(spec.entry_policies),
        "n_exit_policies": len(spec.exit_policies),
        "n_regimes": len(spec.regimes),
        "has_state_policy": spec.state_policy is not None,
        "has_execution_policy": execution is not None,
        "execution_policy": execution.to_dict() if execution else None,
        "risk_policy": risk.to_dict(),
        "metadata": dict(spec.metadata or {}),
        "spec": spec.to_dict(),
    }


def _build_backtest_context_payload(backtest_environment: dict[str, Any] | None) -> dict[str, str]:
    return {
        "backtest_environment_summary": build_backtest_constraint_summary(backtest_environment),
        "backtest_environment_json": _to_json(backtest_environment or {}),
    }


def _build_feedback_payload(backtest_feedback: BacktestFeedbackSummary | None) -> dict[str, str]:
    summary = build_backtest_feedback_summary(backtest_feedback)
    if backtest_feedback is None:
        feedback_json: dict[str, Any] = {}
    else:
        feedback_json = backtest_feedback.model_dump()
    return {
        "backtest_feedback_summary": summary,
        "backtest_feedback_json": _to_json(feedback_json),
    }


def build_llm_review_prompt(
    *,
    spec: StrategySpecV2,
    static_review: ReviewResult,
    backtest_environment: dict[str, Any] | None = None,
    backtest_feedback: BacktestFeedbackSummary | None = None,
) -> tuple[str, str]:
    system_prompt = _load_prompt("reviewer_system.md")
    user_template = _load_prompt("reviewer_user.md")
    context_payload = _build_backtest_context_payload(backtest_environment)
    feedback_payload = _build_feedback_payload(backtest_feedback)
    user_prompt = user_template.format(
        spec_summary=_to_json(_build_spec_summary(spec)),
        static_review_json=_to_json(static_review.to_dict()),
        backtest_environment_summary=context_payload["backtest_environment_summary"],
        backtest_environment_json=context_payload["backtest_environment_json"],
        backtest_feedback_summary=feedback_payload["backtest_feedback_summary"],
        backtest_feedback_json=feedback_payload["backtest_feedback_json"],
    )
    return system_prompt, user_prompt


def build_repair_prompt(
    *,
    spec: StrategySpecV2,
    static_review: ReviewResult,
    llm_review: LLMReviewReport,
    backtest_environment: dict[str, Any] | None = None,
    backtest_feedback: BacktestFeedbackSummary | None = None,
) -> tuple[str, str]:
    system_prompt = _load_prompt("repair_system.md")
    user_template = _load_prompt("repair_user.md")
    context_payload = _build_backtest_context_payload(backtest_environment)
    feedback_payload = _build_feedback_payload(backtest_feedback)
    user_prompt = user_template.format(
        spec_summary=_to_json(_build_spec_summary(spec)),
        static_review_json=_to_json(static_review.to_dict()),
        llm_review_json=_to_json(llm_review.model_dump()),
        backtest_environment_summary=context_payload["backtest_environment_summary"],
        backtest_environment_json=context_payload["backtest_environment_json"],
        backtest_feedback_summary=feedback_payload["backtest_feedback_summary"],
        backtest_feedback_json=feedback_payload["backtest_feedback_json"],
    )
    return system_prompt, user_prompt
