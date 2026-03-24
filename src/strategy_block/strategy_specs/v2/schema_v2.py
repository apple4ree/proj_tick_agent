"""StrategySpec v2 schema — hierarchical strategy IR.

Phase 1 supports:
- Preconditions (market filters that gate all entries)
- Entry policies with AST-based triggers and strength
- Exit policies with prioritized rules and actions
- Risk policy (position sizing, inventory cap)

Phase 2 adds:
- Regimes (market-condition-based policy routing)
- Execution policy (placement hints for downstream)
- AST nodes: lag, rolling, persist

Phase 3 (minimal) adds:
- state_policy (vars / guards / events)
- risk_policy.degradation_rules
- execution_policy.adaptation_rules
- AST nodes: state_var, position_attr
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ast_nodes import (
    ExprNode,
    ComparisonExpr,
    CrossExpr,
    FeatureExpr,
    StateVarExpr,
    PositionAttrExpr,
    LagExpr,
    RollingExpr,
    PersistExpr,
    VALID_COMPARISON_OPS,
    VALID_CROSS_DIRECTIONS,
    VALID_ROLLING_METHODS,
    VALID_NODE_TYPES,
    VALID_POSITION_ATTR_NAMES,
    expr_from_dict,
)


# ── Precondition ──────────────────────────────────────────────────────

@dataclass
class PreconditionV2:
    """Market-level filter that must pass before any entry is evaluated."""
    name: str
    condition: ExprNode

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "condition": self.condition.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PreconditionV2":
        return cls(name=d["name"], condition=expr_from_dict(d["condition"]))


# ── Entry policy ──────────────────────────────────────────────────────

@dataclass
class EntryConstraints:
    """Stateful constraints on entry."""
    cooldown_ticks: int = 0
    no_reentry_until_flat: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cooldown_ticks": self.cooldown_ticks,
            "no_reentry_until_flat": self.no_reentry_until_flat,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EntryConstraints":
        return cls(
            cooldown_ticks=d.get("cooldown_ticks", 0),
            no_reentry_until_flat=d.get("no_reentry_until_flat", False),
        )


@dataclass
class EntryPolicyV2:
    """A named entry policy with a trigger condition and signal strength."""
    name: str
    side: str  # "long" | "short"
    trigger: ExprNode
    strength: ExprNode  # evaluates to a float
    constraints: EntryConstraints = field(default_factory=EntryConstraints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "side": self.side,
            "trigger": self.trigger.to_dict(),
            "strength": self.strength.to_dict(),
            "constraints": self.constraints.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EntryPolicyV2":
        return cls(
            name=d["name"],
            side=d["side"],
            trigger=expr_from_dict(d["trigger"]),
            strength=expr_from_dict(d["strength"]),
            constraints=EntryConstraints.from_dict(d.get("constraints", {})),
        )


# ── Exit policy ───────────────────────────────────────────────────────

@dataclass
class ExitActionV2:
    """Action to take when an exit rule triggers."""
    type: str  # "close_all" | "reduce_position"
    reduce_fraction: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.type == "reduce_position":
            d["reduce_fraction"] = self.reduce_fraction
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExitActionV2":
        return cls(
            type=d["type"],
            reduce_fraction=d.get("reduce_fraction", 0.5),
        )


@dataclass
class ExitRuleV2:
    """A single exit rule with a condition, priority, and action."""
    name: str
    priority: int
    condition: ExprNode
    action: ExitActionV2

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "condition": self.condition.to_dict(),
            "action": self.action.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExitRuleV2":
        return cls(
            name=d["name"],
            priority=d["priority"],
            condition=expr_from_dict(d["condition"]),
            action=ExitActionV2.from_dict(d["action"]),
        )


@dataclass
class ExitPolicyV2:
    """Named exit policy containing ordered exit rules."""
    name: str
    rules: list[ExitRuleV2] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExitPolicyV2":
        return cls(
            name=d["name"],
            rules=[ExitRuleV2.from_dict(r) for r in d.get("rules", [])],
        )


# ── State policy (Phase 3 minimal) ───────────────────────────────────

VALID_STATE_GUARD_EFFECTS: frozenset[str] = frozenset({"block_entry"})
VALID_STATE_EVENT_ON: frozenset[str] = frozenset({
    "on_entry", "on_exit_profit", "on_exit_loss", "on_flatten",
})
VALID_STATE_UPDATE_OPS: frozenset[str] = frozenset({"set", "increment", "reset"})


@dataclass
class StateUpdateV2:
    """Single state variable update operation."""
    var: str
    op: str
    value: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"var": self.var, "op": self.op}
        if self.op in {"set", "increment"}:
            d["value"] = self.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StateUpdateV2":
        return cls(
            var=d["var"],
            op=d["op"],
            value=float(d.get("value", 0.0)),
        )


@dataclass
class StateEventV2:
    """State update bundle fired on a runtime event."""
    name: str
    on: str
    updates: list[StateUpdateV2] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "on": self.on,
            "updates": [u.to_dict() for u in self.updates],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StateEventV2":
        return cls(
            name=d["name"],
            on=d["on"],
            updates=[StateUpdateV2.from_dict(u) for u in d.get("updates", [])],
        )


@dataclass
class StateGuardV2:
    """Entry gate condition based on runtime state/feature context."""
    name: str
    condition: ExprNode
    effect: str = "block_entry"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "condition": self.condition.to_dict(),
            "effect": self.effect,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StateGuardV2":
        return cls(
            name=d["name"],
            condition=expr_from_dict(d["condition"]),
            effect=d.get("effect", "block_entry"),
        )


@dataclass
class StatePolicyV2:
    """Minimal runtime state model for declarative v2 strategies."""
    vars: dict[str, float] = field(default_factory=dict)
    guards: list[StateGuardV2] = field(default_factory=list)
    events: list[StateEventV2] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vars": self.vars,
            "guards": [g.to_dict() for g in self.guards],
            "events": [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StatePolicyV2":
        vars_in = d.get("vars", {})
        parsed_vars: dict[str, float] = {
            str(k): float(v) for k, v in vars_in.items()
        }
        return cls(
            vars=parsed_vars,
            guards=[StateGuardV2.from_dict(g) for g in d.get("guards", [])],
            events=[StateEventV2.from_dict(e) for e in d.get("events", [])],
        )


# ── Risk policy ───────────────────────────────────────────────────────

VALID_DEGRADATION_ACTION_TYPES: frozenset[str] = frozenset({
    "scale_max_position", "scale_strength", "block_new_entries",
})


@dataclass
class RiskDegradationActionV2:
    """Action applied when degradation rule condition matches."""
    type: str
    factor: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.type in {"scale_max_position", "scale_strength"}:
            d["factor"] = self.factor
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RiskDegradationActionV2":
        return cls(
            type=d["type"],
            factor=float(d.get("factor", 1.0)),
        )


@dataclass
class RiskDegradationRuleV2:
    """Condition-driven risk degradation rule."""
    condition: ExprNode
    action: RiskDegradationActionV2

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition.to_dict(),
            "action": self.action.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RiskDegradationRuleV2":
        return cls(
            condition=expr_from_dict(d["condition"]),
            action=RiskDegradationActionV2.from_dict(d["action"]),
        )


@dataclass
class PositionSizingV2:
    """Position sizing configuration."""
    mode: str = "fixed"  # "fixed" | "signal_proportional"
    base_size: int = 100
    max_size: int = 500

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "base_size": self.base_size,
            "max_size": self.max_size,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PositionSizingV2":
        return cls(
            mode=d.get("mode", "fixed"),
            base_size=d.get("base_size", 100),
            max_size=d.get("max_size", 500),
        )


@dataclass
class RiskPolicyV2:
    """Risk and position management policy."""
    max_position: int = 500
    inventory_cap: int = 1000
    position_sizing: PositionSizingV2 = field(default_factory=PositionSizingV2)
    degradation_rules: list[RiskDegradationRuleV2] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "max_position": self.max_position,
            "inventory_cap": self.inventory_cap,
            "position_sizing": self.position_sizing.to_dict(),
        }
        if self.degradation_rules:
            d["degradation_rules"] = [r.to_dict() for r in self.degradation_rules]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RiskPolicyV2":
        return cls(
            max_position=d.get("max_position", 500),
            inventory_cap=d.get("inventory_cap", 1000),
            position_sizing=PositionSizingV2.from_dict(d.get("position_sizing", {})),
            degradation_rules=[
                RiskDegradationRuleV2.from_dict(r)
                for r in d.get("degradation_rules", [])
            ],
        )


# ── Regime (Phase 2) ─────────────────────────────────────────────────

@dataclass
class RegimeV2:
    """Market regime that routes to specific entry/exit policies."""
    name: str
    priority: int
    when: ExprNode
    entry_policy_refs: list[str] = field(default_factory=list)
    exit_policy_ref: str = ""
    risk_override: RiskPolicyV2 | None = None
    execution_override: "ExecutionPolicyV2 | None" = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "priority": self.priority,
            "when": self.when.to_dict(),
            "entry_policy_refs": self.entry_policy_refs,
            "exit_policy_ref": self.exit_policy_ref,
        }
        if self.risk_override is not None:
            d["risk_override"] = self.risk_override.to_dict()
        if self.execution_override is not None:
            d["execution_override"] = self.execution_override.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegimeV2":
        risk_ov = None
        if "risk_override" in d:
            risk_ov = RiskPolicyV2.from_dict(d["risk_override"])
        exec_ov = None
        if "execution_override" in d:
            exec_ov = ExecutionPolicyV2.from_dict(d["execution_override"])
        return cls(
            name=d["name"],
            priority=d["priority"],
            when=expr_from_dict(d["when"]),
            entry_policy_refs=d.get("entry_policy_refs", []),
            exit_policy_ref=d.get("exit_policy_ref", ""),
            risk_override=risk_ov,
            execution_override=exec_ov,
        )


# ── Execution policy (Phase 2/3, hint level) ─────────────────────────

VALID_PLACEMENT_MODES: frozenset[str] = frozenset({
    "passive_join", "passive_only", "aggressive_cross", "adaptive",
})


@dataclass
class ExecutionAdaptationOverrideV2:
    """Hint-level execution override applied when a condition matches."""
    placement_mode: str | None = None
    cancel_after_ticks: int | None = None
    max_reprices: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.placement_mode is not None:
            d["placement_mode"] = self.placement_mode
        if self.cancel_after_ticks is not None:
            d["cancel_after_ticks"] = self.cancel_after_ticks
        if self.max_reprices is not None:
            d["max_reprices"] = self.max_reprices
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecutionAdaptationOverrideV2":
        return cls(
            placement_mode=d.get("placement_mode"),
            cancel_after_ticks=d.get("cancel_after_ticks"),
            max_reprices=d.get("max_reprices"),
        )


@dataclass
class ExecutionAdaptationRuleV2:
    """Condition-driven execution hint override."""
    condition: ExprNode
    override: ExecutionAdaptationOverrideV2

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition.to_dict(),
            "override": self.override.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecutionAdaptationRuleV2":
        return cls(
            condition=expr_from_dict(d["condition"]),
            override=ExecutionAdaptationOverrideV2.from_dict(d.get("override", {})),
        )


@dataclass
class ExecutionPolicyV2:
    """Execution hints passed downstream via signal tags."""
    placement_mode: str = "passive_join"
    cancel_after_ticks: int = 0
    max_reprices: int = 0
    do_not_trade_when: ExprNode | None = None
    adaptation_rules: list[ExecutionAdaptationRuleV2] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "placement_mode": self.placement_mode,
            "cancel_after_ticks": self.cancel_after_ticks,
            "max_reprices": self.max_reprices,
        }
        if self.do_not_trade_when is not None:
            d["do_not_trade_when"] = self.do_not_trade_when.to_dict()
        if self.adaptation_rules:
            d["adaptation_rules"] = [r.to_dict() for r in self.adaptation_rules]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecutionPolicyV2":
        dnt = None
        if "do_not_trade_when" in d:
            dnt = expr_from_dict(d["do_not_trade_when"])
        return cls(
            placement_mode=d.get("placement_mode", "passive_join"),
            cancel_after_ticks=d.get("cancel_after_ticks", 0),
            max_reprices=d.get("max_reprices", 0),
            do_not_trade_when=dnt,
            adaptation_rules=[
                ExecutionAdaptationRuleV2.from_dict(r)
                for r in d.get("adaptation_rules", [])
            ],
        )


# ── StrategySpec V2 ───────────────────────────────────────────────────

VALID_SIDES: frozenset[str] = frozenset({"long", "short"})
VALID_EXIT_ACTION_TYPES: frozenset[str] = frozenset({"close_all", "reduce_position"})
VALID_SIZING_MODES: frozenset[str] = frozenset({"fixed", "signal_proportional"})


@dataclass
class StrategySpecV2:
    """Hierarchical strategy specification (v2 IR)."""
    name: str
    version: str = "2.0"
    description: str = ""
    spec_format: str = "v2"
    preconditions: list[PreconditionV2] = field(default_factory=list)
    entry_policies: list[EntryPolicyV2] = field(default_factory=list)
    exit_policies: list[ExitPolicyV2] = field(default_factory=list)
    risk_policy: RiskPolicyV2 = field(default_factory=RiskPolicyV2)
    regimes: list[RegimeV2] = field(default_factory=list)
    execution_policy: ExecutionPolicyV2 | None = None
    state_policy: StatePolicyV2 | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "spec_format": self.spec_format,
            "preconditions": [p.to_dict() for p in self.preconditions],
            "entry_policies": [e.to_dict() for e in self.entry_policies],
            "exit_policies": [e.to_dict() for e in self.exit_policies],
            "risk_policy": self.risk_policy.to_dict(),
            "regimes": [r.to_dict() for r in self.regimes],
            "metadata": self.metadata,
        }
        if self.execution_policy is not None:
            d["execution_policy"] = self.execution_policy.to_dict()
        if self.state_policy is not None:
            d["state_policy"] = self.state_policy.to_dict()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategySpecV2":
        ep = None
        if "execution_policy" in d and d["execution_policy"] is not None:
            ep = ExecutionPolicyV2.from_dict(d["execution_policy"])
        sp = None
        if "state_policy" in d and d["state_policy"] is not None:
            sp = StatePolicyV2.from_dict(d["state_policy"])
        return cls(
            name=d["name"],
            version=d.get("version", "2.0"),
            description=d.get("description", ""),
            spec_format=d.get("spec_format", "v2"),
            preconditions=[PreconditionV2.from_dict(p) for p in d.get("preconditions", [])],
            entry_policies=[EntryPolicyV2.from_dict(e) for e in d.get("entry_policies", [])],
            exit_policies=[ExitPolicyV2.from_dict(e) for e in d.get("exit_policies", [])],
            risk_policy=RiskPolicyV2.from_dict(d.get("risk_policy", {})),
            regimes=[RegimeV2.from_dict(r) for r in d.get("regimes", [])],
            execution_policy=ep,
            state_policy=sp,
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> "StrategySpecV2":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> "StrategySpecV2":
        path = Path(path)
        return cls.from_json(path.read_text(encoding="utf-8"))

    # ── Validation ────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return a list of validation errors. Empty list means valid."""
        errors: list[str] = []
        if not self.name:
            errors.append("Strategy name is required")
        if self.spec_format != "v2":
            errors.append(f"spec_format must be 'v2', got '{self.spec_format}'")
        if not self.entry_policies:
            errors.append("At least one entry policy is required")
        if not self.exit_policies:
            errors.append("At least one exit policy is required")

        state_vars = set(self.state_policy.vars.keys()) if self.state_policy else set()

        # Validate preconditions
        for i, pc in enumerate(self.preconditions):
            prefix = f"preconditions[{i}]"
            if not pc.name:
                errors.append(f"{prefix}: name is required")
            errors.extend(self._validate_expr(pc.condition, f"{prefix}.condition", state_vars))

        # Validate entry policies
        for i, ep in enumerate(self.entry_policies):
            prefix = f"entry_policies[{i}]"
            if not ep.name:
                errors.append(f"{prefix}: name is required")
            if ep.side not in VALID_SIDES:
                errors.append(f"{prefix}: side must be 'long' or 'short', got '{ep.side}'")
            errors.extend(self._validate_expr(ep.trigger, f"{prefix}.trigger", state_vars))
            errors.extend(self._validate_expr(ep.strength, f"{prefix}.strength", state_vars))
            if ep.constraints.cooldown_ticks < 0:
                errors.append(f"{prefix}: cooldown_ticks must be >= 0")

        # Validate exit policies
        for i, xp in enumerate(self.exit_policies):
            prefix = f"exit_policies[{i}]"
            if not xp.name:
                errors.append(f"{prefix}: name is required")
            if not xp.rules:
                errors.append(f"{prefix}: at least one exit rule is required")
            for j, rule in enumerate(xp.rules):
                rprefix = f"{prefix}.rules[{j}]"
                if not rule.name:
                    errors.append(f"{rprefix}: name is required")
                errors.extend(self._validate_expr(rule.condition, f"{rprefix}.condition", state_vars))
                if rule.action.type not in VALID_EXIT_ACTION_TYPES:
                    errors.append(
                        f"{rprefix}.action: type must be one of "
                        f"{sorted(VALID_EXIT_ACTION_TYPES)}, got '{rule.action.type}'"
                    )
                if rule.action.type == "reduce_position":
                    if not (0.0 < rule.action.reduce_fraction <= 1.0):
                        errors.append(
                            f"{rprefix}.action.reduce_fraction must be in (0, 1], "
                            f"got {rule.action.reduce_fraction}"
                        )

        # Validate state policy (Phase 3)
        if self.state_policy is not None:
            sp = self.state_policy
            for var_name, var_value in sp.vars.items():
                if not var_name:
                    errors.append("state_policy.vars: variable name must be non-empty")
                if not isinstance(var_value, (int, float)):
                    errors.append(
                        f"state_policy.vars['{var_name}'] must be numeric, got {type(var_value).__name__}"
                    )

            for i, guard in enumerate(sp.guards):
                prefix = f"state_policy.guards[{i}]"
                if not guard.name:
                    errors.append(f"{prefix}: name is required")
                if guard.effect not in VALID_STATE_GUARD_EFFECTS:
                    errors.append(
                        f"{prefix}.effect must be one of {sorted(VALID_STATE_GUARD_EFFECTS)}, "
                        f"got '{guard.effect}'"
                    )
                errors.extend(self._validate_expr(guard.condition, f"{prefix}.condition", state_vars))

            for i, event in enumerate(sp.events):
                prefix = f"state_policy.events[{i}]"
                if not event.name:
                    errors.append(f"{prefix}: name is required")
                if event.on not in VALID_STATE_EVENT_ON:
                    errors.append(
                        f"{prefix}.on must be one of {sorted(VALID_STATE_EVENT_ON)}, "
                        f"got '{event.on}'"
                    )
                for j, upd in enumerate(event.updates):
                    up = f"{prefix}.updates[{j}]"
                    if upd.op not in VALID_STATE_UPDATE_OPS:
                        errors.append(
                            f"{up}.op must be one of {sorted(VALID_STATE_UPDATE_OPS)}, "
                            f"got '{upd.op}'"
                        )
                    if upd.var not in state_vars:
                        errors.append(
                            f"{up}.var '{upd.var}' is not declared in state_policy.vars"
                        )
                    if upd.op in {"set", "increment"} and not isinstance(upd.value, (int, float)):
                        errors.append(f"{up}.value must be numeric for op '{upd.op}'")

        # Validate risk policy
        errors.extend(self._validate_risk_policy(self.risk_policy, "risk_policy", state_vars))

        # Validate regimes (Phase 2)
        entry_names = {ep.name for ep in self.entry_policies}
        exit_names = {xp.name for xp in self.exit_policies}
        for i, regime in enumerate(self.regimes):
            prefix = f"regimes[{i}]"
            if not regime.name:
                errors.append(f"{prefix}: name is required")
            errors.extend(self._validate_expr(regime.when, f"{prefix}.when", state_vars))
            for ref in regime.entry_policy_refs:
                if ref not in entry_names:
                    errors.append(
                        f"{prefix}: entry_policy_ref '{ref}' not found in entry_policies"
                    )
            if regime.exit_policy_ref and regime.exit_policy_ref not in exit_names:
                errors.append(
                    f"{prefix}: exit_policy_ref '{regime.exit_policy_ref}' "
                    f"not found in exit_policies"
                )
            if regime.risk_override is not None:
                errors.extend(self._validate_risk_policy(
                    regime.risk_override, f"{prefix}.risk_override", state_vars
                ))
            if regime.execution_override is not None:
                errors.extend(self._validate_execution_policy(
                    regime.execution_override, f"{prefix}.execution_override", state_vars
                ))

        # Validate execution policy (Phase 2/3)
        if self.execution_policy is not None:
            errors.extend(self._validate_execution_policy(
                self.execution_policy, "execution_policy", state_vars
            ))

        return errors

    def _validate_risk_policy(
        self,
        rp: RiskPolicyV2,
        path: str,
        state_vars: set[str],
    ) -> list[str]:
        errors: list[str] = []
        if rp.max_position <= 0:
            errors.append(f"{path}.max_position must be > 0, got {rp.max_position}")
        if rp.inventory_cap <= 0:
            errors.append(f"{path}.inventory_cap must be > 0, got {rp.inventory_cap}")
        if rp.position_sizing.mode not in VALID_SIZING_MODES:
            errors.append(
                f"{path}.position_sizing.mode must be one of "
                f"{sorted(VALID_SIZING_MODES)}, got '{rp.position_sizing.mode}'"
            )
        if rp.position_sizing.base_size <= 0:
            errors.append(f"{path}.position_sizing.base_size must be > 0")
        if rp.position_sizing.max_size <= 0:
            errors.append(f"{path}.position_sizing.max_size must be > 0")

        for i, rule in enumerate(rp.degradation_rules):
            rprefix = f"{path}.degradation_rules[{i}]"
            errors.extend(self._validate_expr(rule.condition, f"{rprefix}.condition", state_vars))
            if rule.action.type not in VALID_DEGRADATION_ACTION_TYPES:
                errors.append(
                    f"{rprefix}.action.type must be one of "
                    f"{sorted(VALID_DEGRADATION_ACTION_TYPES)}, got '{rule.action.type}'"
                )
            if rule.action.type in {"scale_max_position", "scale_strength"}:
                if rule.action.factor < 0:
                    errors.append(f"{rprefix}.action.factor must be >= 0")

        return errors

    def _validate_execution_policy(
        self,
        xp: ExecutionPolicyV2,
        path: str,
        state_vars: set[str],
    ) -> list[str]:
        errors: list[str] = []
        if xp.placement_mode not in VALID_PLACEMENT_MODES:
            errors.append(
                f"{path}: placement_mode must be one of "
                f"{sorted(VALID_PLACEMENT_MODES)}, got '{xp.placement_mode}'"
            )
        if xp.cancel_after_ticks < 0:
            errors.append(f"{path}: cancel_after_ticks must be >= 0")
        if xp.max_reprices < 0:
            errors.append(f"{path}: max_reprices must be >= 0")
        if xp.do_not_trade_when is not None:
            errors.extend(self._validate_expr(
                xp.do_not_trade_when, f"{path}.do_not_trade_when", state_vars
            ))

        for i, rule in enumerate(xp.adaptation_rules):
            rprefix = f"{path}.adaptation_rules[{i}]"
            errors.extend(self._validate_expr(rule.condition, f"{rprefix}.condition", state_vars))
            ov = rule.override
            if (ov.placement_mode is None
                    and ov.cancel_after_ticks is None
                    and ov.max_reprices is None):
                errors.append(f"{rprefix}.override must set at least one field")
            if ov.placement_mode is not None and ov.placement_mode not in VALID_PLACEMENT_MODES:
                errors.append(
                    f"{rprefix}.override.placement_mode must be one of "
                    f"{sorted(VALID_PLACEMENT_MODES)}, got '{ov.placement_mode}'"
                )
            if ov.cancel_after_ticks is not None and ov.cancel_after_ticks < 0:
                errors.append(f"{rprefix}.override.cancel_after_ticks must be >= 0")
            if ov.max_reprices is not None and ov.max_reprices < 0:
                errors.append(f"{rprefix}.override.max_reprices must be >= 0")

        return errors

    def _validate_expr(
        self,
        node: ExprNode,
        path: str,
        state_vars: set[str] | None = None,
    ) -> list[str]:
        """Recursively validate an expression node."""
        errors: list[str] = []
        state_vars = state_vars or set()

        if node.type not in VALID_NODE_TYPES:
            errors.append(f"{path}: unknown node type '{node.type}'")
            return errors

        if isinstance(node, ComparisonExpr):
            if node.op not in VALID_COMPARISON_OPS:
                errors.append(f"{path}: invalid comparison op '{node.op}'")
            if node.left is None:
                if not node.feature:
                    errors.append(f"{path}: feature name is required")
            else:
                errors.extend(self._validate_expr(node.left, f"{path}.left", state_vars))
        elif isinstance(node, CrossExpr):
            if node.direction not in VALID_CROSS_DIRECTIONS:
                errors.append(f"{path}: invalid cross direction '{node.direction}'")
            if not node.feature:
                errors.append(f"{path}: feature name is required")
        elif isinstance(node, FeatureExpr):
            if not node.name:
                errors.append(f"{path}: feature name is required")
        elif isinstance(node, StateVarExpr):
            if not node.name:
                errors.append(f"{path}: state var name is required")
            elif node.name not in state_vars:
                errors.append(
                    f"{path}: state_var '{node.name}' is not declared in state_policy.vars"
                )
        elif isinstance(node, PositionAttrExpr):
            if not node.name:
                errors.append(f"{path}: position_attr name is required")
            elif node.name not in VALID_POSITION_ATTR_NAMES:
                errors.append(
                    f"{path}: position_attr name must be one of "
                    f"{sorted(VALID_POSITION_ATTR_NAMES)}, got '{node.name}'"
                )
        elif isinstance(node, LagExpr):
            if not node.feature:
                errors.append(f"{path}: feature name is required")
            if node.steps < 1:
                errors.append(f"{path}: lag steps must be >= 1, got {node.steps}")
        elif isinstance(node, RollingExpr):
            if not node.feature:
                errors.append(f"{path}: feature name is required")
            if node.window < 2:
                errors.append(f"{path}: rolling window must be >= 2, got {node.window}")
            if node.method not in VALID_ROLLING_METHODS:
                errors.append(
                    f"{path}: rolling method must be one of "
                    f"{sorted(VALID_ROLLING_METHODS)}, got '{node.method}'"
                )
        elif isinstance(node, PersistExpr):
            if node.window < 1:
                errors.append(f"{path}: persist window must be >= 1, got {node.window}")
            if node.min_true < 1:
                errors.append(f"{path}: persist min_true must be >= 1, got {node.min_true}")
            if node.min_true > node.window:
                errors.append(
                    f"{path}: persist min_true ({node.min_true}) must be <= "
                    f"window ({node.window})"
                )
            errors.extend(self._validate_expr(node.expr, f"{path}.expr", state_vars))

        if hasattr(node, "children"):
            for i, child in enumerate(node.children):
                errors.extend(self._validate_expr(child, f"{path}.children[{i}]", state_vars))
        if hasattr(node, "child"):
            errors.extend(self._validate_expr(node.child, f"{path}.child", state_vars))

        return errors

    def collect_all_features(self) -> set[str]:
        """Collect all feature names referenced across the entire spec."""
        features: set[str] = set()
        for pc in self.preconditions:
            features |= pc.condition.collect_features()
        for ep in self.entry_policies:
            features |= ep.trigger.collect_features()
            features |= ep.strength.collect_features()
        for xp in self.exit_policies:
            for rule in xp.rules:
                features |= rule.condition.collect_features()
        for regime in self.regimes:
            features |= regime.when.collect_features()
            if regime.risk_override is not None:
                for rr in regime.risk_override.degradation_rules:
                    features |= rr.condition.collect_features()
            if regime.execution_override is not None:
                if regime.execution_override.do_not_trade_when is not None:
                    features |= regime.execution_override.do_not_trade_when.collect_features()
                for ar in regime.execution_override.adaptation_rules:
                    features |= ar.condition.collect_features()
        if self.execution_policy and self.execution_policy.do_not_trade_when:
            features |= self.execution_policy.do_not_trade_when.collect_features()
        if self.execution_policy:
            for ar in self.execution_policy.adaptation_rules:
                features |= ar.condition.collect_features()
        for rr in self.risk_policy.degradation_rules:
            features |= rr.condition.collect_features()
        if self.state_policy is not None:
            for guard in self.state_policy.guards:
                features |= guard.condition.collect_features()
        return features
