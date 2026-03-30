"""Environment-aware LLM semantic reviewer (v2)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient
from strategy_block.strategy_review.review_common import ReviewResult
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2

from .contracts import BacktestFeedbackSummary, LLMReviewIssue, LLMReviewReport
from .llm_prompt_builder import build_llm_review_prompt


class LLMReviewerV2:
    """LLM semantic reviewer with structured output contract.

    This reviewer is advisory only. Final pass/fail remains static review.
    """

    def __init__(
        self,
        *,
        backend: str = "openai",
        client_mode: str = "mock",
        model: str | None = None,
        replay_path: Path | str | None = None,
    ) -> None:
        if backend != "openai":
            raise ValueError(f"Unsupported backend: {backend!r}")
        self.backend = backend
        self.client = OpenAIStrategyGenClient(
            mode=client_mode,
            model=model,
            replay_path=replay_path,
        )
        self.last_query_meta: dict[str, Any] = {
            "mode": client_mode,
            "status": "not_called",
            "reason": "",
        }

    def review(
        self,
        *,
        spec: StrategySpecV2,
        static_review: ReviewResult,
        backtest_environment: dict[str, Any] | None = None,
        backtest_feedback: BacktestFeedbackSummary | None = None,
    ) -> LLMReviewReport:
        system_prompt, user_prompt = build_llm_review_prompt(
            spec=spec,
            static_review=static_review,
            backtest_environment=backtest_environment,
            backtest_feedback=backtest_feedback,
        )

        response = self.client.query_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=LLMReviewReport,
            mock_factory=lambda: self._build_mock_report(
                spec=spec,
                static_review=static_review,
                backtest_feedback=backtest_feedback,
            ),
        )
        self.last_query_meta = dict(self.client.last_query_meta)
        if response is not None:
            return response

        # Defensive fallback when live/replay yields no structured output.
        return self._build_mock_report(
            spec=spec,
            static_review=static_review,
            backtest_feedback=backtest_feedback,
        )

    def _build_mock_report(
        self,
        *,
        spec: StrategySpecV2,
        static_review: ReviewResult,
        backtest_feedback: BacktestFeedbackSummary | None = None,
    ) -> LLMReviewReport:
        static_errors = [i for i in static_review.issues if i.severity == "error"]
        static_warnings = [i for i in static_review.issues if i.severity == "warning"]

        issues: list[LLMReviewIssue] = []
        for issue in static_errors[:3]:
            issues.append(LLMReviewIssue(
                severity="high_risk",
                category=issue.category,
                description=issue.description,
                rationale="Static hard-gate error indicates immediate execution risk.",
                suggested_fix=issue.suggestion or "Address this static error before deployment.",
            ))
        if not static_errors:
            for issue in static_warnings[:3]:
                issues.append(LLMReviewIssue(
                    severity="warning",
                    category=issue.category,
                    description=issue.description,
                    rationale="Warning may degrade robustness under realistic microstructure.",
                    suggested_fix=issue.suggestion or "Review and tighten this logic.",
                ))

        if not issues:
            issues.append(LLMReviewIssue(
                severity="info",
                category="semantic_review",
                description="No critical semantic risks detected in mock review mode.",
                rationale="Static reviewer passed without warning-level blockers.",
                suggested_fix="Keep validating with out-of-sample backtests.",
            ))

        if static_errors:
            overall: str = "high_risk"
            summary = (
                f"Static review has {len(static_errors)} error(s); "
                "semantic revision is required before use."
            )
        elif static_warnings:
            overall = "revise_recommended"
            summary = (
                f"Static review passed with {len(static_warnings)} warning(s); "
                "targeted revisions are recommended."
            )
        else:
            overall = "pass_with_notes"
            summary = "Static review passed; only minor semantic notes were found."

        focus_areas: list[str] = []
        for issue in issues:
            if issue.category not in focus_areas:
                focus_areas.append(issue.category)
        if spec.execution_policy is not None and "execution_policy" not in focus_areas:
            focus_areas.append("execution_policy")
        if "risk_policy" not in focus_areas:
            focus_areas.append("risk_policy")

        # Add churn-related focus areas when execution policy has churn risk indicators
        churn_categories = {
            "execution_policy_too_aggressive", "churn_risk_high",
            "queue_latency_mismatch", "missing_robust_exit_for_short_horizon",
        }
        has_churn_issue = any(i.category in churn_categories for i in static_review.issues)
        if has_churn_issue or (
            spec.execution_policy is not None
            and spec.execution_policy.max_reprices > 3
        ):
            for area in ("churn_risk", "queue_latency_risk"):
                if area not in focus_areas:
                    focus_areas.append(area)

        if backtest_feedback is not None and backtest_feedback.feedback_available:
            if "backtest_feedback" not in focus_areas:
                focus_areas.append("backtest_feedback")
            flags = backtest_feedback.flags
            if flags.churn_heavy and "churn_risk" not in focus_areas:
                focus_areas.append("churn_risk")
            if flags.queue_ineffective and "queue_latency_risk" not in focus_areas:
                focus_areas.append("queue_latency_risk")
            if flags.cost_dominated and "cost_risk" not in focus_areas:
                focus_areas.append("cost_risk")

        return LLMReviewReport(
            overall_assessment=overall,
            summary=summary,
            issues=issues,
            repair_recommended=bool(static_errors or static_warnings),
            focus_areas=focus_areas,
        )
