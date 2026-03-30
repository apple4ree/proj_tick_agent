from __future__ import annotations

from strategy_block.strategy_review.review_common import ReviewIssue, ReviewResult
from strategy_block.strategy_review.v2.contracts import BacktestFeedbackSummary, LLMReviewReport
from strategy_block.strategy_review.v2.repair_planner_v2 import RepairPlannerV2
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    RiskPolicyV2,
    StrategySpecV2,
)


def _spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="feedback_priority_spec",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                strength=ConstExpr(0.5),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="close_on_spread",
                        priority=1,
                        condition=ComparisonExpr(feature="spread_bps", op=">", threshold=40.0),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            ),
        ],
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=300),
    )


def _llm_review() -> LLMReviewReport:
    return LLMReviewReport(
        overall_assessment="revise_recommended",
        summary="test",
        issues=[],
        repair_recommended=True,
        focus_areas=["execution_policy"],
    )


def _static_review(*issues: ReviewIssue) -> ReviewResult:
    return ReviewResult(passed=len([i for i in issues if i.severity == "error"]) == 0, issues=list(issues))


def _feedback(
    *,
    churn: bool = False,
    queue: bool = False,
    cost: bool = False,
    adverse: bool = False,
    children_per_parent: float = 1.0,
    cancel_rate: float = 0.2,
    max_children: float = 10.0,
    maker_fill_ratio: float = 0.5,
    queue_blocked: float = 0.0,
    blocked_miss: float = 0.0,
    queue_ready: float = 10.0,
    adverse_share: float = 0.1,
    timeout_share: float = 0.1,
    net_pnl: float = 10.0,
    commission: float = 1.0,
    slippage: float = 1.0,
    impact: float = 0.1,
) -> BacktestFeedbackSummary:
    return BacktestFeedbackSummary(
        feedback_available=True,
        lifecycle={
            "children_per_parent": children_per_parent,
            "cancel_rate": cancel_rate,
            "max_children_per_parent": max_children,
        },
        queue={
            "queue_model": "risk_adverse",
            "maker_fill_ratio": maker_fill_ratio,
            "queue_blocked_count": queue_blocked,
            "blocked_miss_count": blocked_miss,
            "queue_ready_count": queue_ready,
        },
        cancel_reasons={
            "adverse_selection_share": adverse_share,
            "timeout_share": timeout_share,
        },
        cost={
            "net_pnl": net_pnl,
            "total_commission": commission,
            "total_slippage": slippage,
            "total_impact": impact,
        },
        flags={
            "churn_heavy": churn,
            "queue_ineffective": queue,
            "cost_dominated": cost,
            "adverse_selection_dominated": adverse,
        },
    )


def _op_names(plan) -> list[str]:
    return [op.op for op in plan.operations]


def test_churn_heavy_feedback_prioritizes_churn_reduction_ops_from_metrics():
    planner = RepairPlannerV2(client_mode="mock")

    feedback = _feedback(
        churn=False,
        children_per_parent=25.0,
        cancel_rate=0.9,
        max_children=200.0,
    )
    plan = planner.plan(
        spec=_spec(),
        static_review=_static_review(),
        llm_review=_llm_review(),
        backtest_feedback=feedback,
    )

    names = _op_names(plan)
    assert names[0] == "set_cancel_after_ticks"
    assert names[1] == "set_max_reprices"


def test_queue_ineffective_feedback_prioritizes_placement_then_bounded_repricing():
    planner = RepairPlannerV2(client_mode="mock")

    feedback = _feedback(
        queue=True,
        maker_fill_ratio=0.0,
        queue_blocked=100.0,
        blocked_miss=90.0,
        queue_ready=0.0,
    )
    plan = planner.plan(
        spec=_spec(),
        static_review=_static_review(),
        llm_review=_llm_review(),
        backtest_feedback=feedback,
    )

    names = _op_names(plan)
    assert names[0] == "set_placement_mode"
    assert "set_cancel_after_ticks" in names[:3]
    assert "set_max_reprices" in names[:4]


def test_cost_dominated_feedback_prioritizes_size_and_risk_controls():
    planner = RepairPlannerV2(client_mode="mock")

    feedback = _feedback(
        cost=True,
        net_pnl=-100.0,
        commission=80.0,
        slippage=60.0,
        impact=10.0,
    )
    plan = planner.plan(
        spec=_spec(),
        static_review=_static_review(),
        llm_review=_llm_review(),
        backtest_feedback=feedback,
    )

    names = _op_names(plan)
    assert names[0] == "set_base_size"
    assert names[1] == "set_max_size"
    assert "tighten_inventory_cap" in names[:4]


def test_adverse_selection_feedback_prioritizes_cancel_and_repricing_controls():
    planner = RepairPlannerV2(client_mode="mock")

    feedback = _feedback(
        adverse=False,
        adverse_share=0.95,
        timeout_share=0.02,
    )
    plan = planner.plan(
        spec=_spec(),
        static_review=_static_review(),
        llm_review=_llm_review(),
        backtest_feedback=feedback,
    )

    names = _op_names(plan)
    assert names[0] == "set_cancel_after_ticks"
    assert names[1] == "set_max_reprices"


def test_composite_flags_have_stable_deduped_priority_order():
    planner = RepairPlannerV2(client_mode="mock")

    feedback = _feedback(
        churn=True,
        queue=True,
        cost=True,
        adverse=True,
        children_per_parent=20.0,
        cancel_rate=0.9,
        max_children=150.0,
        maker_fill_ratio=0.0,
        queue_blocked=100.0,
        blocked_miss=100.0,
        queue_ready=0.0,
        adverse_share=0.9,
        timeout_share=0.05,
        net_pnl=-100.0,
        commission=80.0,
        slippage=70.0,
        impact=10.0,
    )

    plan = planner.plan(
        spec=_spec(),
        static_review=_static_review(),
        llm_review=_llm_review(),
        backtest_feedback=feedback,
    )

    pairs = [(op.op, op.target) for op in plan.operations]
    assert len(pairs) == len(set(pairs))

    names = _op_names(plan)
    assert names[:5] == [
        "set_cancel_after_ticks",
        "set_max_reprices",
        "set_placement_mode",
        "set_base_size",
        "set_max_size",
    ]


def test_no_feedback_keeps_baseline_behavior_without_feedback_matrix_ops():
    planner = RepairPlannerV2(client_mode="mock")

    static_review = _static_review(
        ReviewIssue(
            severity="warning",
            category="churn_risk_high",
            description="high churn",
            suggestion="reduce repricing",
        )
    )

    plan = planner.plan(
        spec=_spec(),
        static_review=static_review,
        llm_review=_llm_review(),
        backtest_feedback=None,
    )

    names = _op_names(plan)
    assert names[0] == "set_cancel_after_ticks"
    assert "set_base_size" not in names
    assert "set_max_size" not in names
