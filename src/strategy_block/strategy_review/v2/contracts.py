"""Structured contracts for LLM review + constrained repair (v2)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from strategy_block.strategy_review.review_common import ReviewResult


LLMSeverity = Literal["info", "warning", "high_risk"]
LLMAssessment = Literal["pass_with_notes", "revise_recommended", "high_risk"]

RepairOpType = Literal[
    "set_cancel_after_ticks",
    "set_max_reprices",
    "set_placement_mode",
    "set_base_size",
    "set_max_size",
    "add_stop_loss_exit",
    "add_time_exit",
    "tighten_inventory_cap",
    "simplify_entry_trigger",
    "set_holding_ticks",
]


class LLMReviewIssue(BaseModel):
    severity: LLMSeverity
    category: str
    description: str
    rationale: str = ""
    suggested_fix: str = ""


class LLMReviewReport(BaseModel):
    overall_assessment: LLMAssessment
    summary: str
    issues: list[LLMReviewIssue] = Field(default_factory=list)
    repair_recommended: bool = False
    focus_areas: list[str] = Field(default_factory=list)


class RepairOperation(BaseModel):
    op: RepairOpType
    target: str = "global"
    value: Any = None
    reason: str = ""


class RepairPlan(BaseModel):
    summary: str
    operations: list[RepairOperation] = Field(default_factory=list)
    expected_effect: str = ""
    requires_manual_followup: bool = False


class BacktestFeedbackLifecycle(BaseModel):
    signal_count: float | None = None
    parent_order_count: float | None = None
    child_order_count: float | None = None
    children_per_parent: float | None = None
    cancel_rate: float | None = None
    avg_child_lifetime_seconds: float | None = None
    max_children_per_parent: float | None = None


class BacktestFeedbackQueue(BaseModel):
    queue_model: str | None = None
    queue_blocked_count: float | None = None
    blocked_miss_count: float | None = None
    queue_ready_count: float | None = None
    maker_fill_ratio: float | None = None


class BacktestFeedbackCancelMix(BaseModel):
    adverse_selection_share: float | None = None
    timeout_share: float | None = None
    stale_price_share: float | None = None
    max_reprices_reached_share: float | None = None
    micro_event_block_share: float | None = None


class BacktestFeedbackCost(BaseModel):
    net_pnl: float | None = None
    total_commission: float | None = None
    total_slippage: float | None = None
    total_impact: float | None = None


class BacktestFeedbackContext(BaseModel):
    resample: str | None = None
    canonical_tick_interval_ms: float | None = None
    configured_order_submit_ms: float | None = None
    configured_cancel_ms: float | None = None


class BacktestFeedbackFlags(BaseModel):
    churn_heavy: bool = False
    queue_ineffective: bool = False
    cost_dominated: bool = False
    adverse_selection_dominated: bool = False


class BacktestFeedbackSummary(BaseModel):
    feedback_available: bool = False
    lifecycle: BacktestFeedbackLifecycle = Field(default_factory=BacktestFeedbackLifecycle)
    queue: BacktestFeedbackQueue = Field(default_factory=BacktestFeedbackQueue)
    cancel_reasons: BacktestFeedbackCancelMix = Field(default_factory=BacktestFeedbackCancelMix)
    cost: BacktestFeedbackCost = Field(default_factory=BacktestFeedbackCost)
    context: BacktestFeedbackContext = Field(default_factory=BacktestFeedbackContext)
    flags: BacktestFeedbackFlags = Field(default_factory=BacktestFeedbackFlags)


class ReviewPipelineResult(BaseModel):
    static_review: dict[str, Any]
    llm_review: LLMReviewReport | None = None
    repair_plan: RepairPlan | None = None
    repair_applied: bool = False
    final_static_review: dict[str, Any]
    final_passed: bool
    repaired_spec: dict[str, Any] | None = None
    backtest_feedback: BacktestFeedbackSummary | None = None
    feedback_aware_repair: bool = False


def review_result_to_dict(result: ReviewResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return result.to_dict()
