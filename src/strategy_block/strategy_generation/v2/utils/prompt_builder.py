"""Builds final prompts from templates with placeholder substitution."""
from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def build_system_prompt() -> str:
    """Return the planner system prompt."""
    return _load_prompt("planner_system.md")


def build_user_prompt(
    *,
    research_goal: str,
    strategy_style: str = "auto",
    latency_ms: float = 1.0,
    constraints: str = "none",
) -> str:
    """Return the planner user prompt with placeholders filled."""
    template = _load_prompt("planner_user.md")
    return template.format(
        research_goal=research_goal,
        strategy_style=strategy_style,
        latency_ms=latency_ms,
        constraints=constraints,
    )
