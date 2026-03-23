"""Multi-Agent wrappers for strategy generation.

Each agent encapsulates:
- A system prompt loaded from ``agents/*.md`` templates
- A structured output schema
- An OpenAI client call
- A heuristic fallback when LLM is unavailable

Agent prompts are stored as separate Markdown files under
``strategy_generation/agents/``.  Shared constraint blocks
(features, operators, sizing modes, exit types) are injected
via placeholder substitution at load time.
"""
from __future__ import annotations

import logging
from typing import Any

from .agent_schemas import (
    ExitRuleDraft,
    FilterRuleDraft,
    IdeaBrief,
    IdeaBriefList,
    KNOWN_FEATURES_LIST,
    PositionRuleDraft,
    ReviewDecision,
    ReviewIssueDraft,
    RiskDraft,
    SignalDraft,
    SignalRuleDraft,
    VALID_EXIT_TYPES,
    VALID_OPERATORS,
    VALID_SIZING_MODES,
)
from .openai_client import OpenAIStrategyGenClient
from .prompt_loader import load_agent_prompt

logger = logging.getLogger(__name__)

# ── Shared prompt fragments (generated from schemas) ─────────────────

_FEATURES_BLOCK = (
    "ALLOWED FEATURES (use ONLY these):\n"
    + "\n".join(f"  - {f}" for f in KNOWN_FEATURES_LIST)
)

_OPERATORS_BLOCK = f"ALLOWED OPERATORS: {', '.join(VALID_OPERATORS)}"

_SIZING_BLOCK = f"ALLOWED SIZING MODES: {', '.join(VALID_SIZING_MODES)}"

_EXIT_TYPES_BLOCK = f"ALLOWED EXIT TYPES: {', '.join(VALID_EXIT_TYPES)}"

_PROMPT_CONTEXT: dict[str, str] = {
    "FEATURES_BLOCK": _FEATURES_BLOCK,
    "OPERATORS_BLOCK": _OPERATORS_BLOCK,
    "SIZING_BLOCK": _SIZING_BLOCK,
    "EXIT_TYPES_BLOCK": _EXIT_TYPES_BLOCK,
}


def _load(name: str) -> str:
    """Shorthand: load an agent prompt with the shared context."""
    return load_agent_prompt(name, _PROMPT_CONTEXT)


# ── Researcher Agent ──────────────────────────────────────────────────


class ResearcherAgent:
    """Proposes strategy ideas from a research goal."""

    def __init__(self, client: OpenAIStrategyGenClient | None = None) -> None:
        self.client = client

    def run(self, research_goal: str, n_ideas: int = 3) -> IdeaBriefList:
        if self.client is not None:
            result = self.client.query_structured(
                system_prompt=_load("researcher"),
                user_prompt=f"Propose {n_ideas} tick-level strategy ideas for: {research_goal}",
                schema=IdeaBriefList,
            )
            if result is not None:
                return result
            logger.info("ResearcherAgent: LLM unavailable, using fallback")
        return self._fallback(research_goal, n_ideas)

    def _fallback(self, research_goal: str, n_ideas: int) -> IdeaBriefList:
        """Deterministic fallback: produce ideas from template keywords."""
        ideas = [
            IdeaBrief(
                name="imbalance_momentum",
                thesis="Order book imbalance predicts short-term price direction",
                core_features=["order_imbalance", "depth_imbalance", "trade_flow_imbalance"],
                style="momentum",
                rationale="Heavy bid side → price likely to rise in next ticks",
            ),
            IdeaBrief(
                name="spread_mean_reversion",
                thesis="Spread widens temporarily then reverts, providing contrarian entry",
                core_features=["spread_bps", "order_imbalance", "mid_price"],
                style="mean_reversion",
                rationale="Wide spread = temporary liquidity shock → fade the move",
            ),
            IdeaBrief(
                name="trade_flow_pressure",
                thesis="Sustained directional trade flow predicts continuation",
                core_features=["trade_flow_imbalance", "volume_surprise", "recent_volume"],
                style="momentum",
                rationale="Aggressive trade flow indicates informed trading",
            ),
        ]
        return IdeaBriefList(ideas=ideas[:n_ideas])


# ── Factor Designer Agent ─────────────────────────────────────────────


class FactorDesignerAgent:
    """Designs signal rules and filters for a strategy idea."""

    def __init__(self, client: OpenAIStrategyGenClient | None = None) -> None:
        self.client = client

    def run(self, idea: IdeaBrief) -> SignalDraft:
        if self.client is not None:
            result = self.client.query_structured(
                system_prompt=_load("factor_designer"),
                user_prompt=(
                    f"Design signal rules for strategy '{idea.name}'.\n"
                    f"Thesis: {idea.thesis}\n"
                    f"Core features: {', '.join(idea.core_features)}\n"
                    f"Style: {idea.style}"
                ),
                schema=SignalDraft,
            )
            if result is not None:
                return result
            logger.info("FactorDesignerAgent: LLM unavailable, using fallback")
        return self._fallback(idea)

    def _fallback(self, idea: IdeaBrief) -> SignalDraft:
        """Heuristic fallback based on idea style and features."""
        features = idea.core_features or ["order_imbalance"]
        primary = features[0]

        if idea.style in ("momentum", "microstructure"):
            rules = [
                SignalRuleDraft(feature=primary, operator=">", threshold=0.3,
                                score_contribution=0.5, description=f"Bullish {primary}"),
                SignalRuleDraft(feature=primary, operator="<", threshold=-0.3,
                                score_contribution=-0.5, description=f"Bearish {primary}"),
            ]
        else:  # mean_reversion, contrarian
            rules = [
                SignalRuleDraft(feature=primary, operator=">", threshold=0.5,
                                score_contribution=-0.4, description=f"Contrarian sell on high {primary}"),
                SignalRuleDraft(feature=primary, operator="<", threshold=-0.5,
                                score_contribution=0.4, description=f"Contrarian buy on low {primary}"),
            ]

        # Add depth feature if available
        if len(features) > 1 and features[1] in ("depth_imbalance", "trade_flow_imbalance"):
            sec = features[1]
            rules.append(SignalRuleDraft(
                feature=sec, operator=">", threshold=0.2,
                score_contribution=0.3, description=f"Supporting bullish {sec}",
            ))
            rules.append(SignalRuleDraft(
                feature=sec, operator="<", threshold=-0.2,
                score_contribution=-0.3, description=f"Supporting bearish {sec}",
            ))

        filters = [
            FilterRuleDraft(feature="spread_bps", operator=">", threshold=30.0,
                            action="block", description="Block on wide spread"),
        ]

        return SignalDraft(signal_rules=rules, filters=filters, rationale="Heuristic fallback")


# ── Risk Designer Agent ────────────────────────────────────────────────


class RiskDesignerAgent:
    """Designs position rules and exit rules."""

    def __init__(self, client: OpenAIStrategyGenClient | None = None) -> None:
        self.client = client

    def run(self, idea: IdeaBrief, signal_draft: SignalDraft, latency_ms: float = 1.0) -> RiskDraft:
        if self.client is not None:
            result = self.client.query_structured(
                system_prompt=_load("risk_designer"),
                user_prompt=(
                    f"Design risk/exit rules for strategy '{idea.name}'.\n"
                    f"Style: {idea.style}\n"
                    f"Signal rules: {len(signal_draft.signal_rules)}\n"
                    f"Expected latency: {latency_ms}ms\n"
                    f"Thesis: {idea.thesis}"
                ),
                schema=RiskDraft,
            )
            if result is not None:
                return result
            logger.info("RiskDesignerAgent: LLM unavailable, using fallback")
        return self._fallback(idea, latency_ms)

    def _fallback(self, idea: IdeaBrief, latency_ms: float) -> RiskDraft:
        """Conservative heuristic risk parameters."""
        return RiskDraft(
            position_rule=PositionRuleDraft(
                max_position=500,
                sizing_mode="signal_proportional",
                fixed_size=100,
                holding_period_ticks=10,
                inventory_cap=1000,
            ),
            exit_rules=[
                ExitRuleDraft(exit_type="stop_loss", threshold_bps=15.0,
                              description="Stop loss at 15 bps"),
                ExitRuleDraft(exit_type="take_profit", threshold_bps=25.0,
                              description="Take profit at 25 bps"),
                ExitRuleDraft(exit_type="time_exit", timeout_ticks=300,
                              description="Time exit after 300 ticks"),
            ],
            latency_notes=f"Designed for {latency_ms}ms latency",
        )


# ── LLM Reviewer Agent ────────────────────────────────────────────────


class LLMReviewerAgent:
    """LLM-based soft review (complement to the static StrategyReviewer)."""

    def __init__(self, client: OpenAIStrategyGenClient | None = None) -> None:
        self.client = client

    def run(self, spec_dict: dict[str, Any]) -> ReviewDecision:
        if self.client is not None:
            import json
            result = self.client.query_structured(
                system_prompt=_load("reviewer"),
                user_prompt=f"Review this strategy spec:\n{json.dumps(spec_dict, indent=2)}",
                schema=ReviewDecision,
            )
            if result is not None:
                return result
            logger.info("LLMReviewerAgent: LLM unavailable, using fallback")
        return self._fallback(spec_dict)

    def _fallback(self, spec_dict: dict[str, Any]) -> ReviewDecision:
        """Fallback: delegate to the static StrategyReviewer."""
        from strategy_block.strategy_specs.schema import StrategySpec
        from strategy_block.strategy_review.reviewer import StrategyReviewer

        spec = StrategySpec.from_dict(spec_dict)
        reviewer = StrategyReviewer()
        result = reviewer.review(spec)

        issues = [
            ReviewIssueDraft(
                category=issue.category,
                severity=issue.severity,
                description=issue.description,
                suggestion=issue.suggestion,
            )
            for issue in result.issues
        ]

        return ReviewDecision(
            approved=result.passed,
            issues=issues,
            confidence=1.0 if result.passed else 0.3,
        )
