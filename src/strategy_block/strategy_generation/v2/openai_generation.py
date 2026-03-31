"""OpenAI-based v2 strategy generation via structured plan + lowering.

Flow:
  goal -> build prompts -> OpenAI structured output -> StrategyPlan
  -> validate plan -> lower_plan_to_spec_v2 -> StrategySpecV2
  -> StrategyReviewerV2 -> pass/fail
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2

from ..openai_client import OpenAIStrategyGenClient
from .lowering import lower_plan_to_spec_v2
from .schemas.plan_schema import StrategyPlan
from .utils.prompt_builder import build_system_prompt, build_user_prompt
from .utils.response_parser import (
    PlanParseError,
    check_position_attr_misuse,
    collect_pre_review_flags,
    parse_plan_response,
    validate_plan,
)

logger = logging.getLogger(__name__)


def _execution_policy_trace_flags(plan: StrategyPlan) -> dict[str, Any]:
    return collect_pre_review_flags(plan)


def _build_mock_plan(research_goal: str) -> StrategyPlan:
    """Build a deterministic mock plan for testing without API calls."""
    goal_lower = research_goal.lower()

    if "reversion" in goal_lower or "mean" in goal_lower:
        style = "mean_reversion"
        name = "openai_mean_reversion_plan"
        desc = "Mean-reversion on order imbalance extremes with spread filter."
        entry_side_long_feat = "order_imbalance"
        entry_long_op = "<"
        entry_long_thresh = -0.35
        entry_side_short_feat = "order_imbalance"
        entry_short_op = ">"
        entry_short_thresh = 0.35
    elif "spread" in goal_lower:
        style = "mean_reversion"
        name = "openai_spread_fade_plan"
        desc = "Fade extreme imbalance when spread is wide."
        entry_side_long_feat = "order_imbalance"
        entry_long_op = "<"
        entry_long_thresh = -0.4
        entry_side_short_feat = "order_imbalance"
        entry_short_op = ">"
        entry_short_thresh = 0.4
    else:
        style = "momentum"
        name = "openai_imbalance_momentum_plan"
        desc = "Momentum entry on sustained order imbalance confirmed by depth."
        entry_side_long_feat = "order_imbalance"
        entry_long_op = ">"
        entry_long_thresh = 0.3
        entry_side_short_feat = "order_imbalance"
        entry_short_op = "<"
        entry_short_thresh = -0.3

    from .schemas.plan_schema import (
        ConditionPlan,
        EntryPlan,
        ExecutionPlan,
        ExitPolicyPlan,
        ExitRulePlan,
        PreconditionPlan,
        RiskPlan,
    )

    return StrategyPlan(
        name=name,
        description=desc,
        research_goal=research_goal,
        strategy_style=style,
        preconditions=[
            PreconditionPlan(
                name="spread_ok",
                condition=ConditionPlan(feature="spread_bps", op="<", threshold=30.0),
            ),
        ],
        entry_policies=[
            EntryPlan(
                name="long_entry",
                side="long",
                trigger=ConditionPlan(
                    combine="all",
                    children=[
                        ConditionPlan(feature=entry_side_long_feat, op=entry_long_op, threshold=entry_long_thresh),
                        ConditionPlan(feature="depth_imbalance", op=">", threshold=0.1),
                    ],
                ),
                strength=0.6,
                cooldown_ticks=50,
                no_reentry_until_flat=True,
            ),
            EntryPlan(
                name="short_entry",
                side="short",
                trigger=ConditionPlan(
                    combine="all",
                    children=[
                        ConditionPlan(feature=entry_side_short_feat, op=entry_short_op, threshold=entry_short_thresh),
                        ConditionPlan(feature="depth_imbalance", op="<", threshold=-0.1),
                    ],
                ),
                strength=0.6,
                cooldown_ticks=50,
                no_reentry_until_flat=True,
            ),
        ],
        exit_policies=[
            ExitPolicyPlan(
                name="risk_exits",
                rules=[
                    ExitRulePlan(
                        name="stop_loss",
                        priority=1,
                        condition=ConditionPlan(
                            position_attr="unrealized_pnl_bps",
                            op="<=",
                            threshold=-25.0,
                        ),
                        action="close_all",
                    ),
                    ExitRulePlan(
                        name="time_exit",
                        priority=2,
                        condition=ConditionPlan(
                            position_attr="holding_ticks",
                            op=">=",
                            threshold=30.0,
                        ),
                        action="close_all",
                    ),
                    ExitRulePlan(
                        name="spread_exit",
                        priority=3,
                        condition=ConditionPlan(feature="spread_bps", op=">", threshold=25.0),
                        action="close_all",
                    ),
                ],
            ),
        ],
        risk_policy=RiskPlan(
            max_position=400,
            inventory_cap=800,
            sizing_mode="fixed",
            base_size=100,
            max_size=400,
        ),
        execution_policy=ExecutionPlan(
            placement_mode="passive_join",
            cancel_after_ticks=15,
            max_reprices=2,
        ),
        notes=f"Mock plan generated for goal: {research_goal}",
    )


def generate_plan_with_openai(
    *,
    client: OpenAIStrategyGenClient,
    research_goal: str,
    latency_ms: float = 1.0,
    strategy_style: str = "auto",
    backtest_environment: dict[str, Any] | None = None,
) -> tuple[StrategyPlan, dict[str, Any]]:
    """Generate a StrategyPlan using OpenAI structured output.

    Returns (plan, trace_info) where trace_info contains parsing metadata.
    """
    trace: dict[str, Any] = {
        "stage": "plan_generation",
        "mode": client.mode,
        "model": client.model,
    }

    if client.mode == "mock":
        plan = _build_mock_plan(research_goal)
        trace["parse_success"] = True
        trace["source"] = "mock"
        flags = _execution_policy_trace_flags(plan)
        trace.update(flags)
        trace["pre_review_flags"] = dict(flags)

        plan_warnings = validate_plan(plan)
        if plan_warnings:
            trace["plan_warnings"] = plan_warnings
            for w in plan_warnings:
                logger.warning("Plan validation warning: %s", w)

        # Hard gate applies to mock plans too
        attr_errors = check_position_attr_misuse(plan)
        if attr_errors:
            trace["position_attr_errors"] = attr_errors
            raise PlanParseError(
                f"Mock plan uses position_attr-only names as feature "
                f"({len(attr_errors)} error(s)): {attr_errors[0]}",
                raw_response=plan,
            )

        return plan, trace

    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(
        research_goal=research_goal,
        strategy_style=strategy_style,
        latency_ms=latency_ms,
        backtest_environment=backtest_environment,
    )

    response = client.query_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=StrategyPlan,
    )

    if response is None:
        trace["parse_success"] = False
        trace["source"] = "api_failed"
        trace["client_meta"] = client.last_query_meta
        raise PlanParseError(
            f"OpenAI returned no response (mode={client.mode}, status={client.last_query_meta.get('status')})",
        )

    plan = parse_plan_response(response)
    trace["parse_success"] = True
    trace["source"] = client.mode
    trace["client_meta"] = client.last_query_meta
    flags = _execution_policy_trace_flags(plan)
    trace.update(flags)
    trace["pre_review_flags"] = dict(flags)

    plan_warnings = validate_plan(plan)
    if plan_warnings:
        trace["plan_warnings"] = plan_warnings
        for w in plan_warnings:
            logger.warning("Plan validation warning: %s", w)

    # Hard gate: position_attr-only names used as feature → reject
    attr_errors = check_position_attr_misuse(plan)
    if attr_errors:
        trace["position_attr_errors"] = attr_errors
        for e in attr_errors:
            logger.error("Plan position_attr misuse: %s", e)
        raise PlanParseError(
            f"Plan uses position_attr-only names as feature "
            f"({len(attr_errors)} error(s)): {attr_errors[0]}",
            raw_response=plan,
        )

    return plan, trace


def generate_spec_v2_with_openai(
    *,
    client: OpenAIStrategyGenClient,
    research_goal: str,
    latency_ms: float = 1.0,
    strategy_style: str = "auto",
    backtest_environment: dict[str, Any] | None = None,
    reviewer: StrategyReviewerV2 | None = None,
) -> tuple[StrategySpecV2, dict[str, Any]]:
    """Full OpenAI v2 generation: plan -> lower -> review.

    Returns (spec, trace).
    Raises PlanParseError if OpenAI response is unparseable.
    """
    plan, plan_trace = generate_plan_with_openai(
        client=client,
        research_goal=research_goal,
        latency_ms=latency_ms,
        strategy_style=strategy_style,
        backtest_environment=backtest_environment,
    )

    # Pre-lowering guard: reject plans with position_attr misuse
    attr_errors = check_position_attr_misuse(plan)
    if attr_errors:
        raise PlanParseError(
            f"Plan has position_attr misuse, cannot lower: {attr_errors[0]}",
            raw_response=plan,
        )

    spec = lower_plan_to_spec_v2(plan, latency_ms=latency_ms)
    ep_flags = _execution_policy_trace_flags(plan)

    spec.metadata = dict(spec.metadata or {})
    spec.metadata.update({
        "research_goal": research_goal,
        "strategy_style": plan.strategy_style,
        "latency_ms": latency_ms,
        "spec_canonical": "v2",
        "generation_source": "openai_v2_plan",
        "plan_schema_version": plan.SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_policy_explicit": ep_flags["execution_policy_explicit"],
        "inferred_holding_horizon_ticks": ep_flags["inferred_holding_horizon_ticks"],
        "inferred_short_horizon": ep_flags["inferred_short_horizon"],
        "execution_policy_missing_short_horizon": ep_flags["execution_policy_missing_short_horizon"],
        "invalid_zero_horizon": ep_flags["invalid_zero_horizon"],
        "aggressive_passive_short_horizon": ep_flags["aggressive_passive_short_horizon"],
    })

    input_ctx: dict[str, Any] = {
        "research_goal": research_goal,
        "strategy_style": strategy_style,
        "latency_ms": latency_ms,
    }
    if backtest_environment is not None:
        input_ctx["backtest_environment"] = backtest_environment

    trace: dict[str, Any] = {
        "pipeline": "openai_v2_plan_generation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": input_ctx,
        "pre_review_flags": dict(ep_flags),
        "plan": {
            "name": plan.name,
            "strategy_style": plan.strategy_style,
            "n_entries": len(plan.entry_policies),
            "n_exits": len(plan.exit_policies),
            "n_regimes": len(plan.regimes),
            "has_state_policy": plan.state_policy is not None,
            "has_execution_policy": plan.execution_policy is not None,
            "execution_policy_explicit": ep_flags["execution_policy_explicit"],
            "inferred_holding_horizon_ticks": ep_flags["inferred_holding_horizon_ticks"],
            "inferred_short_horizon": ep_flags["inferred_short_horizon"],
            "execution_policy_missing_short_horizon": ep_flags["execution_policy_missing_short_horizon"],
            "invalid_zero_horizon": ep_flags["invalid_zero_horizon"],
            "aggressive_passive_short_horizon": ep_flags["aggressive_passive_short_horizon"],
            "schema_version": plan.SCHEMA_VERSION,
        },
        "plan_trace": plan_trace,
        "output": {
            "spec_name": spec.name,
            "spec_version": spec.version,
            "spec_format": "v2",
            "n_entry_policies": len(spec.entry_policies),
            "n_exit_policies": len(spec.exit_policies),
            "n_preconditions": len(spec.preconditions),
            "n_regimes": len(spec.regimes),
            "execution_policy_explicit": spec.metadata.get("execution_policy_explicit"),
            "execution_policy_missing_short_horizon": spec.metadata.get("execution_policy_missing_short_horizon"),
            "invalid_zero_horizon": spec.metadata.get("invalid_zero_horizon"),
            "aggressive_passive_short_horizon": spec.metadata.get("aggressive_passive_short_horizon"),
        },
        "generation_rescue_attempted": False,
        "generation_rescue_applied": False,
        "generation_rescue_operations": [],
        "rescue": {
            "attempted": False,
            "applied": False,
            "operations": [],
            "reasons": [],
            "metadata": {},
        },
        "post_rescue_review": None,
        "fallback_used": False,
        "fallback": {"used": False, "count": 0, "events": []},
    }

    if reviewer is not None:
        review_result = reviewer.review(spec, backtest_environment=backtest_environment)
        trace["static_review"] = review_result.to_dict()
        trace["static_review_passed"] = review_result.passed
    else:
        trace["static_review_passed"] = None

    return spec, trace
