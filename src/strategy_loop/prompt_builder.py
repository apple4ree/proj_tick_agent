"""
strategy_loop/prompt_builder.py
---------------------------------
코드 전략 생성/피드백용 LLM 프롬프트 생성기.

제공 함수:
  1. build_code_generation_messages — Python 코드 전략 생성 요청
  2. build_code_feedback_messages   — 코드 전략 백테스트 피드백 요청

시스템 프롬프트 템플릿은 conf/prompts/ 에서 로드한다.
  code_generation_system.txt : 코드 생성 시스템 프롬프트
  code_generation_user.txt   : 코드 생성 user 메시지 템플릿
  feedback_system.txt        : 피드백 시스템 프롬프트
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

_CODE_GEN_SYSTEM = Template((_PROMPTS_DIR / "code_generation_system.txt").read_text()).substitute(
    features_list=_FEATURES_LIST
)
_CODE_GEN_USER_TMPL = Template((_PROMPTS_DIR / "code_generation_user.txt").read_text())
_FEEDBACK_SYSTEM = (_PROMPTS_DIR / "feedback_system.txt").read_text()


def _section(header: str, body: str) -> str:
    """Return 'header\\nbody' if body is non-empty, otherwise ''."""
    return f"{header}\n{body}" if body.strip() else ""


def build_code_generation_messages(
    research_goal: str,
    memory_insights: list[str] | None = None,
    failure_patterns: list[str] | None = None,
    previous_feedback: dict[str, Any] | None = None,
    session_attempts: list[dict[str, Any]] | None = None,
    best_code_so_far: str | None = None,
    stuck_count: int = 0,
    goal_decomposition: Any | None = None,
    rag_context: str = "",
) -> list[dict]:
    """코드 전략 생성을 위한 LLM 메시지를 빌드한다."""
    _STUCK_THRESHOLD = 3

    def _base_name(strategy_name: str) -> str:
        import re as _re
        return _re.sub(r"_v\d+$", "", strategy_name).strip()

    all_base_names = [_base_name(a["strategy_name"]) for a in session_attempts] if session_attempts else []
    repeated_base = all_base_names[-1] if all_base_names else ""
    archetype_repeat_count = sum(1 for b in all_base_names if b == repeated_base)

    _is_stuck = (stuck_count >= _STUCK_THRESHOLD or archetype_repeat_count >= _STUCK_THRESHOLD) and bool(session_attempts)

    # goal decomposition (stuck일 때 억제)
    if goal_decomposition is not None and not _is_stuck:
        section_goal_decomposition = _section(
            "Goal decomposition (follow this to select archetype and features):",
            goal_decomposition.to_prompt_section(),
        )
    else:
        section_goal_decomposition = ""

    if _is_stuck:
        last_issue = session_attempts[-1].get("primary_issue", "unknown")
        n_recent = min(max(stuck_count, archetype_repeat_count), len(session_attempts))
        recent_names = list(dict.fromkeys(
            a["strategy_name"] for a in session_attempts[-n_recent:]
        ))
        repeat_msg = (
            f"{stuck_count} consecutive non-passing iterations"
            if stuck_count >= _STUCK_THRESHOLD
            else f"'{repeated_base}' archetype repeated {archetype_repeat_count} times"
        )
        section_diversify = (
            f"⚠ EXPLORATION REQUIRED — {repeat_msg}.\n"
            f"Last issue: '{last_issue}'\n"
            f"Strategies attempted (no pass): {', '.join(recent_names)}\n"
            "The current approach is NOT working. You MUST generate a fundamentally different strategy:\n"
            "  1. Choose a DIFFERENT archetype.\n"
            "  2. Use different feature combinations.\n"
            "  3. Set HOLDING_TICKS_EXIT >= 30.\n"
            "  4. Do NOT just change threshold values — change the logic structure."
        )
        best_code_so_far = None
    else:
        section_diversify = ""

    # session history
    if session_attempts:
        lines = [f"[Session history — {len(session_attempts)} attempt(s) so far]"]
        for a in session_attempts:
            line = (
                f"  - iter {a['iteration']}: {a['strategy_name']}"
                f"  entry_freq={a.get('entry_frequency', 0):.4f}"
                f"  net_pnl={a['net_pnl']:.1f}"
                f"  n_fills={a['n_fills']:.0f}"
                f"  verdict={a['verdict']}"
            )
            if a.get("primary_issue"):
                line += f"  → {a['primary_issue']}"
            lines.append(line)
        section_session_history = "\n".join(lines)
    else:
        section_session_history = ""

    section_best_code = _section(
        "Best code strategy so far (profitable — study and improve on it):",
        best_code_so_far or "",
    )

    section_rag_context = _section("Past code attempts (learn from these):", rag_context)

    section_memory_insights = _section(
        "Past insights to incorporate:",
        "\n".join(f"  - {s}" for s in memory_insights) if memory_insights else "",
    )

    section_failure_patterns = _section(
        "Known failure patterns to avoid:",
        "\n".join(f"  - {s}" for s in failure_patterns) if failure_patterns else "",
    )

    if previous_feedback:
        primary = previous_feedback.get("primary_issue", "")
        diagnosis_code = previous_feedback.get("diagnosis_code", "")
        verdict = previous_feedback.get("verdict", "")
        control_mode = previous_feedback.get("control_mode", "neutral")
        issues = previous_feedback.get("issues", [])
        suggestions = previous_feedback.get("suggestions", [])

        if primary:
            header = f"Previous attempt feedback — fix this first: {primary}"
        else:
            header = "Previous attempt feedback (address these):"

        mode_guidance = {
            "explore": "Do NOT just tune constants. Change the primary logic family and feature combination.",
            "repair": "Keep the same general logic family and adjust thresholds, holding logic, or cost filters.",
            "neutral": "Avoid recent failure patterns, but do not force a large logic-family pivot.",
        }

        body_parts = [
            "Controller decision:\n"
            f"  - verdict={verdict}\n"
            f"  - diagnosis_code={diagnosis_code}\n"
            f"  - control_mode={control_mode}",
            "Control-mode instruction:\n"
            f"  - {mode_guidance.get(control_mode, mode_guidance['neutral'])}",
        ]
        if issues:
            body_parts.append("Issues:\n" + "\n".join(f"  - {s}" for s in issues))
        if suggestions:
            body_parts.append("Suggestions:\n" + "\n".join(f"  - {s}" for s in suggestions))
        section_previous_feedback = _section(header, "\n".join(body_parts))
    else:
        section_previous_feedback = ""

    raw = _CODE_GEN_USER_TMPL.substitute(
        research_goal=research_goal,
        section_goal_decomposition=section_goal_decomposition,
        section_diversify=section_diversify,
        section_session_history=section_session_history,
        section_best_code=section_best_code,
        section_rag_context=section_rag_context,
        section_memory_insights=section_memory_insights,
        section_failure_patterns=section_failure_patterns,
        section_previous_feedback=section_previous_feedback,
    )
    user_content = re.sub(r"\n{3,}", "\n\n", raw).strip()

    return [
        {"role": "system", "content": _CODE_GEN_SYSTEM},
        {"role": "user", "content": user_content},
    ]


# Fields from backtest_summary that are actually relevant to strategy quality assessment.
# All other fields (infrastructure config, redundant latency details, unreliable metrics)
# are filtered out to reduce LLM noise.
_FEEDBACK_SUMMARY_KEYS: frozenset[str] = frozenset({
    # Trade activity
    "signal_count", "n_states", "n_fills", "parent_order_count", "child_order_count",
    # PnL fields used by Python controller to derive gross/cost metrics.
    "net_pnl", "total_realized_pnl", "total_unrealized_pnl",
    "total_commission", "total_slippage", "total_impact",
    # Execution quality (reliable)
    "fill_rate", "avg_holding_period", "avg_slippage_bps", "avg_latency_ms",
    # NOTE: alpha_contribution and execution_contribution are NOT included —
    # they are always 0/unreliable due to arrival_prices implementation artifact.
})


def build_code_feedback_messages(
    code: str,
    backtest_summary: dict[str, Any],
    derived_metrics: dict[str, Any],
    controller_decision: dict[str, Any],
    memory_insights: list[str] | None = None,
) -> list[dict]:
    """코드 전략 백테스트 결과에 대한 피드백 생성 메시지."""
    filtered_summary = {k: v for k, v in backtest_summary.items() if k in _FEEDBACK_SUMMARY_KEYS}
    summary_text = json.dumps(filtered_summary, ensure_ascii=False, indent=2)
    derived_metrics_text = json.dumps(derived_metrics, ensure_ascii=False, indent=2)
    controller_decision_text = json.dumps(controller_decision, ensure_ascii=False, indent=2)

    user_parts = [
        f"Strategy code:\n```python\n{code}\n```",
        f"\nBacktest summary:\n{summary_text}",
        f"\nDerived metrics (authoritative):\n{derived_metrics_text}",
        f"\nController decision (authoritative):\n{controller_decision_text}",
        "\nThe derived metrics and controller decision are precomputed and authoritative. "
        "Do not recompute them.",
    ]

    if memory_insights:
        insights_text = "\n".join(f"  - {s}" for s in memory_insights)
        user_parts.append(f"\nKnown patterns from prior runs:\n{insights_text}")

    user_parts.append("\nProvide your feedback JSON.")

    return [
        {"role": "system", "content": _FEEDBACK_SYSTEM},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
