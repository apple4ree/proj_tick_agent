"""
strategy_loop/prompt_builder.py
---------------------------------
LLM 프롬프트 생성기.

두 가지 프롬프트를 제공:
  1. build_generation_messages  — 새 전략 스펙 JSON 생성 요청
  2. build_feedback_messages    — 백테스트 결과 기반 피드백 생성 요청
"""
from __future__ import annotations

import json
from typing import Any

from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES

_FEATURES_LIST = ", ".join(sorted(BUILTIN_FEATURES))

_GEN_SYSTEM = """\
You are a quantitative strategy designer for KRX tick-data backtesting.

Available features: """ + _FEATURES_LIST + """

Rules:
- entry.condition: use shorthand form (feature + op + threshold) for market feature comparisons.
- exit.condition: use full form (left + op + right) when comparing holding_ticks via position_attr.
- entry.size must be <= risk.max_position.
- Prefer strategies robust to KRX fees and 0-500ms latency.
"""

_FEEDBACK_SYSTEM = """\
You are a quantitative strategy analyst reviewing a backtest.
Given a strategy spec and its backtest summary, identify issues and suggest improvements.
Respond with ONLY valid JSON:
{
  "issues": ["<issue1>", "<issue2>", ...],
  "suggestions": ["<suggestion1>", ...],
  "verdict": "pass" | "retry" | "fail"
}
verdict meanings:
  pass  — strategy is viable, stop iterating
  retry — strategy has potential but needs adjustment
  fail  — fundamental flaw, discard and regenerate from scratch
"""


def build_generation_messages(
    research_goal: str,
    memory_insights: list[str] | None = None,
    previous_feedback: dict[str, Any] | None = None,
) -> list[dict]:
    """Build messages for strategy spec generation."""
    user_parts = [f"Research goal: {research_goal}"]

    if memory_insights:
        insights_text = "\n".join(f"- {s}" for s in memory_insights)
        user_parts.append(f"\nPast insights to incorporate:\n{insights_text}")

    if previous_feedback:
        fb_text = json.dumps(previous_feedback, ensure_ascii=False, indent=2)
        user_parts.append(f"\nPrevious attempt feedback (address these):\n{fb_text}")

    user_parts.append("\nGenerate the strategy spec JSON.")

    return [
        {"role": "system", "content": _GEN_SYSTEM},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def build_feedback_messages(
    spec: dict[str, Any],
    backtest_summary: dict[str, Any],
    memory_insights: list[str] | None = None,
) -> list[dict]:
    """Build messages for LLM-based backtest feedback."""
    spec_text = json.dumps(spec, ensure_ascii=False, indent=2)
    summary_text = json.dumps(backtest_summary, ensure_ascii=False, indent=2)

    user_parts = [
        f"Strategy spec:\n{spec_text}",
        f"\nBacktest summary:\n{summary_text}",
    ]

    if memory_insights:
        insights_text = "\n".join(f"- {s}" for s in memory_insights)
        user_parts.append(f"\nKnown patterns from prior runs:\n{insights_text}")

    user_parts.append("\nProvide your feedback JSON.")

    return [
        {"role": "system", "content": _FEEDBACK_SYSTEM},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
