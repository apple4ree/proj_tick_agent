"""Lowers intermediate representations into StrategySpecV2.

Supports two input formats:
1. Template dicts (from templates_v2.py) via lower_to_spec_v2()
2. StrategyPlan (from OpenAI structured output) via lower_plan_to_spec_v2()

Phase 3 additions:
- state_policy lowering
- risk_policy.degradation_rules lowering
- execution_policy.adaptation_rules lowering
- state_var expression lowering
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from strategy_block.strategy_specs.v2.ast_nodes import (
    AllExpr,
    AnyExpr,
    ComparisonExpr,
    ConstExpr,
    CrossExpr,
    ExprNode,
    FeatureExpr,
    LagExpr,
    NotExpr,
    PersistExpr,
    RollingExpr,
    StateVarExpr,
    PositionAttrExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryConstraints,
    EntryPolicyV2,
    ExecutionAdaptationOverrideV2,
    ExecutionAdaptationRuleV2,
    ExecutionPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    PositionSizingV2,
    PreconditionV2,
    RegimeV2,
    RiskDegradationActionV2,
    RiskDegradationRuleV2,
    RiskPolicyV2,
    StateEventV2,
    StateGuardV2,
    StatePolicyV2,
    StateUpdateV2,
    StrategySpecV2,
)


def _expr_from_template(d: dict[str, Any]) -> ExprNode:
    """Convert template expression dict to AST node.

    Supports:
    1) compact condition form: {feature, op, threshold}
    2) explicit AST form with `type`
    """
    if "type" not in d:
        return ComparisonExpr(
            feature=d["feature"],
            op=d["op"],
            threshold=d["threshold"],
        )

    t = d["type"]
    if t == "const":
        return ConstExpr(value=float(d.get("value", 0.0)))
    if t == "feature":
        return FeatureExpr(name=d["name"])
    if t == "state_var":
        return StateVarExpr(name=d["name"])
    if t == "position_attr":
        return PositionAttrExpr(name=d["name"])
    if t == "comparison":
        left = None
        if "left" in d and d["left"] is not None:
            left = _expr_from_template(d["left"])
        return ComparisonExpr(
            feature=d.get("feature", ""),
            op=d["op"],
            threshold=d["threshold"],
            left=left,
        )
    if t == "all":
        return AllExpr(children=[_expr_from_template(c) for c in d.get("children", [])])
    if t == "any":
        return AnyExpr(children=[_expr_from_template(c) for c in d.get("children", [])])
    if t == "not":
        return NotExpr(child=_expr_from_template(d["child"]))
    if t == "cross":
        return CrossExpr(
            feature=d["feature"],
            threshold=d.get("threshold", 0.0),
            direction=d.get("direction", "above"),
        )
    if t == "lag":
        return LagExpr(feature=d["feature"], steps=d.get("steps", 1))
    if t == "rolling":
        return RollingExpr(
            feature=d["feature"],
            method=d.get("method", "mean"),
            window=d.get("window", 5),
        )
    if t == "persist":
        return PersistExpr(
            expr=_expr_from_template(d["expr"]),
            window=d.get("window", 5),
            min_true=d.get("min_true", 3),
        )

    raise ValueError(f"Unsupported template AST node type: {t!r}")


def _lower_entry(entry: dict[str, Any]) -> EntryPolicyV2:
    """Lower a template entry dict to an EntryPolicyV2."""
    trigger_type = entry.get("trigger_type", "all")

    if trigger_type == "ast":
        trigger = _expr_from_template(entry["trigger"])
    elif trigger_type == "cross":
        trigger = CrossExpr(
            feature=entry["cross_feature"],
            threshold=entry.get("cross_threshold", 0.0),
            direction=entry.get("cross_direction", "above"),
        )
    elif trigger_type == "persist":
        persist_cond = _expr_from_template(entry["persist_expr"])
        trigger = PersistExpr(
            expr=persist_cond,
            window=entry.get("persist_window", 5),
            min_true=entry.get("persist_min_true", 3),
        )
    elif trigger_type == "any":
        trigger = AnyExpr(
            children=[_expr_from_template(c) for c in entry.get("conditions", [])]
        )
    else:
        children: list[ExprNode] = [
            _expr_from_template(c) for c in entry.get("conditions", [])
        ]

        for extra in entry.get("extra_conditions", []):
            if extra.get("type") == "rolling_comparison":
                children.append(
                    ComparisonExpr(
                        left=RollingExpr(
                            feature=extra["rolling_feature"],
                            method=extra.get("rolling_method", "mean"),
                            window=extra.get("rolling_window", 5),
                        ),
                        op=extra["op"],
                        threshold=extra["threshold"],
                    )
                )
            else:
                children.append(_expr_from_template(extra))

        if len(children) == 1:
            trigger = children[0]
        else:
            trigger = AllExpr(children=children)

    if "strength_expr" in entry:
        strength = _expr_from_template(entry["strength_expr"])
    else:
        strength = ConstExpr(value=entry.get("strength_value", 0.5))

    constraints = EntryConstraints(
        cooldown_ticks=entry.get("cooldown_ticks", 0),
        no_reentry_until_flat=entry.get("no_reentry_until_flat", False),
    )

    return EntryPolicyV2(
        name=entry["name"],
        side=entry["side"],
        trigger=trigger,
        strength=strength,
        constraints=constraints,
    )


def _lower_exit_rule(rule: dict[str, Any]) -> ExitRuleV2:
    """Lower a template exit rule dict to an ExitRuleV2."""
    action_str = rule.get("action", "close_all")
    if isinstance(action_str, str):
        if action_str == "close_all":
            action = ExitActionV2(type="close_all")
        elif action_str == "reduce_position":
            action = ExitActionV2(
                type="reduce_position",
                reduce_fraction=rule.get("reduce_fraction", 0.5),
            )
        else:
            action = ExitActionV2(type=action_str)
    else:
        action = ExitActionV2.from_dict(action_str)

    return ExitRuleV2(
        name=rule["name"],
        priority=rule.get("priority", 10),
        condition=_expr_from_template(rule["condition"]),
        action=action,
    )


def _lower_regime(regime_dict: dict[str, Any]) -> RegimeV2:
    """Lower a template regime dict to a RegimeV2."""
    when_expr = _expr_from_template(regime_dict["when"])

    risk_override = None
    if "risk_override" in regime_dict:
        risk_override = _lower_risk_policy(regime_dict["risk_override"])

    execution_override = None
    if "execution_override" in regime_dict:
        execution_override = _lower_execution_policy(regime_dict["execution_override"])

    return RegimeV2(
        name=regime_dict["name"],
        priority=regime_dict.get("priority", 10),
        when=when_expr,
        entry_policy_refs=regime_dict.get("entry_policy_refs", []),
        exit_policy_ref=regime_dict.get("exit_policy_ref", ""),
        risk_override=risk_override,
        execution_override=execution_override,
    )


def _lower_execution_policy(ep_dict: dict[str, Any]) -> ExecutionPolicyV2:
    """Lower a template execution policy dict to an ExecutionPolicyV2."""
    dnt = None
    if "do_not_trade_when" in ep_dict:
        dnt = _expr_from_template(ep_dict["do_not_trade_when"])

    adaptation_rules = [
        ExecutionAdaptationRuleV2(
            condition=_expr_from_template(r["condition"]),
            override=ExecutionAdaptationOverrideV2(
                placement_mode=r.get("override", {}).get("placement_mode"),
                cancel_after_ticks=r.get("override", {}).get("cancel_after_ticks"),
                max_reprices=r.get("override", {}).get("max_reprices"),
            ),
        )
        for r in ep_dict.get("adaptation_rules", [])
    ]

    return ExecutionPolicyV2(
        placement_mode=ep_dict.get("placement_mode", "passive_join"),
        cancel_after_ticks=ep_dict.get("cancel_after_ticks", 0),
        max_reprices=ep_dict.get("max_reprices", 0),
        do_not_trade_when=dnt,
        adaptation_rules=adaptation_rules,
    )


def _lower_state_policy(sp_dict: dict[str, Any]) -> StatePolicyV2:
    """Lower a template state_policy dict to StatePolicyV2."""
    vars_dict = {
        str(k): float(v)
        for k, v in sp_dict.get("vars", {}).items()
    }

    guards = [
        StateGuardV2(
            name=g["name"],
            condition=_expr_from_template(g["condition"]),
            effect=g.get("effect", "block_entry"),
        )
        for g in sp_dict.get("guards", [])
    ]

    events: list[StateEventV2] = []
    for e in sp_dict.get("events", []):
        updates = [
            StateUpdateV2(
                var=u["var"],
                op=u["op"],
                value=float(u.get("value", 0.0)),
            )
            for u in e.get("updates", [])
        ]
        events.append(StateEventV2(name=e["name"], on=e["on"], updates=updates))

    return StatePolicyV2(vars=vars_dict, guards=guards, events=events)


def _lower_risk_policy(risk_dict: dict[str, Any]) -> RiskPolicyV2:
    degradation_rules = [
        RiskDegradationRuleV2(
            condition=_expr_from_template(r["condition"]),
            action=RiskDegradationActionV2(
                type=r.get("action", {}).get("type", "block_new_entries"),
                factor=float(r.get("action", {}).get("factor", 1.0)),
            ),
        )
        for r in risk_dict.get("degradation_rules", [])
    ]

    return RiskPolicyV2(
        max_position=risk_dict.get("max_position", 500),
        inventory_cap=risk_dict.get("inventory_cap", 1000),
        position_sizing=PositionSizingV2(
            mode=risk_dict.get("sizing_mode", "fixed"),
            base_size=risk_dict.get("base_size", 100),
            max_size=risk_dict.get("max_size", 500),
        ),
        degradation_rules=degradation_rules,
    )


def lower_to_spec_v2(template: dict[str, Any]) -> StrategySpecV2:
    """Convert a template intermediate dict into a StrategySpecV2."""
    preconditions = [
        PreconditionV2(
            name=pc["name"],
            condition=_expr_from_template(pc),
        )
        for pc in template.get("preconditions", [])
    ]

    entry_policies = [_lower_entry(e) for e in template.get("entries", [])]

    exit_policies = [
        ExitPolicyV2(
            name=xp["name"],
            rules=[_lower_exit_rule(r) for r in xp.get("rules", [])],
        )
        for xp in template.get("exits", [])
    ]

    risk_policy = _lower_risk_policy(template.get("risk", {}))

    regimes = [_lower_regime(r) for r in template.get("regimes", [])]

    execution_policy = None
    if "execution_policy" in template:
        execution_policy = _lower_execution_policy(template["execution_policy"])

    state_policy = None
    if "state_policy" in template:
        state_policy = _lower_state_policy(template["state_policy"])

    return StrategySpecV2(
        name=template["name"],
        version="2.0",
        description=template.get("description", ""),
        spec_format="v2",
        preconditions=preconditions,
        entry_policies=entry_policies,
        exit_policies=exit_policies,
        risk_policy=risk_policy,
        regimes=regimes,
        execution_policy=execution_policy,
        state_policy=state_policy,
        metadata={
            "pipeline": "v2_template_lowering",
            "template_name": template["name"],
        },
    )


# ── Plan-based lowering (OpenAI structured output) ──────────────────


def _condition_plan_to_expr(cond: Any) -> ExprNode:
    """Convert a ConditionPlan to an ExprNode.

    Handles all condition types from the plan schema.
    """
    # Composite (all / any)
    if cond.combine is not None and cond.children:
        children = [_condition_plan_to_expr(c) for c in cond.children]
        if cond.combine == "any":
            return AnyExpr(children=children)
        return AllExpr(children=children)

    # Cross condition
    if cond.cross_feature is not None:
        return CrossExpr(
            feature=cond.cross_feature,
            threshold=cond.cross_threshold or 0.0,
            direction=cond.cross_direction or "above",
        )

    # Persist condition
    if cond.persist_condition is not None:
        inner = _condition_plan_to_expr(cond.persist_condition)
        return PersistExpr(
            expr=inner,
            window=cond.persist_window or 5,
            min_true=cond.persist_min_true or 3,
        )

    # Rolling comparison
    if cond.rolling_feature is not None:
        rolling_node = RollingExpr(
            feature=cond.rolling_feature,
            method=cond.rolling_method or "mean",
            window=cond.rolling_window or 5,
        )
        if cond.op and cond.threshold is not None:
            return ComparisonExpr(
                left=rolling_node,
                op=cond.op,
                threshold=cond.threshold,
            )
        return rolling_node

    # State variable comparison
    if cond.state_var is not None:
        left = StateVarExpr(name=cond.state_var)
        return ComparisonExpr(
            left=left,
            op=cond.op or ">=",
            threshold=cond.threshold or 0.0,
        )

    # Position attribute comparison
    if cond.position_attr is not None:
        left = PositionAttrExpr(name=cond.position_attr)
        return ComparisonExpr(
            left=left,
            op=cond.op or ">=",
            threshold=cond.threshold or 0.0,
        )

    # Simple feature comparison (default)
    if cond.feature is not None:
        return ComparisonExpr(
            feature=cond.feature,
            op=cond.op or ">",
            threshold=cond.threshold or 0.0,
        )

    logger.warning("ConditionPlan has no recognizable condition type, defaulting to const true")
    return ConstExpr(value=1.0)


def _lower_plan_entry(entry: Any) -> EntryPolicyV2:
    """Lower an EntryPlan to EntryPolicyV2."""
    trigger = _condition_plan_to_expr(entry.trigger)

    return EntryPolicyV2(
        name=entry.name,
        side=entry.side,
        trigger=trigger,
        strength=ConstExpr(value=entry.strength),
        constraints=EntryConstraints(
            cooldown_ticks=entry.cooldown_ticks,
            no_reentry_until_flat=entry.no_reentry_until_flat,
        ),
    )


def _lower_plan_exit_rule(rule: Any) -> ExitRuleV2:
    """Lower an ExitRulePlan to ExitRuleV2."""
    condition = _condition_plan_to_expr(rule.condition)

    if rule.action == "reduce_position":
        action = ExitActionV2(
            type="reduce_position",
            reduce_fraction=rule.reduce_fraction or 0.5,
        )
    else:
        action = ExitActionV2(type=rule.action or "close_all")

    return ExitRuleV2(
        name=rule.name,
        priority=rule.priority,
        condition=condition,
        action=action,
    )


def _lower_plan_risk(risk: Any) -> RiskPolicyV2:
    """Lower a RiskPlan to RiskPolicyV2."""
    degradation_rules = []
    for r in risk.degradation_rules:
        degradation_rules.append(
            RiskDegradationRuleV2(
                condition=_condition_plan_to_expr(r.condition),
                action=RiskDegradationActionV2(
                    type=r.action_type,
                    factor=r.factor,
                ),
            )
        )

    return RiskPolicyV2(
        max_position=risk.max_position,
        inventory_cap=risk.inventory_cap,
        position_sizing=PositionSizingV2(
            mode=risk.sizing_mode,
            base_size=risk.base_size,
            max_size=risk.max_size,
        ),
        degradation_rules=degradation_rules,
    )


def _lower_plan_execution(ep: Any) -> ExecutionPolicyV2:
    """Lower an ExecutionPlan to ExecutionPolicyV2."""
    dnt = None
    if ep.do_not_trade_when is not None:
        dnt = _condition_plan_to_expr(ep.do_not_trade_when)

    adaptation_rules = []
    for r in ep.adaptation_rules:
        adaptation_rules.append(
            ExecutionAdaptationRuleV2(
                condition=_condition_plan_to_expr(r.condition),
                override=ExecutionAdaptationOverrideV2(
                    placement_mode=r.placement_mode,
                    cancel_after_ticks=r.cancel_after_ticks,
                    max_reprices=r.max_reprices,
                ),
            )
        )

    return ExecutionPolicyV2(
        placement_mode=ep.placement_mode,
        cancel_after_ticks=ep.cancel_after_ticks,
        max_reprices=ep.max_reprices,
        do_not_trade_when=dnt,
        adaptation_rules=adaptation_rules,
    )


def _lower_plan_regime(regime: Any) -> RegimeV2:
    """Lower a RegimePlan to RegimeV2."""
    return RegimeV2(
        name=regime.name,
        priority=regime.priority,
        when=_condition_plan_to_expr(regime.when),
        entry_policy_refs=regime.entry_policy_refs,
        exit_policy_ref=regime.exit_policy_ref,
    )


def _lower_plan_state(sp: Any) -> StatePolicyV2:
    """Lower a StatePlan to StatePolicyV2.

    ``sp.vars`` is a ``list[StateVarPlan]`` (OpenAI-compatible).
    StrategySpecV2 expects ``dict[str, float]``.
    """
    guards = [
        StateGuardV2(
            name=g.name,
            condition=_condition_plan_to_expr(g.condition),
            effect=g.effect,
        )
        for g in sp.guards
    ]

    events: list[StateEventV2] = []
    for e in sp.events:
        updates = [
            StateUpdateV2(var=u.var, op=u.op, value=u.value)
            for u in e.updates
        ]
        events.append(StateEventV2(name=e.name, on=e.on, updates=updates))

    # Convert list[StateVarPlan] → dict[str, float]
    vars_dict: dict[str, float] = {}
    for sv in sp.vars:
        vars_dict[sv.name] = float(sv.initial_value)

    return StatePolicyV2(
        vars=vars_dict,
        guards=guards,
        events=events,
    )


_SHORT_HORIZON_THRESHOLD_TICKS = 30
_SHORT_HORIZON_STYLE_HINTS: frozenset[str] = frozenset({
    "momentum",
    "scalping",
    "execution_adaptive",
})


def _infer_plan_holding_horizon_ticks(plan: Any) -> int | None:
    inferred: int | None = None
    for xp in plan.exit_policies:
        for rule in xp.rules:
            cond = rule.condition
            if (
                cond.position_attr == "holding_ticks"
                and cond.op in {">=", ">"}
                and cond.threshold is not None
            ):
                threshold = int(cond.threshold)
                if inferred is None or threshold < inferred:
                    inferred = threshold
    return inferred


def _infer_short_horizon_from_plan(plan: Any, holding_horizon: int | None) -> bool:
    if holding_horizon is not None:
        return holding_horizon <= _SHORT_HORIZON_THRESHOLD_TICKS

    style_hint = str(getattr(plan, "strategy_style", "") or "").strip().lower()
    fast_entry = any(0 < ep.cooldown_ticks <= _SHORT_HORIZON_THRESHOLD_TICKS for ep in plan.entry_policies)
    return style_hint in _SHORT_HORIZON_STYLE_HINTS and fast_entry


def lower_plan_to_spec_v2(
    plan: Any,
    *,
    latency_ms: float = 1.0,
) -> StrategySpecV2:
    """Convert a StrategyPlan (OpenAI output) into a StrategySpecV2.

    This is the plan-path equivalent of lower_to_spec_v2 (template path).
    """
    preconditions = [
        PreconditionV2(
            name=pc.name,
            condition=_condition_plan_to_expr(pc.condition),
        )
        for pc in plan.preconditions
    ]

    entry_policies = [_lower_plan_entry(e) for e in plan.entry_policies]

    exit_policies = [
        ExitPolicyV2(
            name=xp.name,
            rules=[_lower_plan_exit_rule(r) for r in xp.rules],
        )
        for xp in plan.exit_policies
    ]

    risk_policy = _lower_plan_risk(plan.risk_policy)

    regimes = [_lower_plan_regime(r) for r in plan.regimes]

    execution_policy = None
    if plan.execution_policy is not None:
        execution_policy = _lower_plan_execution(plan.execution_policy)

    state_policy = None
    if plan.state_policy is not None:
        state_policy = _lower_plan_state(plan.state_policy)

    holding_horizon = _infer_plan_holding_horizon_ticks(plan)
    inferred_short_horizon = _infer_short_horizon_from_plan(plan, holding_horizon)
    execution_policy_missing_short_horizon = (execution_policy is None and inferred_short_horizon)

    if execution_policy_missing_short_horizon:
        logger.warning(
            "Plan '%s' appears short-horizon but omitted execution_policy; keeping None and marking metadata for review",
            plan.name,
        )

    metadata: dict[str, Any] = {
        "pipeline": "v2_openai_plan_lowering",
        "plan_name": plan.name,
        "plan_style": plan.strategy_style,
        "execution_policy_explicit": execution_policy is not None,
        "execution_policy_missing": execution_policy is None,
        "inferred_holding_horizon_ticks": holding_horizon,
        "inferred_short_horizon": inferred_short_horizon,
        "execution_policy_missing_short_horizon": execution_policy_missing_short_horizon,
    }

    return StrategySpecV2(
        name=plan.name,
        version="2.0",
        description=plan.description,
        spec_format="v2",
        preconditions=preconditions,
        entry_policies=entry_policies,
        exit_policies=exit_policies,
        risk_policy=risk_policy,
        regimes=regimes,
        execution_policy=execution_policy,
        state_policy=state_policy,
        metadata=metadata,
    )
