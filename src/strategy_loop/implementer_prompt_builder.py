"""
strategy_loop/implementer_prompt_builder.py
--------------------------------------------
Builds implementer LLM messages that translate a normalized StrategySpec
into executable Python code.

The code interface is identical to the existing pipeline:
  - UPPER_CASE numeric constants
  - def generate_signal(features, position) -> int | None

This module augments the user message with a structured implementation guide
derived from the spec — the system prompt (code_generation_system.txt) is
reused unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path
from string import Template
from typing import Any

from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
from strategy_loop.spec_schema import StrategySpec

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "conf" / "prompts"
_FEATURES_LIST = ", ".join(sorted(BUILTIN_FEATURES))
_CODE_GEN_SYSTEM = Template(
    (_PROMPTS_DIR / "code_generation_system.txt").read_text(encoding="utf-8")
).substitute(features_list=_FEATURES_LIST)


def _render_condition(c) -> str:
    """Render a SpecCondition as a Python-style expression string.

    When threshold_param is set, the constant name is used instead of the
    numeric literal (e.g. `> ORDER_IMBALANCE_THRESHOLD` not `> 0.3`).
    """
    rhs = c.threshold_param if c.threshold_param else c.threshold
    if c.source_type == "derived_feature":
        # Use the derived variable name directly (not features.get)
        return f"  {c.source} {c.op} {rhs}"
    # source_type == "feature"
    return f"  features.get({c.source!r}, 0.0) {c.op} {rhs}"


def _spec_to_guidance(spec: StrategySpec) -> str:
    """Render a StrategySpec as an implementation guide for the implementer LLM."""
    lines: list[str] = [
        "## Strategy spec — implement this exactly",
        f"Archetype: {spec.archetype} — {spec.archetype_name}",
        f"Rationale: {spec.rationale}",
    ]

    # ── Derived feature definitions (compute BEFORE entry/exit logic) ─
    if spec.derived_features:
        lines.append("")
        lines.append(
            "Derived features — compute these variables first, then use them in conditions:"
        )
        for df in spec.derived_features:
            input_list = ", ".join(f"features.get({inp!r}, 0.0)" for inp in df.inputs)
            lines.append(
                f"  {df.name} = {df.formula}  "
                f"# inputs: {', '.join(df.inputs)}"
            )
        lines.append(
            "  (Use features.get(key, 0.0) for each raw input from features dict.)"
        )

    # ── Entry conditions ──────────────────────────────────────────────
    lines.append("")
    lines.append("Entry conditions (ALL must be satisfied simultaneously):")
    for c in spec.entry_conditions:
        lines.append(_render_condition(c))

    # ── Exit conditions ───────────────────────────────────────────────
    lines.append("")
    lines.append(
        f"Exit — time floor: holding_ticks >= {spec.exit_time_ticks}  "
        f"(set HOLDING_TICKS_EXIT = {spec.exit_time_ticks})"
    )
    if spec.exit_signal_conditions:
        lines.append("Exit — signal reversal (ANY of):")
        for c in spec.exit_signal_conditions:
            lines.append(_render_condition(c))

    # ── Tunable params ────────────────────────────────────────────────
    if spec.tunable_params:
        lines.append("")
        lines.append("Suggested UPPER_CASE constants (Optuna will tune these):")
        for p in spec.tunable_params:
            lines.append(
                f"  {p.name} = {p.default}  "
                f"# type={p.type}, optuna_range={list(p.range)}"
            )

    return "\n".join(lines)


def build_implementer_messages(
    spec: StrategySpec,
    session_attempts: list[dict[str, Any]] | None = None,
    previous_feedback: dict[str, Any] | None = None,
    best_code_so_far: str | None = None,
    stuck_count: int = 0,
    rag_context: str = "",
) -> list[dict]:
    """Build implementer LLM messages from a normalized StrategySpec.

    Args:
        spec: Validated StrategySpec from spec_review.normalized_spec.
        session_attempts: Code attempts within this plan iteration.
        previous_feedback: Feedback from the most recent code attempt.
        best_code_so_far: Best-performing code within this plan (if any).
        stuck_count: Number of consecutive non-passing code attempts.
        rag_context: RAG memory context string.

    Returns:
        [{"role": "system", ...}, {"role": "user", ...}]
    """
    parts: list[str] = [_spec_to_guidance(spec)]

    if session_attempts:
        lines = [
            f"[Code attempts for this spec — {len(session_attempts)} so far]"
        ]
        for a in session_attempts:
            line = (
                f"  - attempt {a['iteration']}: {a['strategy_name']}"
                f"  entry_freq={a.get('entry_frequency', 0.0):.4f}"
                f"  net_pnl={a.get('net_pnl', 0.0):.1f}"
                f"  verdict={a.get('verdict', '?')}"
            )
            if a.get("primary_issue"):
                line += f"  → {a['primary_issue']}"
            lines.append(line)
        parts.append("\n".join(lines))

    if best_code_so_far:
        parts.append(
            "Best code for this spec so far (profitable — refine, do not replace):\n"
            f"```python\n{best_code_so_far}\n```"
        )

    if rag_context:
        parts.append(f"Past code patterns (learn from these):\n{rag_context}")

    if previous_feedback:
        primary = previous_feedback.get("primary_issue", "")
        issues = previous_feedback.get("issues", [])
        suggestions = previous_feedback.get("suggestions", [])
        control_mode = previous_feedback.get("control_mode", "neutral")

        _mode_guidance = {
            "explore": (
                "⚠ The current logic family is not working. "
                "Relax ALL threshold constants significantly or simplify the entry logic."
            ),
            "repair": (
                "Keep the same general logic; adjust threshold constants, "
                "holding period, or cost filters."
            ),
            "neutral": "Avoid recent failure patterns.",
        }

        fb_lines = [
            f"Previous feedback — fix this: {primary}" if primary else "Previous feedback:",
            f"  control_mode: {control_mode}",
            f"  {_mode_guidance.get(control_mode, _mode_guidance['neutral'])}",
        ]
        if issues:
            fb_lines.append("  Issues: " + "; ".join(issues))
        if suggestions:
            fb_lines.append("  Suggestions: " + "; ".join(suggestions))
        parts.append("\n".join(fb_lines))

    if stuck_count >= 3:
        parts.append(
            f"⚠ {stuck_count} consecutive non-passing code attempts for this spec. "
            "Relax ALL UPPER_CASE threshold constants to significantly widen entry conditions."
        )

    parts.append("Generate the strategy Python code.")

    user_content = re.sub(
        r"\n{3,}", "\n\n", "\n\n".join(p for p in parts if p.strip())
    ).strip()
    return [
        {"role": "system", "content": _CODE_GEN_SYSTEM},
        {"role": "user", "content": user_content},
    ]
