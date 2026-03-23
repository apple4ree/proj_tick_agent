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
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .ast_nodes import (
    ExprNode,
    ConstExpr,
    ComparisonExpr,
    CrossExpr,
    FeatureExpr,
    LagExpr,
    RollingExpr,
    PersistExpr,
    VALID_COMPARISON_OPS,
    VALID_CROSS_DIRECTIONS,
    VALID_ROLLING_METHODS,
    VALID_NODE_TYPES,
    expr_from_dict,
)
from strategy_block.strategy_review.reviewer import KNOWN_FEATURES


# ── Precondition ──────────────────────────────────────────────────────

@dataclass
class PreconditionV2:
    """Market-level filter that must pass before any entry is evaluated."""
    name: str
    condition: ExprNode

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "condition": self.condition.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PreconditionV2:
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
    def from_dict(cls, d: dict[str, Any]) -> EntryConstraints:
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
    strength: ExprNode  # evaluates to a float — typically ConstExpr or FeatureExpr
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
    def from_dict(cls, d: dict[str, Any]) -> EntryPolicyV2:
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
    reduce_fraction: float = 0.5  # used only when type == "reduce_position"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.type == "reduce_position":
            d["reduce_fraction"] = self.reduce_fraction
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExitActionV2:
        return cls(
            type=d["type"],
            reduce_fraction=d.get("reduce_fraction", 0.5),
        )


@dataclass
class ExitRuleV2:
    """A single exit rule with a condition, priority, and action."""
    name: str
    priority: int  # lower number = higher priority
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
    def from_dict(cls, d: dict[str, Any]) -> ExitRuleV2:
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
    def from_dict(cls, d: dict[str, Any]) -> ExitPolicyV2:
        return cls(
            name=d["name"],
            rules=[ExitRuleV2.from_dict(r) for r in d.get("rules", [])],
        )


# ── Risk policy ───────────────────────────────────────────────────────

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
    def from_dict(cls, d: dict[str, Any]) -> PositionSizingV2:
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_position": self.max_position,
            "inventory_cap": self.inventory_cap,
            "position_sizing": self.position_sizing.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RiskPolicyV2:
        return cls(
            max_position=d.get("max_position", 500),
            inventory_cap=d.get("inventory_cap", 1000),
            position_sizing=PositionSizingV2.from_dict(d.get("position_sizing", {})),
        )


# ── Regime (Phase 2) ─────────────────────────────────────────────────

@dataclass
class RegimeV2:
    """Market regime that routes to specific entry/exit policies.

    When regimes are defined, the compiler selects the highest-priority
    matching regime and evaluates only the referenced policies.
    """
    name: str
    priority: int  # lower = higher priority
    when: ExprNode  # boolean condition for regime activation
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
    def from_dict(cls, d: dict[str, Any]) -> RegimeV2:
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


# ── Execution policy (Phase 2, hint level) ───────────────────────────

VALID_PLACEMENT_MODES: frozenset[str] = frozenset({
    "passive_join", "passive_only", "aggressive_cross", "adaptive",
})


@dataclass
class ExecutionPolicyV2:
    """Execution hints passed downstream via signal tags.

    This is a declarative intent layer — it does NOT directly control
    the execution engine, but tells downstream what the strategy prefers.
    """
    placement_mode: str = "passive_join"
    cancel_after_ticks: int = 0  # 0 = no auto-cancel
    max_reprices: int = 0  # 0 = no limit
    do_not_trade_when: ExprNode | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "placement_mode": self.placement_mode,
            "cancel_after_ticks": self.cancel_after_ticks,
            "max_reprices": self.max_reprices,
        }
        if self.do_not_trade_when is not None:
            d["do_not_trade_when"] = self.do_not_trade_when.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionPolicyV2:
        dnt = None
        if "do_not_trade_when" in d:
            dnt = expr_from_dict(d["do_not_trade_when"])
        return cls(
            placement_mode=d.get("placement_mode", "passive_join"),
            cancel_after_ticks=d.get("cancel_after_ticks", 0),
            max_reprices=d.get("max_reprices", 0),
            do_not_trade_when=dnt,
        )


# ── StrategySpec V2 ───────────────────────────────────────────────────

VALID_SIDES: frozenset[str] = frozenset({"long", "short"})
VALID_EXIT_ACTION_TYPES: frozenset[str] = frozenset({"close_all", "reduce_position"})
VALID_SIZING_MODES: frozenset[str] = frozenset({"fixed", "signal_proportional"})


@dataclass
class StrategySpecV2:
    """Hierarchical strategy specification (v2 IR).

    This is the v2 counterpart of StrategySpec (v1). It replaces flat
    rule lists with structured policies and an expression AST, enabling
    richer strategy logic while remaining fully declarative and
    JSON-serializable.
    """
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
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StrategySpecV2:
        ep = None
        if "execution_policy" in d and d["execution_policy"] is not None:
            ep = ExecutionPolicyV2.from_dict(d["execution_policy"])
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
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> StrategySpecV2:
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> StrategySpecV2:
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

        # Validate entry policies
        for i, ep in enumerate(self.entry_policies):
            prefix = f"entry_policies[{i}]"
            if not ep.name:
                errors.append(f"{prefix}: name is required")
            if ep.side not in VALID_SIDES:
                errors.append(f"{prefix}: side must be 'long' or 'short', got '{ep.side}'")
            errors.extend(self._validate_expr(ep.trigger, f"{prefix}.trigger"))
            errors.extend(self._validate_expr(ep.strength, f"{prefix}.strength"))
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
                errors.extend(self._validate_expr(rule.condition, f"{rprefix}.condition"))
                if rule.action.type not in VALID_EXIT_ACTION_TYPES:
                    errors.append(
                        f"{rprefix}.action: type must be one of "
                        f"{sorted(VALID_EXIT_ACTION_TYPES)}, got '{rule.action.type}'"
                    )

        # Validate risk policy
        rp = self.risk_policy
        if rp.max_position <= 0:
            errors.append(f"risk_policy.max_position must be > 0, got {rp.max_position}")
        if rp.inventory_cap <= 0:
            errors.append(f"risk_policy.inventory_cap must be > 0, got {rp.inventory_cap}")
        if rp.position_sizing.mode not in VALID_SIZING_MODES:
            errors.append(
                f"risk_policy.position_sizing.mode must be one of "
                f"{sorted(VALID_SIZING_MODES)}, got '{rp.position_sizing.mode}'"
            )

        # Validate regimes (Phase 2)
        entry_names = {ep.name for ep in self.entry_policies}
        exit_names = {xp.name for xp in self.exit_policies}
        for i, regime in enumerate(self.regimes):
            prefix = f"regimes[{i}]"
            if not regime.name:
                errors.append(f"{prefix}: name is required")
            errors.extend(self._validate_expr(regime.when, f"{prefix}.when"))
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

        # Validate execution policy (Phase 2)
        if self.execution_policy is not None:
            ep_prefix = "execution_policy"
            xp = self.execution_policy
            if xp.placement_mode not in VALID_PLACEMENT_MODES:
                errors.append(
                    f"{ep_prefix}: placement_mode must be one of "
                    f"{sorted(VALID_PLACEMENT_MODES)}, got '{xp.placement_mode}'"
                )
            if xp.cancel_after_ticks < 0:
                errors.append(f"{ep_prefix}: cancel_after_ticks must be >= 0")
            if xp.max_reprices < 0:
                errors.append(f"{ep_prefix}: max_reprices must be >= 0")
            if xp.do_not_trade_when is not None:
                errors.extend(self._validate_expr(
                    xp.do_not_trade_when, f"{ep_prefix}.do_not_trade_when"
                ))

        return errors

    def _validate_expr(self, node: ExprNode, path: str) -> list[str]:
        """Recursively validate an expression node."""
        errors: list[str] = []
        if node.type not in VALID_NODE_TYPES:
            errors.append(f"{path}: unknown node type '{node.type}'")
            return errors

        if isinstance(node, ComparisonExpr):
            if node.op not in VALID_COMPARISON_OPS:
                errors.append(f"{path}: invalid comparison op '{node.op}'")
            if not node.feature:
                errors.append(f"{path}: feature name is required")
        elif isinstance(node, CrossExpr):
            if node.direction not in VALID_CROSS_DIRECTIONS:
                errors.append(f"{path}: invalid cross direction '{node.direction}'")
            if not node.feature:
                errors.append(f"{path}: feature name is required")
        elif isinstance(node, FeatureExpr):
            if not node.name:
                errors.append(f"{path}: feature name is required")
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
            errors.extend(self._validate_expr(node.expr, f"{path}.expr"))
        elif hasattr(node, "children"):
            for i, child in enumerate(node.children):
                errors.extend(self._validate_expr(child, f"{path}.children[{i}]"))
        elif hasattr(node, "child"):
            errors.extend(self._validate_expr(node.child, f"{path}.child"))

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
        if self.execution_policy and self.execution_policy.do_not_trade_when:
            features |= self.execution_policy.do_not_trade_when.collect_features()
        return features
