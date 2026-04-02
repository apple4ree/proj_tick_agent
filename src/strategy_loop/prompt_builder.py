"""
strategy_loop/prompt_builder.py
---------------------------------
LLM 프롬프트 생성기.

두 가지 프롬프트를 제공:
  1. build_generation_messages  — 새 전략 스펙 JSON 생성 요청
  2. build_feedback_messages    — 백테스트 결과 기반 피드백 생성 요청

시스템 프롬프트 템플릿은 conf/prompts/ 에서 로드한다.
  generation_system.txt : 정적 지식 (feature semantics, 환경 제약, 안티패턴)
  generation_user.txt   : user message 구조 템플릿 ($placeholder 문법)
  feedback_system.txt   : feedback 시스템 프롬프트
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from typing import Any

from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "conf" / "prompts"

_FEATURES_LIST = ", ".join(sorted(BUILTIN_FEATURES))

_GEN_SYSTEM = (_PROMPTS_DIR / "generation_system.txt").read_text().format(
    features_list=_FEATURES_LIST
)
_GEN_USER_TMPL = Template((_PROMPTS_DIR / "generation_user.txt").read_text())
_FEEDBACK_SYSTEM = (_PROMPTS_DIR / "feedback_system.txt").read_text()


def _section(header: str, body: str) -> str:
    """Return 'header\\nbody' if body is non-empty, otherwise ''."""
    return f"{header}\n{body}" if body.strip() else ""


def build_generation_messages(
    research_goal: str,
    memory_insights: list[str] | None = None,
    failure_patterns: list[str] | None = None,
    previous_feedback: dict[str, Any] | None = None,
    session_attempts: list[dict[str, Any]] | None = None,
    best_so_far: dict[str, Any] | None = None,
) -> list[dict]:
    """Build messages for strategy spec generation."""

    # ── session history ───────────────────────────────────────────────
    if session_attempts:
        lines = [f"[Session history — {len(session_attempts)} attempt(s) so far]"]
        for a in session_attempts:
            lines.append(
                f"  - iter {a['iteration']}: {a['spec_name']}"
                f"  fill_rate={a['fill_rate']:.3f}"
                f"  net_pnl={a['net_pnl']:.1f}"
                f"  n_fills={a['n_fills']:.0f}"
                f"  verdict={a['verdict']}"
            )
        section_session_history = "\n".join(lines)
    else:
        section_session_history = ""

    # ── best spec ─────────────────────────────────────────────────────
    section_best_spec = _section(
        "Best spec so far (highest fill_rate):",
        json.dumps(best_so_far, ensure_ascii=False, indent=2) if best_so_far else "",
    )

    # ── memory insights ───────────────────────────────────────────────
    section_memory_insights = _section(
        "Past insights to incorporate:",
        "\n".join(f"  - {s}" for s in memory_insights) if memory_insights else "",
    )

    # ── failure patterns ──────────────────────────────────────────────
    section_failure_patterns = _section(
        "Known failure patterns to avoid:",
        "\n".join(f"  - {s}" for s in failure_patterns) if failure_patterns else "",
    )

    # ── previous feedback ─────────────────────────────────────────────
    section_previous_feedback = _section(
        "Previous attempt feedback (address these):",
        json.dumps(previous_feedback, ensure_ascii=False, indent=2) if previous_feedback else "",
    )

    # ── assemble via template ─────────────────────────────────────────
    raw = _GEN_USER_TMPL.substitute(
        research_goal=research_goal,
        section_session_history=section_session_history,
        section_best_spec=section_best_spec,
        section_memory_insights=section_memory_insights,
        section_failure_patterns=section_failure_patterns,
        section_previous_feedback=section_previous_feedback,
    )
    # Collapse 3+ consecutive newlines (from empty sections) down to 2
    user_content = re.sub(r"\n{3,}", "\n\n", raw).strip()

    return [
        {"role": "system", "content": _GEN_SYSTEM},
        {"role": "user", "content": user_content},
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
        insights_text = "\n".join(f"  - {s}" for s in memory_insights)
        user_parts.append(f"\nKnown patterns from prior runs:\n{insights_text}")

    user_parts.append("\nProvide your feedback JSON.")

    return [
        {"role": "system", "content": _FEEDBACK_SYSTEM},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
