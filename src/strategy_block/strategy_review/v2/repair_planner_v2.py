"""Constrained repair planner for StrategySpecV2."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient
from strategy_block.strategy_review.review_common import ReviewResult
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2

from .contracts import BacktestFeedbackSummary, LLMReviewReport, RepairOperation, RepairPlan
from .llm_prompt_builder import build_repair_prompt


class RepairPlannerV2:
    """Builds structured repair plans.

    Planner proposes operations only. It does not mutate specs.
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

    def plan(
        self,
        *,
        spec: StrategySpecV2,
        static_review: ReviewResult,
        llm_review: LLMReviewReport,
        backtest_environment: dict[str, Any] | None = None,
        backtest_feedback: BacktestFeedbackSummary | None = None,
    ) -> RepairPlan:
        system_prompt, user_prompt = build_repair_prompt(
            spec=spec,
            static_review=static_review,
            llm_review=llm_review,
            backtest_environment=backtest_environment,
            backtest_feedback=backtest_feedback,
        )

        response = self.client.query_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=RepairPlan,
            mock_factory=lambda: self._build_mock_plan(
                static_review=static_review,
                llm_review=llm_review,
            ),
        )
        self.last_query_meta = dict(self.client.last_query_meta)
        base_plan = response if response is not None else self._build_mock_plan(
            static_review=static_review,
            llm_review=llm_review,
        )
        return self._apply_feedback_priority(
            plan=base_plan,
            static_review=static_review,
            backtest_feedback=backtest_feedback,
        )

    # Repair priority order: execution policy churn reduction first,
    # then exits, then risk, then general.
    # This ensures auto-repair reduces churn before touching strategy logic.
    _CHURN_CATEGORIES: frozenset[str] = frozenset({
        "execution_policy_too_aggressive",
        "churn_risk_high",
        "queue_latency_mismatch",
        "missing_robust_exit_for_short_horizon",
        "missing_execution_policy_for_short_horizon",
        "execution_policy_implicit_risk",
    })

    _PATTERN_ORDER: tuple[str, ...] = (
        "churn_heavy",
        "queue_ineffective",
        "cost_dominated",
        "adverse_selection_dominated",
    )

    def _op(
        self,
        *,
        op: str,
        value: Any,
        reason: str,
        target: str = "execution_policy",
    ) -> RepairOperation:
        return RepairOperation(op=op, target=target, value=value, reason=reason)

    def _dedupe_ops(self, ops: list[RepairOperation]) -> list[RepairOperation]:
        deduped: list[RepairOperation] = []
        seen: set[tuple[str, str]] = set()
        for op in ops:
            key = (op.op, op.target)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(op)
        return deduped

    def _error_safety_ops(self, static_review: ReviewResult) -> list[RepairOperation]:
        error_categories = {i.category for i in static_review.issues if i.severity == "error"}
        ops: list[RepairOperation] = []

        if "missing_execution_policy_for_short_horizon" in error_categories:
            ops.extend([
                self._op(
                    op="set_placement_mode",
                    value="passive_join",
                    reason="Hard safety fix: insert explicit execution policy placement mode.",
                ),
                self._op(
                    op="set_cancel_after_ticks",
                    value=15,
                    reason="Hard safety fix: set bounded cancel horizon for missing execution policy.",
                ),
                self._op(
                    op="set_max_reprices",
                    value=2,
                    reason="Hard safety fix: set bounded repricing budget for missing execution policy.",
                ),
            ])

        if "execution_policy_too_aggressive" in error_categories:
            ops.extend([
                self._op(
                    op="set_cancel_after_ticks",
                    value=20,
                    reason="Hard safety fix: extend cancel horizon for overly aggressive execution policy.",
                ),
                self._op(
                    op="set_max_reprices",
                    value=2,
                    reason="Hard safety fix: reduce repricing budget for overly aggressive policy.",
                ),
                self._op(
                    op="set_placement_mode",
                    value="passive_join",
                    reason="Hard safety fix: avoid aggressive crossing under high churn risk.",
                ),
            ])

        if (
            "missing_robust_exit_for_short_horizon" in error_categories
            or "exit_completeness" in error_categories
            or "exit_semantics_risk" in error_categories
            or "regime_exit_coverage" in error_categories
        ):
            ops.extend([
                self._op(
                    op="set_holding_ticks",
                    target="primary_exit_policy",
                    value=30,
                    reason="Hard safety fix: enforce minimum holding horizon before exit.",
                ),
                self._op(
                    op="add_time_exit",
                    target="primary_exit_policy",
                    value={"holding_ticks": 120},
                    reason="Hard safety fix: add bounded time exit.",
                ),
                self._op(
                    op="add_stop_loss_exit",
                    target="primary_exit_policy",
                    value={"threshold_bps": -25.0},
                    reason="Hard safety fix: add close_all stop-loss fail-safe.",
                ),
            ])

        if "risk_inconsistency" in error_categories:
            ops.append(
                self._op(
                    op="tighten_inventory_cap",
                    target="risk_policy",
                    value={"factor": 0.8},
                    reason="Hard safety fix: tighten inventory cap to restore risk consistency.",
                )
            )

        return self._dedupe_ops(ops)

    def _build_baseline_ops(
        self,
        *,
        static_review: ReviewResult,
        llm_review: LLMReviewReport,
    ) -> list[RepairOperation]:
        categories = {i.category for i in static_review.issues}
        ops: list[RepairOperation] = []

        # Priority 0: Insert conservative execution policy if missing.
        if (
            "missing_execution_policy_for_short_horizon" in categories
            or "execution_policy_implicit_risk" in categories
        ):
            ops.extend([
                self._op(
                    op="set_placement_mode",
                    value="passive_join",
                    reason="Add explicit execution policy with conservative placement mode.",
                ),
                self._op(
                    op="set_cancel_after_ticks",
                    value=15,
                    reason="Set bounded cancel horizon for missing execution policy.",
                ),
                self._op(
                    op="set_max_reprices",
                    value=2,
                    reason="Set conservative repricing budget for missing execution policy.",
                ),
            ])

        # Priority 1-2: Execution policy churn reduction first.
        if (
            "churn_risk_high" in categories
            or "queue_latency_mismatch" in categories
            or "latency_structure_warning" in categories
        ):
            ops.append(
                self._op(
                    op="set_cancel_after_ticks",
                    value=20,
                    reason="Extend cancel horizon to reduce churn under queue/latency friction.",
                )
            )

        if (
            "execution_policy_too_aggressive" in categories
            or "churn_risk_high" in categories
            or "execution_risk_mismatch" in categories
        ):
            ops.append(
                self._op(
                    op="set_max_reprices",
                    value=3,
                    reason="Bound repricing to reduce execution churn.",
                )
            )

        if "execution_policy_too_aggressive" in categories:
            ops.append(
                self._op(
                    op="set_placement_mode",
                    value="passive_join",
                    reason="Use standard passive placement instead of aggressive mode.",
                )
            )

        if "missing_robust_exit_for_short_horizon" in categories:
            ops.append(
                self._op(
                    op="set_holding_ticks",
                    target="primary_exit_policy",
                    value=30,
                    reason="Ensure minimum holding horizon for short-horizon strategy.",
                )
            )

        if (
            "exit_completeness" in categories
            or "exit_semantics_risk" in categories
            or "regime_exit_coverage" in categories
            or "missing_robust_exit_for_short_horizon" in categories
        ):
            ops.extend([
                self._op(
                    op="add_stop_loss_exit",
                    target="primary_exit_policy",
                    value={"threshold_bps": -25.0},
                    reason="Add robust close_all fail-safe stop-loss exit.",
                ),
                self._op(
                    op="add_time_exit",
                    target="primary_exit_policy",
                    value={"holding_ticks": 120},
                    reason="Add bounded time-based close_all exit.",
                ),
            ])

        if "risk_inconsistency" in categories:
            ops.append(
                self._op(
                    op="tighten_inventory_cap",
                    target="risk_policy",
                    value={"factor": 0.8},
                    reason="Reduce inventory cap to stabilize risk posture.",
                )
            )

        if not ops and llm_review.repair_recommended:
            ops.append(
                self._op(
                    op="set_max_reprices",
                    value=2,
                    reason="Conservative default tweak from semantic review.",
                )
            )

        return self._dedupe_ops(ops)

    def _detect_feedback_patterns(self, backtest_feedback: BacktestFeedbackSummary | None) -> list[str]:
        if backtest_feedback is None or not backtest_feedback.feedback_available:
            return []

        lifecycle = backtest_feedback.lifecycle
        queue = backtest_feedback.queue
        cancel = backtest_feedback.cancel_reasons
        cost = backtest_feedback.cost
        flags = backtest_feedback.flags

        patterns: list[str] = []

        churn_heavy = bool(
            flags.churn_heavy
            or ((lifecycle.children_per_parent or 0.0) >= 8.0)
            or ((lifecycle.cancel_rate or 0.0) >= 0.75)
            or ((lifecycle.max_children_per_parent or 0.0) >= 75.0)
        )
        if churn_heavy:
            patterns.append("churn_heavy")

        queue_ineffective = bool(
            flags.queue_ineffective
            or (
                (queue.maker_fill_ratio is not None and queue.maker_fill_ratio <= 0.05)
                and ((queue.blocked_miss_count or 0.0) > 0.0 or (queue.queue_blocked_count or 0.0) > 0.0)
            )
            or (
                (queue.queue_ready_count is not None and queue.queue_ready_count <= 0.0)
                and (queue.queue_blocked_count or 0.0) >= 20.0
            )
        )
        if queue_ineffective:
            patterns.append("queue_ineffective")

        cost_total = abs(cost.total_commission or 0.0) + abs(cost.total_slippage or 0.0) + abs(cost.total_impact or 0.0)
        cost_dominated = bool(
            flags.cost_dominated
            or (
                cost.net_pnl is not None
                and cost.net_pnl <= 0.0
                and cost_total >= max(1.0, abs(cost.net_pnl) * 0.5)
            )
        )
        if cost_dominated:
            patterns.append("cost_dominated")

        adverse_selection_dominated = bool(
            flags.adverse_selection_dominated
            or ((cancel.adverse_selection_share or 0.0) >= 0.60)
        )
        if adverse_selection_dominated:
            patterns.append("adverse_selection_dominated")

        return patterns

    def _feedback_matrix_ops(self, patterns: list[str]) -> list[RepairOperation]:
        ops: list[RepairOperation] = []
        active = set(patterns)

        # Composition rules first.
        if "churn_heavy" in active and "adverse_selection_dominated" in active:
            ops.extend([
                self._op(
                    op="set_cancel_after_ticks",
                    value=20,
                    reason="Feedback composite(churn+adverse): extend cancel horizon before alpha changes.",
                ),
                self._op(
                    op="set_max_reprices",
                    value=1,
                    reason="Feedback composite(churn+adverse): aggressively bound repricing budget.",
                ),
            ])

        if "queue_ineffective" in active and "cost_dominated" in active:
            ops.extend([
                self._op(
                    op="set_placement_mode",
                    value="adaptive",
                    reason="Feedback composite(queue+cost): avoid passive-only loops when queue edge is poor.",
                ),
                self._op(
                    op="set_base_size",
                    target="risk_policy",
                    value=25,
                    reason="Feedback composite(queue+cost): reduce turnover via smaller base size.",
                ),
                self._op(
                    op="set_max_size",
                    target="risk_policy",
                    value=100,
                    reason="Feedback composite(queue+cost): cap size to limit trading costs.",
                ),
            ])

        # Pattern matrix.
        for pattern in self._PATTERN_ORDER:
            if pattern not in active:
                continue
            if pattern == "churn_heavy":
                ops.extend([
                    self._op(
                        op="set_cancel_after_ticks",
                        value=20,
                        reason="Feedback churn_heavy: reduce cancel/repost loop intensity.",
                    ),
                    self._op(
                        op="set_max_reprices",
                        value=2,
                        reason="Feedback churn_heavy: bound repricing cycles.",
                    ),
                    self._op(
                        op="set_placement_mode",
                        value="passive_join",
                        reason="Feedback churn_heavy: use less loop-prone passive join baseline.",
                    ),
                    self._op(
                        op="set_holding_ticks",
                        target="primary_exit_policy",
                        value=45,
                        reason="Feedback churn_heavy: extend hold horizon to lower churn pressure.",
                    ),
                    self._op(
                        op="add_time_exit",
                        target="primary_exit_policy",
                        value={"holding_ticks": 120},
                        reason="Feedback churn_heavy: add bounded time exit fail-safe.",
                    ),
                    self._op(
                        op="add_stop_loss_exit",
                        target="primary_exit_policy",
                        value={"threshold_bps": -25.0},
                        reason="Feedback churn_heavy: add stop-loss fail-safe.",
                    ),
                ])

            elif pattern == "queue_ineffective":
                ops.extend([
                    self._op(
                        op="set_placement_mode",
                        value="adaptive",
                        reason="Feedback queue_ineffective: do not keep passive repricing loops when queue edge is poor.",
                    ),
                    self._op(
                        op="set_cancel_after_ticks",
                        value=25,
                        reason="Feedback queue_ineffective: increase quote persistence to avoid queue resets.",
                    ),
                    self._op(
                        op="set_max_reprices",
                        value=1,
                        reason="Feedback queue_ineffective: tighten repricing budget.",
                    ),
                    self._op(
                        op="set_holding_ticks",
                        target="primary_exit_policy",
                        value=60,
                        reason="Feedback queue_ineffective: allow more time for fills before cancel/replace.",
                    ),
                ])

            elif pattern == "cost_dominated":
                ops.extend([
                    self._op(
                        op="set_base_size",
                        target="risk_policy",
                        value=25,
                        reason="Feedback cost_dominated: reduce turnover via smaller base size.",
                    ),
                    self._op(
                        op="set_max_size",
                        target="risk_policy",
                        value=100,
                        reason="Feedback cost_dominated: limit max size under high execution costs.",
                    ),
                    self._op(
                        op="tighten_inventory_cap",
                        target="risk_policy",
                        value={"factor": 0.7},
                        reason="Feedback cost_dominated: tighten inventory cap to curb cost-heavy exposure.",
                    ),
                    self._op(
                        op="set_cancel_after_ticks",
                        value=25,
                        reason="Feedback cost_dominated: reduce quote churn that drives commission/slippage.",
                    ),
                    self._op(
                        op="set_max_reprices",
                        value=1,
                        reason="Feedback cost_dominated: reduce repricing-induced turnover.",
                    ),
                ])

            elif pattern == "adverse_selection_dominated":
                ops.extend([
                    self._op(
                        op="set_cancel_after_ticks",
                        value=20,
                        reason="Feedback adverse_selection_dominated: extend cancel horizon to avoid stale repost loops.",
                    ),
                    self._op(
                        op="set_max_reprices",
                        value=1,
                        reason="Feedback adverse_selection_dominated: sharply bound repricing budget.",
                    ),
                    self._op(
                        op="set_placement_mode",
                        value="passive_join",
                        reason="Feedback adverse_selection_dominated: avoid aggressive stale quoting behavior.",
                    ),
                    self._op(
                        op="set_holding_ticks",
                        target="primary_exit_policy",
                        value=45,
                        reason="Feedback adverse_selection_dominated: increase holding horizon to reduce micro-churn.",
                    ),
                ])

        return self._dedupe_ops(ops)

    def _apply_feedback_priority(
        self,
        *,
        plan: RepairPlan,
        static_review: ReviewResult,
        backtest_feedback: BacktestFeedbackSummary | None,
    ) -> RepairPlan:
        patterns = self._detect_feedback_patterns(backtest_feedback)
        if not patterns:
            return plan

        safety_ops = self._error_safety_ops(static_review)
        feedback_ops = self._feedback_matrix_ops(patterns)
        combined = self._dedupe_ops(safety_ops + feedback_ops + list(plan.operations))

        # Keep the final plan compact in feedback mode.
        max_ops = 6 if len(safety_ops) <= 6 else len(safety_ops)
        combined = combined[:max_ops]

        pattern_text = ", ".join(patterns)
        summary = (
            "Feedback-aware constrained repair plan: prioritize minimal changes that directly target "
            f"observed failure patterns [{pattern_text}] before alpha rewrites."
        )
        expected_effect = (
            "Reduce observed churn/queue/cost/adverse-selection failure modes with deterministic "
            "execution/risk adjustments first, while preserving static hard-gate safety constraints."
        )

        return RepairPlan(
            summary=summary,
            operations=combined,
            expected_effect=expected_effect,
            requires_manual_followup=bool(not combined),
        )

    def _build_mock_plan(
        self,
        *,
        static_review: ReviewResult,
        llm_review: LLMReviewReport,
    ) -> RepairPlan:
        baseline_ops = self._build_baseline_ops(
            static_review=static_review,
            llm_review=llm_review,
        )

        return RepairPlan(
            summary=(
                "Constrained repair plan: execution policy churn reduction first, "
                "then robust exits and risk adjustments."
            ),
            operations=baseline_ops,
            expected_effect=(
                "Reduce execution churn and hard-gate errors without rewriting "
                "strategy structure. Prefer minimal changes that reduce churn risk "
                "before changing the alpha logic."
            ),
            requires_manual_followup=bool(not baseline_ops),
        )
