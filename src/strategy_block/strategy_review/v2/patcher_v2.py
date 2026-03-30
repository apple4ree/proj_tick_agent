"""Deterministic patcher for constrained RepairPlan operations."""
from __future__ import annotations

import copy
from typing import Any, Callable

from strategy_block.strategy_specs.v2.ast_nodes import AllExpr, AnyExpr, ComparisonExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    ExecutionPolicyV2,
    ExitActionV2,
    ExitRuleV2,
    StrategySpecV2,
)

from .contracts import RepairOperation, RepairPlan


class StrategyRepairPatcherV2:
    """Apply constrained repair operations in a deterministic way."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[StrategySpecV2, RepairOperation], None]] = {
            "set_cancel_after_ticks": self._set_cancel_after_ticks,
            "set_max_reprices": self._set_max_reprices,
            "set_placement_mode": self._set_placement_mode,
            "set_base_size": self._set_base_size,
            "set_max_size": self._set_max_size,
            "add_stop_loss_exit": self._add_stop_loss_exit,
            "add_time_exit": self._add_time_exit,
            "tighten_inventory_cap": self._tighten_inventory_cap,
            "set_holding_ticks": self._set_holding_ticks,
            "simplify_entry_trigger": self._simplify_entry_trigger,
        }

    def apply(self, spec: StrategySpecV2, plan: RepairPlan) -> StrategySpecV2:
        patched = copy.deepcopy(spec)
        for op in plan.operations:
            handler = self._handlers.get(op.op)
            if handler is None:
                raise ValueError(f"Unsupported repair operation: {op.op}")
            handler(patched, op)

        errors = patched.validate()
        if errors:
            raise ValueError(
                "Patched strategy failed schema validation: " + "; ".join(errors)
            )
        return patched

    def _ensure_execution_policy(self, spec: StrategySpecV2) -> ExecutionPolicyV2:
        if spec.execution_policy is None:
            spec.execution_policy = ExecutionPolicyV2()
        return spec.execution_policy

    def _coerce_int(self, value: Any, default: int) -> int:
        if value is None:
            return default
        if isinstance(value, dict):
            if "value" in value:
                return int(value["value"])
            if "ticks" in value:
                return int(value["ticks"])
        return int(value)

    def _coerce_float(self, value: Any, default: float) -> float:
        if value is None:
            return default
        if isinstance(value, dict):
            if "value" in value:
                return float(value["value"])
            if "threshold_bps" in value:
                return float(value["threshold_bps"])
        return float(value)

    def _set_cancel_after_ticks(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        xp = self._ensure_execution_policy(spec)
        xp.cancel_after_ticks = max(0, self._coerce_int(op.value, xp.cancel_after_ticks))

    def _set_max_reprices(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        xp = self._ensure_execution_policy(spec)
        xp.max_reprices = max(0, self._coerce_int(op.value, xp.max_reprices))

    def _set_placement_mode(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        xp = self._ensure_execution_policy(spec)
        if op.value is None:
            return
        xp.placement_mode = str(op.value)

    def _set_base_size(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        current = spec.risk_policy.position_sizing.base_size
        spec.risk_policy.position_sizing.base_size = max(1, self._coerce_int(op.value, current))

    def _set_max_size(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        current = spec.risk_policy.position_sizing.max_size
        spec.risk_policy.position_sizing.max_size = max(1, self._coerce_int(op.value, current))

    def _pick_exit_policy_index(self, spec: StrategySpecV2, target: str) -> int:
        if not spec.exit_policies:
            raise ValueError("Strategy has no exit policies to patch")
        if target and target not in {"global", "primary_exit_policy"}:
            for idx, policy in enumerate(spec.exit_policies):
                if policy.name == target:
                    return idx
        return 0

    def _is_stop_loss_rule(self, rule: ExitRuleV2) -> bool:
        cond = rule.condition
        return (
            rule.action.type == "close_all"
            and isinstance(cond, ComparisonExpr)
            and cond.left is not None
            and isinstance(cond.left, PositionAttrExpr)
            and cond.left.name == "unrealized_pnl_bps"
            and cond.op in {"<=", "<"}
        )

    def _is_time_exit_rule(self, rule: ExitRuleV2) -> bool:
        cond = rule.condition
        return (
            rule.action.type == "close_all"
            and isinstance(cond, ComparisonExpr)
            and cond.left is not None
            and isinstance(cond.left, PositionAttrExpr)
            and cond.left.name == "holding_ticks"
            and cond.op in {">=", ">"}
        )

    def _next_priority(self, spec: StrategySpecV2) -> int:
        priorities = [r.priority for p in spec.exit_policies for r in p.rules]
        return (max(priorities) + 1) if priorities else 1

    def _add_stop_loss_exit(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        idx = self._pick_exit_policy_index(spec, op.target)
        policy = spec.exit_policies[idx]
        if any(self._is_stop_loss_rule(rule) for rule in policy.rules):
            return
        threshold = self._coerce_float(op.value, -25.0)
        rule = ExitRuleV2(
            name="auto_stop_loss_exit",
            priority=self._next_priority(spec),
            condition=ComparisonExpr(
                left=PositionAttrExpr("unrealized_pnl_bps"),
                op="<=",
                threshold=threshold,
            ),
            action=ExitActionV2(type="close_all"),
        )
        policy.rules.append(rule)

    def _add_time_exit(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        idx = self._pick_exit_policy_index(spec, op.target)
        policy = spec.exit_policies[idx]
        if any(self._is_time_exit_rule(rule) for rule in policy.rules):
            return
        ticks_default = 120
        if isinstance(op.value, dict) and "holding_ticks" in op.value:
            ticks = int(op.value["holding_ticks"])
        else:
            ticks = self._coerce_int(op.value, ticks_default)
        rule = ExitRuleV2(
            name="auto_time_exit",
            priority=self._next_priority(spec),
            condition=ComparisonExpr(
                left=PositionAttrExpr("holding_ticks"),
                op=">=",
                threshold=float(max(1, ticks)),
            ),
            action=ExitActionV2(type="close_all"),
        )
        policy.rules.append(rule)

    def _tighten_inventory_cap(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        current = int(spec.risk_policy.inventory_cap)
        max_position = int(spec.risk_policy.max_position)
        target = current
        if isinstance(op.value, dict):
            if "cap" in op.value:
                target = int(op.value["cap"])
            elif "factor" in op.value:
                target = int(round(current * float(op.value["factor"])))
            elif "value" in op.value:
                target = int(op.value["value"])
        elif op.value is not None:
            numeric = float(op.value)
            if numeric <= 1.0:
                target = int(round(current * numeric))
            else:
                target = int(round(numeric))
        spec.risk_policy.inventory_cap = max(max_position, target)

    def _set_holding_ticks(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        new_ticks = float(max(1, self._coerce_int(op.value, 120)))
        updated = False
        for policy in spec.exit_policies:
            for rule in policy.rules:
                cond = rule.condition
                if (
                    isinstance(cond, ComparisonExpr)
                    and cond.left is not None
                    and isinstance(cond.left, PositionAttrExpr)
                    and cond.left.name == "holding_ticks"
                ):
                    cond.threshold = new_ticks
                    cond.op = ">="
                    updated = True
        if not updated:
            self._add_time_exit(
                spec,
                RepairOperation(
                    op="add_time_exit",
                    target=op.target,
                    value={"holding_ticks": int(new_ticks)},
                    reason="created from set_holding_ticks",
                ),
            )

    @staticmethod
    def execution_policy_summary(spec: StrategySpecV2) -> dict[str, object]:
        """Compute a summary of the current execution policy for diagnostics.

        Returns dict with:
        - placement_mode
        - cancel_after_ticks
        - max_reprices
        - repricing_budget (max_reprices value, 0 if no policy)
        - has_time_exit
        - has_stop_loss_exit
        - inferred_holding_horizon (smallest holding_ticks threshold or None)
        """
        xp = spec.execution_policy
        placement = xp.placement_mode if xp else "none"
        cancel_ticks = xp.cancel_after_ticks if xp else 0
        max_repr = xp.max_reprices if xp else 0

        has_time = False
        has_stop = False
        min_horizon: int | None = None

        for ep in spec.exit_policies:
            for rule in ep.rules:
                cond = rule.condition
                if rule.action.type != "close_all":
                    continue
                if (
                    isinstance(cond, ComparisonExpr)
                    and isinstance(cond.left, PositionAttrExpr)
                ):
                    if cond.left.name == "holding_ticks":
                        has_time = True
                        val = int(cond.threshold)
                        if min_horizon is None or val < min_horizon:
                            min_horizon = val
                    elif cond.left.name == "unrealized_pnl_bps":
                        has_stop = True

        return {
            "placement_mode": placement,
            "cancel_after_ticks": cancel_ticks,
            "max_reprices": max_repr,
            "repricing_budget": max_repr,
            "has_time_exit": has_time,
            "has_stop_loss_exit": has_stop,
            "inferred_holding_horizon": min_horizon,
        }

    def _simplify_entry_trigger(self, spec: StrategySpecV2, op: RepairOperation) -> None:
        if not spec.entry_policies:
            return
        target_idx = 0
        if op.target and op.target not in {"global", "entry_policy"}:
            for idx, entry in enumerate(spec.entry_policies):
                if entry.name == op.target:
                    target_idx = idx
                    break
        entry = spec.entry_policies[target_idx]
        trigger = entry.trigger
        if isinstance(trigger, AllExpr) and trigger.children:
            entry.trigger = trigger.children[0]
        elif isinstance(trigger, AnyExpr) and trigger.children:
            entry.trigger = trigger.children[0]
