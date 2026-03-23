"""Pydantic schemas for Multi-Agent structured outputs.

Each agent produces a typed output that constrains the LLM to generate
only compiler-compatible values (features, operators, exit types, etc.).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Canonical feature list — mirrors strategy_review.reviewer.KNOWN_FEATURES
KNOWN_FEATURES_LIST: list[str] = sorted([
    "mid_price", "spread_bps", "order_imbalance",
    "best_bid", "best_ask",
    "bid_depth_5", "ask_depth_5", "depth_imbalance",
    "trade_count", "recent_volume", "trade_flow_imbalance",
    "price_impact_buy", "price_impact_sell",
    "price_impact_buy_bps", "price_impact_sell_bps",
    "volume_surprise", "micro_price", "trade_flow",
    "depth_imbalance_l1",
    "log_bid_depth", "log_ask_depth",
    "bid_depth", "ask_depth",
])

KNOWN_FEATURES_SET: frozenset[str] = frozenset(KNOWN_FEATURES_LIST)

VALID_OPERATORS: list[str] = [">", "<", ">=", "<=", "==", "cross_above", "cross_below"]
VALID_SIZING_MODES: list[str] = ["fixed", "signal_proportional", "kelly"]
VALID_EXIT_TYPES: list[str] = [
    "stop_loss", "take_profit", "trailing_stop", "time_exit", "signal_reversal",
]
VALID_FILTER_ACTIONS: list[str] = ["block", "reduce"]
VALID_STYLES: list[str] = [
    "momentum", "mean_reversion", "contrarian", "microstructure", "statistical_arbitrage",
]


# ── Researcher Agent ──────────────────────────────────────────────────

class IdeaBrief(BaseModel):
    """A single strategy idea proposed by the Researcher Agent."""
    name: str = Field(description="Short snake_case strategy name")
    thesis: str = Field(description="Core hypothesis in 1-2 sentences")
    core_features: list[str] = Field(description="Key features to use")
    style: str = Field(description="Strategy style")
    rationale: str = Field(default="", description="Why this idea could work")

    @field_validator("core_features", mode="before")
    @classmethod
    def filter_known_features(cls, v: list[str]) -> list[str]:
        return [f for f in v if f in KNOWN_FEATURES_SET] or ["order_imbalance"]


class IdeaBriefList(BaseModel):
    """List of strategy ideas from the Researcher Agent."""
    ideas: list[IdeaBrief] = Field(min_length=1)


# ── Factor Designer Agent ─────────────────────────────────────────────

OperatorType = Literal[">", "<", ">=", "<=", "==", "cross_above", "cross_below"]
FilterActionType = Literal["block", "reduce"]


class SignalRuleDraft(BaseModel):
    """A single signal rule draft."""
    feature: str
    operator: OperatorType
    threshold: float
    score_contribution: float
    description: str = ""

    @field_validator("feature")
    @classmethod
    def validate_feature(cls, v: str) -> str:
        if v not in KNOWN_FEATURES_SET:
            raise ValueError(f"Unknown feature: {v}. Must be one of {KNOWN_FEATURES_LIST}")
        return v


class FilterRuleDraft(BaseModel):
    """A single filter rule draft."""
    feature: str
    operator: OperatorType
    threshold: float
    action: FilterActionType = "block"
    description: str = ""

    @field_validator("feature")
    @classmethod
    def validate_feature(cls, v: str) -> str:
        if v not in KNOWN_FEATURES_SET:
            raise ValueError(f"Unknown feature: {v}. Must be one of {KNOWN_FEATURES_LIST}")
        return v


class SignalDraft(BaseModel):
    """Factor Designer Agent output: signal rules + filters."""
    signal_rules: list[SignalRuleDraft] = Field(min_length=1)
    filters: list[FilterRuleDraft] = Field(default_factory=list)
    rationale: str = Field(default="", description="Design rationale")


# ── Risk Designer Agent ────────────────────────────────────────────────

SizingModeType = Literal["fixed", "signal_proportional", "kelly"]
ExitTypeType = Literal["stop_loss", "take_profit", "trailing_stop", "time_exit", "signal_reversal"]


class PositionRuleDraft(BaseModel):
    """Position sizing draft."""
    max_position: int = Field(ge=1, le=10000, default=500)
    sizing_mode: SizingModeType = "signal_proportional"
    fixed_size: int = Field(ge=1, le=10000, default=100)
    holding_period_ticks: int = Field(ge=0, le=10000, default=10)
    inventory_cap: int = Field(ge=1, le=10000, default=1000)


class ExitRuleDraft(BaseModel):
    """A single exit rule draft."""
    exit_type: ExitTypeType
    threshold_bps: float = Field(ge=0.0, le=500.0, default=0.0)
    timeout_ticks: int = Field(ge=0, le=100000, default=0)
    description: str = ""


class RiskDraft(BaseModel):
    """Risk Designer Agent output."""
    position_rule: PositionRuleDraft
    exit_rules: list[ExitRuleDraft] = Field(min_length=1)
    latency_notes: str = Field(default="", description="Latency considerations")


# ── LLM Reviewer Agent ────────────────────────────────────────────────

class ReviewIssueDraft(BaseModel):
    """A single review issue."""
    category: str
    severity: Literal["error", "warning", "info"] = "warning"
    description: str
    suggestion: str = ""


class ReviewDecision(BaseModel):
    """LLM Reviewer Agent output."""
    approved: bool
    issues: list[ReviewIssueDraft] = Field(default_factory=list)
    suggested_changes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
