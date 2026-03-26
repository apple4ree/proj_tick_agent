"""Intermediate plan schema for OpenAI structured generation.

OpenAI returns a StrategyPlan (this schema), NOT a final StrategySpecV2.
The plan is then lowered to StrategySpecV2 by lowering.py.

This schema is designed to be:
1. Human-readable — a researcher can review the plan before lowering
2. Lowerable — every field maps deterministically to StrategySpecV2 constructs
3. Constrained — OpenAI cannot invent unsupported constructs
4. OpenAI strict-schema compatible — no dynamic maps, no unsupported constraints

OpenAI structured output strict schema rules observed here:
- No ``dict[str, X]`` (generates ``additionalProperties`` — use list-of-objects instead)
- No ``ge``/``le``/``gt``/``lt`` Field constraints (generate ``minimum``/``maximum``)
- All fields either required or have defaults (SDK makes all required in strict mode)
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── Condition plan (simplified, lowerable to ExprNode) ──────────────

class ConditionPlan(BaseModel):
    """A single condition that lowers to an ExprNode.

    Supported forms (use exactly one):
    - feature comparison: feature + op + threshold
    - state_var comparison: state_var + op + threshold
    - position_attr comparison: position_attr + op + threshold
    - composite: combine="all"|"any", children=[ConditionPlan, ...]
    - cross: cross_feature + cross_threshold + cross_direction
    - persist: persist_condition + persist_window + persist_min_true
    - rolling comparison: rolling_feature + rolling_method + rolling_window + op + threshold

    CRITICAL NAMESPACE RULE:
    - feature, cross_feature, rolling_feature → market data ONLY
      (spread_bps, order_imbalance, depth_imbalance, etc.)
    - position_attr → position state ONLY
      (holding_ticks, unrealized_pnl_bps, entry_price, position_size, position_side)

    Using position attributes in the feature field causes SILENT RUNTIME FAILURE
    (the lookup returns 0.0 and the condition becomes dead). The validator will
    reject any plan that violates this rule.
    """
    # ── Market feature comparison ─────────────────────────────────────
    # Market features come from the LOB/trade data pipeline.
    # ALLOWED: spread_bps, order_imbalance, depth_imbalance, etc.
    # FORBIDDEN here: holding_ticks, unrealized_pnl_bps, entry_price,
    #   position_size, position_side (use position_attr instead)
    feature: str | None = Field(
        default=None,
        description=(
            "Market feature name from the LOB/trade pipeline "
            "(e.g. spread_bps, order_imbalance, depth_imbalance). "
            "FORBIDDEN: holding_ticks, unrealized_pnl_bps, entry_price, "
            "position_size, position_side — these MUST use position_attr."
        ),
    )
    op: str | None = Field(
        default=None,
        description="Comparison operator: >, <, >=, <=, ==, !=",
    )
    threshold: float | None = Field(
        default=None,
        description="Numeric threshold for the comparison",
    )

    # ── State variable comparison ─────────────────────────────────────
    state_var: str | None = Field(
        default=None,
        description="User-defined state variable name (e.g. loss_streak)",
    )

    # ── Position attribute comparison ─────────────────────────────────
    # Position attributes are computed by the runtime engine.
    # They return 0.0 when flat — use ONLY in exit rules.
    position_attr: str | None = Field(
        default=None,
        description=(
            "Position attribute: holding_ticks, unrealized_pnl_bps, "
            "entry_price, position_size, position_side. "
            "Returns 0.0 when no position is held. "
            "Use in exit rules, NOT in entry triggers or preconditions."
        ),
    )

    # ── Composite (all / any) ─────────────────────────────────────────
    combine: str | None = Field(
        default=None,
        description='Logical combinator: "all" (AND) or "any" (OR)',
    )
    children: list[ConditionPlan] | None = None

    # ── Cross condition ───────────────────────────────────────────────
    # Market features ONLY — position attributes cannot cross thresholds
    cross_feature: str | None = Field(
        default=None,
        description=(
            "Market feature for cross detection. "
            "FORBIDDEN: position attributes (holding_ticks, etc.)"
        ),
    )
    cross_threshold: float | None = None
    cross_direction: str | None = Field(
        default=None,
        description='"above" (prev <= thresh < current) or "below" (prev >= thresh > current)',
    )

    # ── Persist condition ─────────────────────────────────────────────
    persist_condition: ConditionPlan | None = None
    persist_window: int | None = Field(
        default=None,
        description="Number of ticks to observe the condition over",
    )
    persist_min_true: int | None = Field(
        default=None,
        description="Minimum ticks the condition must be true within the window",
    )

    # ── Rolling comparison ────────────────────────────────────────────
    # Market features ONLY — position attributes cannot be aggregated
    rolling_feature: str | None = Field(
        default=None,
        description=(
            "Market feature for rolling aggregation. "
            "FORBIDDEN: position attributes (holding_ticks, etc.)"
        ),
    )
    rolling_method: str | None = Field(
        default=None,
        description='Aggregation method: "mean", "min", or "max"',
    )
    rolling_window: int | None = Field(
        default=None,
        description="Number of ticks for the rolling window",
    )


# ── Entry plan ──────────────────────────────────────────────────────

class EntryPlan(BaseModel):
    """An entry policy plan.

    Entry triggers should use market features (feature field), NOT position
    attributes. Position attributes return 0.0 when flat and are meaningless
    for entry decisions.
    """
    name: str = Field(description="Unique entry policy name (snake_case)")
    side: str = Field(description='Entry direction: "long" or "short"')
    trigger: ConditionPlan = Field(
        description=(
            "Condition that fires this entry. "
            "Use market features (feature field), NOT position_attr."
        ),
    )
    strength: float = Field(default=0.5, description="Signal strength 0.0–1.0")
    cooldown_ticks: int = Field(default=0, description="Ticks to wait before re-entry (typical: 10–200)")
    no_reentry_until_flat: bool = False


# ── Exit rule plan ──────────────────────────────────────────────────

class ExitRulePlan(BaseModel):
    """A single exit rule.

    Exit conditions should use position_attr for stop-loss and time exits:
    - Stop-loss: {position_attr: "unrealized_pnl_bps", op: "<=", threshold: -25.0}
    - Time exit: {position_attr: "holding_ticks", op: ">=", threshold: 100}

    Every exit policy MUST include at least one close_all rule.
    """
    name: str = Field(description="Unique exit rule name (snake_case)")
    priority: int = Field(default=10, description="Lower number = higher priority (evaluated first)")
    condition: ConditionPlan = Field(
        description=(
            "When this fires, the exit action is taken. "
            "Use position_attr for stop-loss/time exits, feature for market-based exits."
        ),
    )
    action: str = Field(
        default="close_all",
        description='"close_all" (close entire position) or "reduce_position" (partial exit)',
    )
    reduce_fraction: float | None = Field(
        default=None,
        description="Fraction to reduce (0.0–1.0), only used with reduce_position",
    )


class ExitPolicyPlan(BaseModel):
    """An exit policy with ordered rules.

    MUST include at least one close_all rule with a position_attr-based
    condition (stop-loss or time exit) as a robust fail-safe.
    """
    name: str = Field(description="Unique exit policy name (snake_case)")
    rules: list[ExitRulePlan] = Field(
        description="Ordered exit rules. Must include at least one close_all rule.",
    )


# ── Risk plan ───────────────────────────────────────────────────────

class DegradationRulePlan(BaseModel):
    """A risk degradation rule."""
    condition: ConditionPlan
    action_type: str  # "scale_strength" | "scale_max_position" | "block_new_entries"
    factor: float = 1.0


class RiskPlan(BaseModel):
    """Risk policy plan."""
    max_position: int = 500
    inventory_cap: int = 1000
    sizing_mode: str = "fixed"  # "fixed" | "signal_proportional" | "kelly"
    base_size: int = 100
    max_size: int = 500
    degradation_rules: list[DegradationRulePlan] = Field(default_factory=list)


# ── Execution plan ──────────────────────────────────────────────────

class ExecutionAdaptationPlan(BaseModel):
    """Execution adaptation rule."""
    condition: ConditionPlan
    placement_mode: str | None = None
    cancel_after_ticks: int | None = None
    max_reprices: int | None = None


class ExecutionPlan(BaseModel):
    """Execution policy plan."""
    placement_mode: str = "passive_join"  # "passive_join" | "aggressive_cross" | "adaptive"
    cancel_after_ticks: int = 0
    max_reprices: int = 0
    do_not_trade_when: ConditionPlan | None = None
    adaptation_rules: list[ExecutionAdaptationPlan] = Field(default_factory=list)


# ── Regime plan ─────────────────────────────────────────────────────

class RegimePlan(BaseModel):
    """A market regime definition."""
    name: str
    priority: int = 10
    when: ConditionPlan
    entry_policy_refs: list[str] = Field(default_factory=list)
    exit_policy_ref: str = ""


# ── State plan ──────────────────────────────────────────────────────

class StateUpdatePlan(BaseModel):
    """A state variable update."""
    var: str
    op: str  # "set" | "increment" | "reset"
    value: float = 0.0


class StateEventPlan(BaseModel):
    """A state event.

    IMPORTANT: Every variable that is incremented on one event (e.g. on_exit_loss)
    MUST also be reset on another event (e.g. on_exit_profit or on_flatten).
    Otherwise guards/degradation referencing it will become permanent.
    """
    name: str = Field(description="Event name (snake_case)")
    on: str = Field(
        description='Trigger: "on_entry", "on_exit_profit", "on_exit_loss", "on_flatten"',
    )
    updates: list[StateUpdatePlan]


class StateGuardPlan(BaseModel):
    """A state guard that blocks entry."""
    name: str
    condition: ConditionPlan
    effect: str = "block_entry"


class StateVarPlan(BaseModel):
    """A state variable definition (name + initial value).

    Replaces ``dict[str, float]`` which generates ``additionalProperties``
    and is rejected by OpenAI strict JSON schema.
    """
    name: str
    initial_value: float = 0.0


class StatePlan(BaseModel):
    """State policy plan."""
    vars: list[StateVarPlan] = Field(default_factory=list)
    guards: list[StateGuardPlan] = Field(default_factory=list)
    events: list[StateEventPlan] = Field(default_factory=list)


# ── Precondition plan ──────────────────────────────────────────────

class PreconditionPlan(BaseModel):
    """A precondition gate."""
    name: str
    condition: ConditionPlan


# ── Top-level strategy plan ────────────────────────────────────────

class StrategyPlan(BaseModel):
    """Intermediate structured plan for a v2 strategy.

    OpenAI generates this schema. Code then lowers it to StrategySpecV2.

    Validation rules enforced downstream:
    - position_attr names (holding_ticks, unrealized_pnl_bps, etc.) in
      feature/cross_feature/rolling_feature → REJECTED
    - No close_all exit rule → REJECTED
    - Entry gates without robust close_all fail-safe → REJECTED
    - State increment without reset → REJECTED
    """
    name: str = Field(description="Strategy name (snake_case)")
    description: str = Field(description="1-3 sentence strategy description")
    research_goal: str = Field(description="The research goal this strategy addresses")
    strategy_style: str = Field(
        description="Primary style: momentum | mean_reversion | scalping | stat_arb | execution_adaptive"
    )

    preconditions: list[PreconditionPlan] = Field(
        default_factory=list,
        description="Market-level gates using feature conditions. Evaluated before entries.",
    )
    entry_policies: list[EntryPlan] = Field(
        default_factory=list,
        description="At least one required. Use feature-based triggers, not position_attr.",
    )
    exit_policies: list[ExitPolicyPlan] = Field(
        default_factory=list,
        description=(
            "At least one required with a close_all rule. "
            "Use position_attr for stop-loss/time exits."
        ),
    )
    risk_policy: RiskPlan = Field(default_factory=RiskPlan)
    execution_policy: ExecutionPlan | None = None
    regimes: list[RegimePlan] = Field(default_factory=list)
    state_policy: StatePlan | None = None

    notes: str = Field(
        default="",
        description="Optional design rationale or notes for the researcher",
    )

    SCHEMA_VERSION: str = Field(default="plan_v1", description="Plan schema version identifier")
