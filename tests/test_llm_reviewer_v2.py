from __future__ import annotations

from strategy_block.strategy_review.v2.contracts import (
    BacktestFeedbackSummary,
    LLMReviewIssue,
    LLMReviewReport,
)
from strategy_block.strategy_review.v2.llm_prompt_builder import build_llm_review_prompt, build_repair_prompt
from strategy_block.strategy_review.v2.llm_reviewer_v2 import LLMReviewerV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    RiskPolicyV2,
    StrategySpecV2,
)


def _valid_spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="llm_review_valid",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
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
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def _invalid_spec_no_close_all() -> StrategySpecV2:
    return StrategySpecV2(
        name="llm_review_invalid",
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
                        condition=ComparisonExpr(feature="spread_bps", op=">", threshold=20.0),
                        action=ExitActionV2(type="reduce_position", reduce_fraction=0.5),
                    ),
                ],
            ),
        ],
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def test_mock_llm_reviewer_returns_contract():
    spec = _valid_spec()
    static_review = StrategyReviewerV2().review(spec)
    reviewer = LLMReviewerV2(client_mode="mock")

    report = reviewer.review(spec=spec, static_review=static_review, backtest_environment={})

    assert report.overall_assessment in {
        "pass_with_notes",
        "revise_recommended",
        "high_risk",
    }
    assert isinstance(report.focus_areas, list)
    assert reviewer.last_query_meta.get("mode") == "mock"


def test_mock_llm_reviewer_handles_static_failures():
    spec = _invalid_spec_no_close_all()
    static_review = StrategyReviewerV2().review(spec)
    assert static_review.passed is False

    reviewer = LLMReviewerV2(client_mode="mock")
    report = reviewer.review(spec=spec, static_review=static_review, backtest_environment={})

    assert report.repair_recommended is True
    assert report.overall_assessment in {"revise_recommended", "high_risk"}


def _backtest_environment() -> dict:
    return {
        "resample": "500ms",
        "canonical_tick_interval_ms": 500.0,
        "market_data_delay_ms": 100.0,
        "decision_compute_ms": 20.0,
        "effective_delay_ms": 120.0,
        "latency": {
            "order_submit_ms": 3.0,
            "order_ack_ms": 7.0,
            "cancel_ms": 2.0,
            "order_ack_used_for_fill_gating": False,
        },
        "queue": {
            "queue_model": "risk_adverse",
            "queue_position_assumption": 0.5,
        },
        "semantics": {
            "submit_latency_gating": True,
            "cancel_latency_gating": True,
            "replace_model": "minimal_immediate",
        },
    }


def _feedback_summary() -> BacktestFeedbackSummary:
    return BacktestFeedbackSummary(
        feedback_available=True,
        lifecycle={
            "signal_count": 82.0,
            "parent_order_count": 77.0,
            "child_order_count": 1975.0,
            "children_per_parent": 25.6,
            "cancel_rate": 0.96,
            "avg_child_lifetime_seconds": 5.08,
            "max_children_per_parent": 435.0,
        },
        queue={
            "queue_model": "risk_adverse",
            "queue_blocked_count": 10022.0,
            "blocked_miss_count": 10022.0,
            "queue_ready_count": 0.0,
            "maker_fill_ratio": 0.0,
        },
        cancel_reasons={
            "adverse_selection_share": 0.89,
            "timeout_share": 0.10,
            "stale_price_share": 0.0,
            "max_reprices_reached_share": 0.0,
            "micro_event_block_share": 0.0,
        },
        cost={
            "net_pnl": -100.0,
            "total_commission": 60.0,
            "total_slippage": 50.0,
            "total_impact": 10.0,
        },
        context={
            "resample": "1s",
            "canonical_tick_interval_ms": 1000.0,
            "configured_order_submit_ms": 30.0,
            "configured_cancel_ms": 20.0,
        },
        flags={
            "churn_heavy": True,
            "queue_ineffective": True,
            "cost_dominated": True,
            "adverse_selection_dominated": True,
        },
    )


def test_llm_review_prompt_contains_canonical_backtest_constraint_summary():
    spec = _valid_spec()
    static_review = StrategyReviewerV2().review(spec)
    _, user_prompt = build_llm_review_prompt(
        spec=spec,
        static_review=static_review,
        backtest_environment=_backtest_environment(),
    )

    assert "[BACKTEST CONSTRAINT SUMMARY]" in user_prompt
    assert "Backtest constraint summary (canonical)" in user_prompt
    assert "tick = resample step" in user_prompt
    assert "passive fills require queue waiting" in user_prompt
    assert "repricing resets queue position" in user_prompt
    assert "replace is minimal immediate, not staged venue replace" in user_prompt


def test_repair_prompt_contains_canonical_backtest_constraint_summary():
    spec = _valid_spec()
    static_review = StrategyReviewerV2().review(spec)
    llm_review = LLMReviewReport(
        overall_assessment="revise_recommended",
        summary="test",
        issues=[
            LLMReviewIssue(
                severity="warning",
                category="execution_policy",
                description="test",
                rationale="test",
                suggested_fix="test",
            ),
        ],
        repair_recommended=True,
        focus_areas=["execution_policy"],
    )

    _, user_prompt = build_repair_prompt(
        spec=spec,
        static_review=static_review,
        llm_review=llm_review,
        backtest_environment=_backtest_environment(),
    )

    assert "[BACKTEST CONSTRAINT SUMMARY]" in user_prompt
    assert "Backtest constraint summary (canonical)" in user_prompt
    assert "tick = resample step" in user_prompt
    assert "submit/cancel latency compounds churn cost" in user_prompt
    assert "low-churn execution is preferred under queue and latency friction" in user_prompt


def test_review_prompt_includes_feedback_summary_and_json_blocks():
    spec = _valid_spec()
    static_review = StrategyReviewerV2().review(spec)
    _, user_prompt = build_llm_review_prompt(
        spec=spec,
        static_review=static_review,
        backtest_environment=_backtest_environment(),
        backtest_feedback=_feedback_summary(),
    )

    assert "## Recent Backtest Feedback" in user_prompt
    assert "## Recent Backtest Feedback (JSON)" in user_prompt
    assert "Recent backtest feedback (aggregate-only):" in user_prompt
    assert "\"feedback_available\": true" in user_prompt


def test_repair_prompt_includes_feedback_summary_and_json_blocks():
    spec = _valid_spec()
    static_review = StrategyReviewerV2().review(spec)
    llm_review = LLMReviewReport(
        overall_assessment="revise_recommended",
        summary="test",
        issues=[
            LLMReviewIssue(
                severity="warning",
                category="execution_policy",
                description="test",
                rationale="test",
                suggested_fix="test",
            ),
        ],
        repair_recommended=True,
        focus_areas=["execution_policy"],
    )

    _, user_prompt = build_repair_prompt(
        spec=spec,
        static_review=static_review,
        llm_review=llm_review,
        backtest_environment=_backtest_environment(),
        backtest_feedback=_feedback_summary(),
    )

    assert "## Recent Backtest Feedback" in user_prompt
    assert "## Recent Backtest Feedback (JSON)" in user_prompt
    assert "Recent backtest feedback (aggregate-only):" in user_prompt
    assert "\"feedback_available\": true" in user_prompt


def test_review_prompt_uses_feedback_fallback_when_missing():
    spec = _valid_spec()
    static_review = StrategyReviewerV2().review(spec)
    _, user_prompt = build_llm_review_prompt(
        spec=spec,
        static_review=static_review,
        backtest_environment=_backtest_environment(),
        backtest_feedback=None,
    )

    assert (
        "No recent backtest feedback provided; critique spec using static review + "
        "environment context only."
    ) in user_prompt
