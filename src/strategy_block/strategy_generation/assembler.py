"""Deterministic assembler: converts agent Pydantic outputs to StrategySpec.

LLM outputs are never trusted directly — this module normalizes, clamps,
fills defaults, and records provenance before producing a StrategySpec.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from strategy_block.strategy_specs.schema import (
    ExitRule,
    FilterRule,
    PositionRule,
    SignalRule,
    StrategySpec,
)

from .agent_schemas import (
    ExitRuleDraft,
    FilterRuleDraft,
    IdeaBrief,
    KNOWN_FEATURES_SET,
    PositionRuleDraft,
    RiskDraft,
    SignalDraft,
    SignalRuleDraft,
)

logger = logging.getLogger(__name__)


def _convert_signal_rule(draft: SignalRuleDraft) -> SignalRule | None:
    """Convert a Pydantic SignalRuleDraft to a dataclass SignalRule.

    Returns None if the feature is unknown.
    """
    if draft.feature not in KNOWN_FEATURES_SET:
        logger.warning("Dropping signal rule with unknown feature: %s", draft.feature)
        return None
    return SignalRule(
        feature=draft.feature,
        operator=draft.operator,
        threshold=draft.threshold,
        score_contribution=max(-5.0, min(5.0, draft.score_contribution)),
        description=draft.description,
    )


def _convert_filter_rule(draft: FilterRuleDraft) -> FilterRule | None:
    if draft.feature not in KNOWN_FEATURES_SET:
        logger.warning("Dropping filter rule with unknown feature: %s", draft.feature)
        return None
    return FilterRule(
        feature=draft.feature,
        operator=draft.operator,
        threshold=draft.threshold,
        action=draft.action if draft.action in ("block", "reduce") else "block",
        description=draft.description,
    )


def _convert_position_rule(draft: PositionRuleDraft) -> PositionRule:
    return PositionRule(
        max_position=max(1, min(10000, draft.max_position)),
        sizing_mode=draft.sizing_mode,
        fixed_size=max(1, min(10000, draft.fixed_size)),
        holding_period_ticks=max(0, min(10000, draft.holding_period_ticks)),
        inventory_cap=max(1, min(10000, draft.inventory_cap)),
    )


def _convert_exit_rule(draft: ExitRuleDraft) -> ExitRule:
    return ExitRule(
        exit_type=draft.exit_type,
        threshold_bps=max(0.0, min(500.0, draft.threshold_bps)),
        timeout_ticks=max(0, min(100000, draft.timeout_ticks)),
        description=draft.description,
    )


def assemble_spec(
    *,
    idea: IdeaBrief,
    signal_draft: SignalDraft,
    risk_draft: RiskDraft,
    research_goal: str,
    latency_ms: float = 1.0,
    pipeline_version: str = "multi_agent_openai_v1",
) -> StrategySpec:
    """Assemble a StrategySpec from agent outputs.

    This is the deterministic bridge between LLM outputs and the existing
    codebase. Invalid features are dropped, values are clamped, and
    provenance is recorded in metadata.
    """
    # Convert signal rules (drop invalid)
    signal_rules = []
    for draft in signal_draft.signal_rules:
        rule = _convert_signal_rule(draft)
        if rule is not None:
            signal_rules.append(rule)

    if not signal_rules:
        raise ValueError("No valid signal rules after assembly — all features were unknown")

    # Convert filters (merge factor + risk additional filters)
    all_filter_drafts = list(signal_draft.filters)
    if hasattr(risk_draft, "additional_filters") and risk_draft.model_extra:
        pass  # additional_filters not in base RiskDraft
    filters = []
    for draft in all_filter_drafts:
        filt = _convert_filter_rule(draft)
        if filt is not None:
            filters.append(filt)

    # Convert position and exit rules
    position_rule = _convert_position_rule(risk_draft.position_rule)
    exit_rules = [_convert_exit_rule(d) for d in risk_draft.exit_rules]

    # Ensure at least stop_loss and time_exit
    exit_types_present = {r.exit_type for r in exit_rules}
    if "stop_loss" not in exit_types_present:
        exit_rules.append(ExitRule(exit_type="stop_loss", threshold_bps=20.0,
                                   description="Auto-added stop loss"))
    if "time_exit" not in exit_types_present:
        exit_rules.append(ExitRule(exit_type="time_exit", timeout_ticks=300,
                                   description="Auto-added time exit"))

    # Apply latency calibration
    latency_factor = max(1.0, latency_ms / 10.0)
    position_rule.holding_period_ticks = max(
        1, int(position_rule.holding_period_ticks * latency_factor)
    )
    for rule in exit_rules:
        if rule.exit_type == "time_exit" and rule.timeout_ticks > 0:
            rule.timeout_ticks = max(10, int(rule.timeout_ticks * latency_factor))

    metadata: dict[str, Any] = {
        "research_goal": research_goal,
        "idea_name": idea.name,
        "idea_thesis": idea.thesis,
        "idea_style": idea.style,
        "latency_ms": latency_ms,
        "latency_factor": latency_factor,
        "pipeline": pipeline_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return StrategySpec(
        name=idea.name,
        version="1.0",
        description=idea.thesis,
        signal_rules=signal_rules,
        filters=filters,
        position_rule=position_rule,
        exit_rules=exit_rules,
        metadata=metadata,
    )
