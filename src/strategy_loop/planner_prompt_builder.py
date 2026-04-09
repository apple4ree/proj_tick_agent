"""
strategy_loop/planner_prompt_builder.py
-----------------------------------------
Builds planner LLM messages.

The planner returns a single JSON object:
  {
    "strategy_text": "<markdown>",
    "strategy_spec": { <StrategySpec v2.2> }
  }

Call via client.chat_json(build_planner_messages(...), context="planner").
"""
from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "conf" / "prompts"
_FEATURES_LIST = ", ".join(sorted(BUILTIN_FEATURES))

# Substitute $features_list at module load time — same pattern as implementer
_PLANNER_SYSTEM = Template(
    (_PROMPTS_DIR / "planner_system.txt").read_text(encoding="utf-8")
).substitute(features_list=_FEATURES_LIST)


def build_planner_messages(
    research_goal: str,
    goal_decomposition: Any | None = None,
    planner_memory: list[dict[str, Any]] | None = None,
    previous_plan_feedback: str | None = None,
) -> list[dict]:
    """Build planner LLM messages that request strategy_text + strategy_spec JSON.

    Args:
        research_goal: Free-form research goal string.
        goal_decomposition: Optional GoalDecomposition (from goal_decomposer.decompose).
        planner_memory: Last N plan-level records from MemoryStore for context.
        previous_plan_feedback: Plain-text feedback on why the previous plan failed.

    Returns:
        List of {"role": ..., "content": ...} dicts ready for client.chat_json(..., context="planner").
    """
    parts: list[str] = [f"Research goal: {research_goal}"]

    if goal_decomposition is not None:
        section = goal_decomposition.to_prompt_section()
        if section.strip():
            parts.append(f"Goal decomposition:\n{section}")

    if planner_memory:
        lines = [f"Past plan history ({len(planner_memory)} plans — learn from these):"]
        for rec in planner_memory[-3:]:       # show at most the 3 most recent
            plan_id = rec.get("plan_id", "?")
            arch_name = rec.get("archetype_name", "?")
            outcome = rec.get("outcome", "?")
            issue = rec.get("primary_issue", "")
            line = (
                f"  - plan={plan_id}"
                f"  archetype={arch_name}"
                f"  outcome={outcome}"
            )
            if issue:
                line += f"  → {issue}"
            lines.append(line)
        parts.append("\n".join(lines))

    if previous_plan_feedback:
        parts.append(
            f"Previous plan was rejected — fix this:\n{previous_plan_feedback}"
        )

    parts.append(
        "Now produce the strategy_text and strategy_spec. "
        "Return ONLY the JSON object (no markdown fences)."
    )

    user_content = "\n\n".join(p for p in parts if p.strip())
    return [
        {"role": "system", "content": _PLANNER_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def parse_planner_response(response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract (strategy_text, strategy_spec_dict) from planner JSON response.

    Raises:
        KeyError: if required keys are missing.
        TypeError: if strategy_spec is not a dict.
    """
    strategy_text = str(response.get("strategy_text", ""))
    spec_raw = response["strategy_spec"]
    if not isinstance(spec_raw, dict):
        raise TypeError(
            f"strategy_spec must be a dict, got {type(spec_raw).__name__}"
        )
    return strategy_text, spec_raw
