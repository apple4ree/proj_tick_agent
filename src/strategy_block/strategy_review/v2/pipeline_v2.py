"""Pipeline for static review + optional LLM critique + constrained repair."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from strategy_block.strategy_review.review_common import ReviewResult
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2

from .contracts import BacktestFeedbackSummary, LLMReviewReport, RepairPlan, ReviewPipelineResult
from .llm_reviewer_v2 import LLMReviewerV2
from .patcher_v2 import StrategyRepairPatcherV2
from .repair_planner_v2 import RepairPlannerV2


def _feedback_available(backtest_feedback: BacktestFeedbackSummary | None) -> bool:
    return bool(backtest_feedback is not None and backtest_feedback.feedback_available)


def run_static_review(
    spec: StrategySpecV2,
    reviewer: StrategyReviewerV2 | None = None,
    backtest_environment: dict[str, Any] | None = None,
) -> ReviewResult:
    active_reviewer = reviewer or StrategyReviewerV2()
    return active_reviewer.review(spec, backtest_environment=backtest_environment)


def run_llm_review(
    *,
    spec: StrategySpecV2,
    static_review: ReviewResult,
    backtest_environment: dict[str, Any] | None = None,
    backtest_feedback: BacktestFeedbackSummary | None = None,
    llm_reviewer: LLMReviewerV2 | None = None,
    backend: str = "openai",
    client_mode: str = "mock",
    model: str | None = None,
    replay_path: Path | str | None = None,
) -> LLMReviewReport:
    active = llm_reviewer or LLMReviewerV2(
        backend=backend,
        client_mode=client_mode,
        model=model,
        replay_path=replay_path,
    )
    return active.review(
        spec=spec,
        static_review=static_review,
        backtest_environment=backtest_environment,
        backtest_feedback=backtest_feedback,
    )


def run_auto_repair(
    *,
    spec: StrategySpecV2,
    backtest_environment: dict[str, Any] | None = None,
    backtest_feedback: BacktestFeedbackSummary | None = None,
    static_reviewer: StrategyReviewerV2 | None = None,
    llm_reviewer: LLMReviewerV2 | None = None,
    repair_planner: RepairPlannerV2 | None = None,
    patcher: StrategyRepairPatcherV2 | None = None,
    backend: str = "openai",
    client_mode: str = "mock",
    model: str | None = None,
    replay_path: Path | str | None = None,
) -> ReviewPipelineResult:
    static_result = run_static_review(
        spec,
        reviewer=static_reviewer,
        backtest_environment=backtest_environment,
    )
    llm_result = run_llm_review(
        spec=spec,
        static_review=static_result,
        backtest_environment=backtest_environment,
        backtest_feedback=backtest_feedback,
        llm_reviewer=llm_reviewer,
        backend=backend,
        client_mode=client_mode,
        model=model,
        replay_path=replay_path,
    )

    planner = repair_planner or RepairPlannerV2(
        backend=backend,
        client_mode=client_mode,
        model=model,
        replay_path=replay_path,
    )
    repair_plan: RepairPlan | None = None
    repair_applied = False
    final_static = static_result
    repaired_spec: dict[str, Any] | None = None

    if llm_result.repair_recommended:
        repair_plan = planner.plan(
            spec=spec,
            static_review=static_result,
            llm_review=llm_result,
            backtest_environment=backtest_environment,
            backtest_feedback=backtest_feedback,
        )
        if repair_plan.operations:
            active_patcher = patcher or StrategyRepairPatcherV2()
            patched_spec = active_patcher.apply(spec, repair_plan)
            final_static = run_static_review(
                patched_spec,
                reviewer=static_reviewer,
                backtest_environment=backtest_environment,
            )
            repair_applied = True
            repaired_spec = patched_spec.to_dict()

    feedback_aware_repair = bool(
        _feedback_available(backtest_feedback)
        and llm_result.repair_recommended
    )

    return ReviewPipelineResult(
        static_review=static_result.to_dict(),
        llm_review=llm_result,
        repair_plan=repair_plan,
        repair_applied=repair_applied,
        final_static_review=final_static.to_dict(),
        final_passed=final_static.passed,
        repaired_spec=repaired_spec,
        backtest_feedback=backtest_feedback,
        feedback_aware_repair=feedback_aware_repair,
    )


def run_pipeline(
    *,
    mode: str,
    spec: StrategySpecV2,
    backtest_environment: dict[str, Any] | None = None,
    backtest_feedback: BacktestFeedbackSummary | None = None,
    static_reviewer: StrategyReviewerV2 | None = None,
    llm_reviewer: LLMReviewerV2 | None = None,
    repair_planner: RepairPlannerV2 | None = None,
    patcher: StrategyRepairPatcherV2 | None = None,
    backend: str = "openai",
    client_mode: str = "mock",
    model: str | None = None,
    replay_path: Path | str | None = None,
) -> ReviewPipelineResult:
    normalized_mode = mode.replace("_", "-").lower()
    static_result = run_static_review(
        spec,
        reviewer=static_reviewer,
        backtest_environment=backtest_environment,
    )

    if normalized_mode == "static":
        return ReviewPipelineResult(
            static_review=static_result.to_dict(),
            llm_review=None,
            repair_plan=None,
            repair_applied=False,
            final_static_review=static_result.to_dict(),
            final_passed=static_result.passed,
            repaired_spec=None,
            backtest_feedback=backtest_feedback,
            feedback_aware_repair=False,
        )

    if normalized_mode == "llm-review":
        llm_result = run_llm_review(
            spec=spec,
            static_review=static_result,
            backtest_environment=backtest_environment,
            backtest_feedback=backtest_feedback,
            llm_reviewer=llm_reviewer,
            backend=backend,
            client_mode=client_mode,
            model=model,
            replay_path=replay_path,
        )
        return ReviewPipelineResult(
            static_review=static_result.to_dict(),
            llm_review=llm_result,
            repair_plan=None,
            repair_applied=False,
            final_static_review=static_result.to_dict(),
            final_passed=static_result.passed,
            repaired_spec=None,
            backtest_feedback=backtest_feedback,
            feedback_aware_repair=False,
        )

    if normalized_mode == "auto-repair":
        return run_auto_repair(
            spec=spec,
            backtest_environment=backtest_environment,
            backtest_feedback=backtest_feedback,
            static_reviewer=static_reviewer,
            llm_reviewer=llm_reviewer,
            repair_planner=repair_planner,
            patcher=patcher,
            backend=backend,
            client_mode=client_mode,
            model=model,
            replay_path=replay_path,
        )

    raise ValueError(f"Unsupported review mode: {mode!r}")
