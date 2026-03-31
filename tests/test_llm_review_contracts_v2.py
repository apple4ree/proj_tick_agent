from __future__ import annotations

import pytest
from pydantic import ValidationError

from strategy_block.strategy_review.v2.contracts import (
    BacktestFeedbackSummary,
    LLMReviewIssue,
    LLMReviewReport,
    RepairOperation,
    ReviewPipelineResult,
)


def test_llm_review_report_contract_valid():
    report = LLMReviewReport(
        overall_assessment="revise_recommended",
        summary="Needs targeted fixes",
        issues=[
            LLMReviewIssue(
                severity="warning",
                category="execution_risk_mismatch",
                description="Execution hints are too aggressive",
                rationale="Likely to increase churn",
                suggested_fix="reduce max_reprices",
            ),
        ],
        repair_recommended=True,
        focus_areas=["execution_policy", "risk_policy"],
    )
    assert report.repair_recommended is True
    assert report.issues[0].severity == "warning"


def test_llm_review_issue_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        LLMReviewIssue(
            severity="error",
            category="x",
            description="y",
        )


def test_repair_operation_rejects_unknown_op():
    with pytest.raises(ValidationError):
        RepairOperation(
            op="free_form_rewrite",
            target="global",
            value={},
            reason="not allowed",
        )


def test_review_pipeline_result_contract_valid():
    result = ReviewPipelineResult(
        static_review={"passed": False, "issues": [{"severity": "error"}]},
        llm_review=None,
        repair_plan=None,
        repair_applied=False,
        final_static_review={"passed": True, "issues": []},
        final_passed=True,
    )
    assert result.final_passed is True
    assert result.static_review["passed"] is False
    assert result.feedback_aware_repair is False


def test_backtest_feedback_summary_contract_valid():
    feedback = BacktestFeedbackSummary(
        feedback_available=True,
        lifecycle={"signal_count": 1.0, "child_order_count": 2.0},
        queue={"queue_model": "prob_queue", "blocked_miss_count": 3.0},
        cancel_reasons={"adverse_selection_share": 0.7},
        cost={"net_pnl": -1.0, "total_commission": 2.0},
        context={"resample": "500ms", "canonical_tick_interval_ms": 500.0},
        flags={"churn_heavy": True},
    )
    assert feedback.feedback_available is True
    assert feedback.flags.churn_heavy is True
