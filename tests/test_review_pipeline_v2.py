from __future__ import annotations

from strategy_block.strategy_review.v2.contracts import (
    BacktestFeedbackSummary,
    LLMReviewReport,
    RepairPlan,
)
from strategy_block.strategy_review.v2.pipeline_v2 import run_auto_repair, run_llm_review, run_static_review
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    ExecutionPolicyV2,
    RiskPolicyV2,
    StrategySpecV2,
)


def _missing_execution_policy_spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="pipeline_missing_ep",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                strength=ConstExpr(value=0.4),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="stop_only",
                        priority=1,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("unrealized_pnl_bps"),
                            op="<=",
                            threshold=-25.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            ),
        ],
        execution_policy=None,
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def _invalid_spec_no_close_all() -> StrategySpecV2:
    return StrategySpecV2(
        name="pipeline_invalid",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                strength=ConstExpr(0.4),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="partial_only",
                        priority=1,
                        condition=ComparisonExpr(feature="spread_bps", op=">", threshold=25.0),
                        action=ExitActionV2(type="reduce_position", reduce_fraction=0.5),
                    ),
                ],
            ),
        ],
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def _feedback_summary() -> BacktestFeedbackSummary:
    return BacktestFeedbackSummary(
        feedback_available=True,
        lifecycle={
            "signal_count": 10.0,
            "parent_order_count": 5.0,
            "child_order_count": 100.0,
            "children_per_parent": 20.0,
            "cancel_rate": 0.9,
            "avg_child_lifetime_seconds": 3.0,
            "max_children_per_parent": 50.0,
        },
        queue={
            "queue_model": "prob_queue",
            "queue_blocked_count": 20.0,
            "blocked_miss_count": 20.0,
            "queue_ready_count": 0.0,
            "maker_fill_ratio": 0.0,
        },
        cancel_reasons={
            "adverse_selection_share": 0.8,
            "timeout_share": 0.1,
            "stale_price_share": 0.1,
        },
        cost={
            "net_pnl": -100.0,
            "total_commission": 30.0,
            "total_slippage": 20.0,
            "total_impact": 5.0,
        },
        context={
            "resample": "500ms",
            "canonical_tick_interval_ms": 500.0,
            "configured_order_submit_ms": 5.0,
            "configured_cancel_ms": 2.0,
        },
        flags={
            "churn_heavy": True,
            "queue_ineffective": True,
            "cost_dominated": True,
            "adverse_selection_dominated": True,
        },
    )


def test_run_static_review_returns_review_result():
    result = run_static_review(_invalid_spec_no_close_all())
    assert result.passed is False
    assert any(i.severity == "error" for i in result.issues)


def test_run_llm_review_mock_runs_on_static_fail():
    spec = _invalid_spec_no_close_all()
    static_result = run_static_review(spec)
    llm_result = run_llm_review(
        spec=spec,
        static_review=static_result,
        backtest_environment={},
        client_mode="mock",
    )
    assert llm_result.repair_recommended is True


def test_run_auto_repair_applies_patch_and_reruns_static():
    spec = _invalid_spec_no_close_all()
    result = run_auto_repair(
        spec=spec,
        backtest_environment={},
        client_mode="mock",
    )

    assert result.static_review["passed"] is False
    assert result.llm_review is not None
    assert result.repair_plan is not None
    assert result.repair_applied is True
    assert result.final_static_review["passed"] is True
    assert result.final_passed is True
    assert result.repaired_spec is not None
    assert result.feedback_aware_repair is False


def test_run_auto_repair_inserts_missing_execution_policy_from_warning_path():
    spec = _missing_execution_policy_spec()
    static_result = run_static_review(spec)
    assert static_result.passed is True
    assert any(i.category == "execution_policy_implicit_risk" for i in static_result.issues)

    result = run_auto_repair(
        spec=spec,
        backtest_environment={},
        client_mode="mock",
    )

    assert result.repair_applied is True
    assert result.repaired_spec is not None
    repaired = StrategySpecV2.from_dict(result.repaired_spec)
    assert repaired.execution_policy is not None

    repaired_static = run_static_review(repaired)
    assert not any(i.category == "execution_policy_implicit_risk" for i in repaired_static.issues)


def _canonical_backtest_environment() -> dict:
    return {
        "resample": "500ms",
        "canonical_tick_interval_ms": 500.0,
        "market_data_delay_ms": 200.0,
        "decision_compute_ms": 50.0,
        "effective_delay_ms": 250.0,
        "latency": {
            "order_submit_ms": 5.0,
            "order_ack_ms": 15.0,
            "cancel_ms": 3.0,
            "order_ack_used_for_fill_gating": False,
        },
        "queue": {
            "queue_model": "prob_queue",
            "queue_position_assumption": 0.5,
        },
        "semantics": {
            "submit_latency_gating": True,
            "cancel_latency_gating": True,
            "replace_model": "minimal_immediate",
        },
    }


def test_run_llm_review_accepts_canonical_backtest_constraint_context():
    spec = _invalid_spec_no_close_all()
    static_result = run_static_review(spec)
    llm_result = run_llm_review(
        spec=spec,
        static_review=static_result,
        backtest_environment=_canonical_backtest_environment(),
        client_mode="mock",
    )
    assert llm_result.repair_recommended is True


def _short_horizon_passive_spec_cancel5() -> StrategySpecV2:
    return StrategySpecV2(
        name="pipeline_env_sensitive",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                strength=ConstExpr(value=0.4),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="stop",
                        priority=1,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("unrealized_pnl_bps"),
                            op="<=",
                            threshold=-25.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                    ExitRuleV2(
                        name="time",
                        priority=2,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("holding_ticks"),
                            op=">=",
                            threshold=10.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            ),
        ],
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=5,
            max_reprices=1,
        ),
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def test_run_static_review_uses_backtest_environment_for_env_aware_gate():
    spec = _short_horizon_passive_spec_cancel5()
    env_1s = dict(_canonical_backtest_environment())
    env_1s["resample"] = "1s"
    env_1s["canonical_tick_interval_ms"] = 1000.0

    result_1s = run_static_review(spec, backtest_environment=env_1s)
    result_500ms = run_static_review(spec, backtest_environment=_canonical_backtest_environment())

    assert not any(i.category == "churn_risk_high" and i.severity == "error" for i in result_1s.issues)
    assert any(i.category == "churn_risk_high" and i.severity == "error" for i in result_500ms.issues)


def test_run_llm_review_forwards_backtest_feedback_to_reviewer():
    class _CapturingLLMReviewer:
        def __init__(self) -> None:
            self.called_feedback = None

        def review(self, *, spec, static_review, backtest_environment=None, backtest_feedback=None):
            self.called_feedback = backtest_feedback
            return LLMReviewReport(
                overall_assessment="revise_recommended",
                summary="captured",
                issues=[],
                repair_recommended=False,
                focus_areas=[],
            )

    spec = _invalid_spec_no_close_all()
    static_result = run_static_review(spec)
    feedback = _feedback_summary()
    reviewer = _CapturingLLMReviewer()

    result = run_llm_review(
        spec=spec,
        static_review=static_result,
        backtest_environment=_canonical_backtest_environment(),
        backtest_feedback=feedback,
        llm_reviewer=reviewer,
    )

    assert reviewer.called_feedback is feedback
    assert result.summary == "captured"


def test_run_auto_repair_forwards_backtest_feedback_to_repair_planner():
    class _CapturingLLMReviewer:
        def review(self, *, spec, static_review, backtest_environment=None, backtest_feedback=None):
            return LLMReviewReport(
                overall_assessment="revise_recommended",
                summary="needs repair",
                issues=[],
                repair_recommended=True,
                focus_areas=["backtest_feedback"],
            )

    class _CapturingRepairPlanner:
        def __init__(self) -> None:
            self.called_feedback = None

        def plan(self, *, spec, static_review, llm_review, backtest_environment=None, backtest_feedback=None):
            self.called_feedback = backtest_feedback
            return RepairPlan(
                summary="no-op",
                operations=[],
                expected_effect="none",
                requires_manual_followup=True,
            )

    spec = _invalid_spec_no_close_all()
    feedback = _feedback_summary()
    planner = _CapturingRepairPlanner()

    result = run_auto_repair(
        spec=spec,
        backtest_environment=_canonical_backtest_environment(),
        backtest_feedback=feedback,
        llm_reviewer=_CapturingLLMReviewer(),
        repair_planner=planner,
    )

    assert planner.called_feedback is feedback
    assert result.backtest_feedback == feedback
    assert result.feedback_aware_repair is True


def test_run_static_review_surfaces_leakage_lint_errors() -> None:
    spec = StrategySpecV2(
        name="pipeline_leakage_case",
        entry_policies=[
            EntryPolicyV2(
                name="future_entry",
                side="long",
                trigger=ComparisonExpr(feature="future_mid_price", op=">", threshold=0.1),
                strength=ConstExpr(value=0.4),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="stop",
                        priority=1,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("unrealized_pnl_bps"),
                            op="<=",
                            threshold=-25.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            ),
        ],
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )

    result = run_static_review(spec)
    assert result.passed is False
    assert any(i.category == "leakage_lookahead_risk" and i.severity == "error" for i in result.issues)
