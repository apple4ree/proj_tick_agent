"""Parses and validates OpenAI responses against the plan schema."""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from strategy_block.strategy_review.review_common import POSITION_ATTR_ONLY
from ..schemas.plan_schema import StrategyPlan

logger = logging.getLogger(__name__)


class PlanParseError(ValueError):
    """Raised when the OpenAI response fails to parse into StrategyPlan."""

    def __init__(self, message: str, *, raw_response: Any = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


def parse_plan_response(response: Any) -> StrategyPlan:
    """Parse an OpenAI response into a validated StrategyPlan.

    Accepts:
    - A StrategyPlan instance (pass-through from structured output)
    - A dict (parsed JSON)
    - A JSON string
    """
    if isinstance(response, StrategyPlan):
        return response

    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError as e:
            raise PlanParseError(
                f"Response is not valid JSON: {e}",
                raw_response=response,
            ) from e

    if isinstance(response, dict):
        try:
            return StrategyPlan.model_validate(response)
        except ValidationError as e:
            raise PlanParseError(
                f"Response does not match StrategyPlan schema: {e}",
                raw_response=response,
            ) from e

    raise PlanParseError(
        f"Unexpected response type: {type(response).__name__}",
        raw_response=response,
    )


def validate_plan(plan: StrategyPlan) -> list[str]:
    """Run basic semantic validation on a parsed plan. Returns list of warnings."""
    warnings: list[str] = []

    if not plan.entry_policies:
        warnings.append("Plan has no entry policies")

    if not plan.exit_policies:
        warnings.append("Plan has no exit policies")

    for ep in plan.exit_policies:
        has_close_all = any(r.action == "close_all" for r in ep.rules)
        if not has_close_all:
            warnings.append(f"Exit policy '{ep.name}' has no close_all rule")

    if plan.regimes:
        entry_names = {e.name for e in plan.entry_policies}
        exit_names = {e.name for e in plan.exit_policies}
        for regime in plan.regimes:
            for ref in regime.entry_policy_refs:
                if ref not in entry_names:
                    warnings.append(
                        f"Regime '{regime.name}' references unknown entry policy '{ref}'"
                    )
            if regime.exit_policy_ref and regime.exit_policy_ref not in exit_names:
                warnings.append(
                    f"Regime '{regime.name}' references unknown exit policy '{regime.exit_policy_ref}'"
                )

    if plan.state_policy:
        defined_vars = {sv.name for sv in plan.state_policy.vars}
        for guard in plan.state_policy.guards:
            _check_condition_state_refs(guard.condition, defined_vars, warnings, f"guard '{guard.name}'")
        for event in plan.state_policy.events:
            for upd in event.updates:
                if upd.var not in defined_vars:
                    warnings.append(
                        f"State event '{event.name}' updates undefined var '{upd.var}'"
                    )

    return warnings


def check_position_attr_misuse(plan: StrategyPlan) -> list[str]:
    """Check that position_attr-only names are not used as feature.

    Returns a list of error messages. Any non-empty result means the plan
    is invalid and must be rejected — not silently fixed.
    """
    errors: list[str] = []

    def _check_cond(cond: Any, context: str) -> None:
        if cond is None:
            return
        if cond.feature and cond.feature in POSITION_ATTR_ONLY:
            errors.append(
                f"{context}: '{cond.feature}' is a position attribute and must use "
                f"position_attr, not feature (feature-based lookup returns 0.0 at runtime)"
            )
        if cond.cross_feature and cond.cross_feature in POSITION_ATTR_ONLY:
            errors.append(
                f"{context}: cross_feature '{cond.cross_feature}' is a position attribute "
                f"and cannot be used as a market feature"
            )
        if cond.rolling_feature and cond.rolling_feature in POSITION_ATTR_ONLY:
            errors.append(
                f"{context}: rolling_feature '{cond.rolling_feature}' is a position attribute "
                f"and cannot be used as a market feature"
            )
        if cond.children:
            for i, child in enumerate(cond.children):
                _check_cond(child, f"{context}.children[{i}]")
        if cond.persist_condition:
            _check_cond(cond.persist_condition, f"{context}.persist_condition")

    for pc in plan.preconditions:
        _check_cond(pc.condition, f"precondition '{pc.name}'")
    for ep in plan.entry_policies:
        _check_cond(ep.trigger, f"entry '{ep.name}'.trigger")
    for xp in plan.exit_policies:
        for rule in xp.rules:
            _check_cond(rule.condition, f"exit '{xp.name}'.rule '{rule.name}'")
    if plan.execution_policy:
        if plan.execution_policy.do_not_trade_when:
            _check_cond(plan.execution_policy.do_not_trade_when, "execution.do_not_trade_when")
        for ar in plan.execution_policy.adaptation_rules:
            _check_cond(ar.condition, "execution.adaptation_rule")
    for regime in plan.regimes:
        _check_cond(regime.when, f"regime '{regime.name}'.when")
    if plan.risk_policy:
        for dr in plan.risk_policy.degradation_rules:
            _check_cond(dr.condition, "risk.degradation_rule")
    if plan.state_policy:
        for guard in plan.state_policy.guards:
            _check_cond(guard.condition, f"state.guard '{guard.name}'")

    return errors


def _check_condition_state_refs(
    cond: Any,
    defined_vars: set[str],
    warnings: list[str],
    context: str,
) -> None:
    """Check that state_var references in conditions point to defined vars."""
    if cond is None:
        return
    if cond.state_var and cond.state_var not in defined_vars:
        warnings.append(
            f"{context} references undefined state_var '{cond.state_var}'"
        )
    if cond.children:
        for child in cond.children:
            _check_condition_state_refs(child, defined_vars, warnings, context)
    if cond.persist_condition:
        _check_condition_state_refs(cond.persist_condition, defined_vars, warnings, context)
