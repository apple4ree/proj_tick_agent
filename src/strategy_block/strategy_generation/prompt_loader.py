"""Prompt loader for multi-agent strategy generation.

Reads agent prompt templates from ``agents/*.md`` and substitutes
placeholders (``{FEATURES_BLOCK}``, ``{OPERATORS_BLOCK}``, etc.) with
runtime-generated constraint strings.
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_agent_prompt(name: str, context: dict[str, str]) -> str:
    """Load an agent prompt template and substitute placeholders.

    Parameters
    ----------
    name:
        Stem name of the prompt file (without ``.md`` extension).
        E.g. ``"researcher"``, ``"factor_designer"``.
    context:
        Mapping of placeholder names to replacement strings.
        E.g. ``{"FEATURES_BLOCK": "...", "OPERATORS_BLOCK": "..."}``.

    Returns
    -------
    str
        The final prompt text with all placeholders replaced.

    Raises
    ------
    FileNotFoundError
        If the prompt file does not exist.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Agent prompt file not found: {path}. "
            f"Expected a prompt template at {path.relative_to(path.parents[3])}."
        )
    template = path.read_text(encoding="utf-8")
    for key, value in context.items():
        template = template.replace(f"{{{key}}}", value)
    return template
