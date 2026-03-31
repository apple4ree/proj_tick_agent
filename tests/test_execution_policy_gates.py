"""Tests for execution-policy churn suppression.

Covers:
1. Static reviewer hard gates for churn-heavy execution policies
2. Generation prompt churn-avoidance content
3. LLM reviewer mock includes churn-related focus areas
4. Repair planner prioritizes execution policy relaxation
5. Patcher applies churn-reduction ops deterministically
6. Pipeline end-to-end: churn-heavy spec → repair → risk mitigated
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.ast_nodes import (
    ComparisonExpr,
    ConstExpr,
    PositionAttrExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    ExecutionPolicyV2,
    RiskPolicyV2,
    StrategySpecV2,
)
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_review.v2.contracts import RepairOperation, RepairPlan
from strategy_block.strategy_review.v2.patcher_v2 import StrategyRepairPatcherV2
from strategy_block.strategy_review.v2.llm_reviewer_v2 import LLMReviewerV2
from strategy_block.strategy_review.v2.repair_planner_v2 import RepairPlannerV2
from strategy_block.strategy_review.v2.pipeline_v2 import run_auto_repair
from strategy_block.strategy_generation.v2.utils.prompt_builder import (
    build_user_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec_with_execution(
    *,
    placement_mode: str = "passive_join",
    cancel_after_ticks: int = 0,
    max_reprices: int = 0,
    holding_ticks_threshold: float = 100.0,
    include_stop_loss: bool = True,
    include_time_exit: bool = True,
) -> StrategySpecV2:
    """Build a spec with configurable execution policy and exits."""
    exit_rules = []
    if include_stop_loss:
        exit_rules.append(ExitRuleV2(
            name="stop_loss",
            priority=1,
            condition=ComparisonExpr(
                left=PositionAttrExpr("unrealized_pnl_bps"),
                op="<=",
                threshold=-25.0,
            ),
            action=ExitActionV2(type="close_all"),
        ))
    if include_time_exit:
        exit_rules.append(ExitRuleV2(
            name="time_exit",
            priority=2,
            condition=ComparisonExpr(
                left=PositionAttrExpr("holding_ticks"),
                op=">=",
                threshold=holding_ticks_threshold,
            ),
            action=ExitActionV2(type="close_all"),
        ))
    if not exit_rules:
        # At least a market-based exit to avoid schema error
        exit_rules.append(ExitRuleV2(
            name="market_exit",
            priority=1,
            condition=ComparisonExpr(feature="spread_bps", op=">", threshold=50.0),
            action=ExitActionV2(type="close_all"),
        ))

    return StrategySpecV2(
        name="churn_test",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.5),
            ),
        ],
        exit_policies=[ExitPolicyV2(name="exits", rules=exit_rules)],
        execution_policy=ExecutionPolicyV2(
            placement_mode=placement_mode,
            cancel_after_ticks=cancel_after_ticks,
            max_reprices=max_reprices,
        ),
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def _review(spec, backtest_environment: dict | None = None):
    return StrategyReviewerV2().review(spec, backtest_environment=backtest_environment)


def _env_context(*, resample: str, tick_ms: float, submit_ms: float, cancel_ms: float) -> dict:
    return {
        "resample": resample,
        "canonical_tick_interval_ms": tick_ms,
        "market_data_delay_ms": 0.0,
        "decision_compute_ms": 0.0,
        "effective_delay_ms": 0.0,
        "latency": {
            "order_submit_ms": submit_ms,
            "order_ack_ms": 0.0,
            "cancel_ms": cancel_ms,
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


def _has_error(result, category: str) -> bool:
    return any(
        i.category == category and i.severity == "error"
        for i in result.issues
    )


def _has_warning(result, category: str) -> bool:
    return any(
        i.category == category and i.severity == "warning"
        for i in result.issues
    )


def _has_issue(result, category: str) -> bool:
    return any(i.category == category for i in result.issues)


# ===================================================================
# 1. Static reviewer: short-horizon + aggressive passive → error
# ===================================================================

class TestShortHorizonAggressivePassive:

    def test_short_horizon_high_reprices_passive_is_error(self):
        """horizon=10, passive_join, max_reprices=5 → execution_policy_too_aggressive."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=5,
            holding_ticks_threshold=10.0,
        )
        result = _review(spec)
        assert _has_error(result, "execution_policy_too_aggressive")

    def test_short_horizon_low_reprices_passive_no_error(self):
        """horizon=10, passive_join, max_reprices=2 → no error."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=2,
            holding_ticks_threshold=10.0,
        )
        result = _review(spec)
        assert not _has_error(result, "execution_policy_too_aggressive")

    def test_long_horizon_high_reprices_no_error(self):
        """horizon=100, passive_join, max_reprices=5 → no error (not short)."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=5,
            holding_ticks_threshold=100.0,
        )
        result = _review(spec)
        assert not _has_error(result, "execution_policy_too_aggressive")


# ===================================================================
# 2. Static reviewer: cancel_after_ticks too short
# ===================================================================

class TestCancelAfterTicksTooShort:

    def test_very_short_cancel_horizon_passive_short_strategy(self):
        """cancel_after_ticks=2, short horizon, passive → churn_risk_high error."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=2,
            max_reprices=1,
            holding_ticks_threshold=15.0,
        )
        result = _review(spec)
        assert _has_error(result, "churn_risk_high")

    def test_reasonable_cancel_horizon_no_error(self):
        """cancel_after_ticks=10 → no churn_risk_high error."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=1,
            holding_ticks_threshold=15.0,
        )
        result = _review(spec)
        assert not _has_error(result, "churn_risk_high")


# ===================================================================
# 3. Static reviewer: max_reprices excessively large
# ===================================================================

class TestMaxRepricesTooLarge:

    def test_very_large_max_reprices_warning(self):
        """max_reprices=15 → churn_risk_high warning."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=20,
            max_reprices=15,
            holding_ticks_threshold=100.0,
        )
        result = _review(spec)
        assert _has_warning(result, "churn_risk_high")

    def test_moderate_reprices_no_warning(self):
        """max_reprices=5 → no churn_risk_high warning."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=20,
            max_reprices=5,
            holding_ticks_threshold=100.0,
        )
        result = _review(spec)
        assert not _has_warning(result, "churn_risk_high")


# ===================================================================
# 4. Static reviewer: short-horizon without robust exit
# ===================================================================

class TestShortHorizonMissingRobustExit:

    def test_short_horizon_only_time_exit_no_stop_loss_passes(self):
        """Short horizon with time exit but no stop-loss is still robust (time exit is robust)."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=1,
            holding_ticks_threshold=10.0,
            include_stop_loss=False,
            include_time_exit=True,
        )
        result = _review(spec)
        # Time exit IS a robust close_all, so no error
        assert not _has_error(result, "missing_robust_exit_for_short_horizon")

    def test_short_horizon_with_both_exits_no_error(self):
        """Short horizon with stop-loss + time exit → no missing_robust_exit error."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=1,
            holding_ticks_threshold=10.0,
            include_stop_loss=True,
            include_time_exit=True,
        )
        result = _review(spec)
        assert not _has_error(result, "missing_robust_exit_for_short_horizon")

    def test_no_time_exit_means_no_inferred_horizon(self):
        """Without a time exit, no horizon is inferred, so no short-horizon checks fire."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=1,
            holding_ticks_threshold=10.0,
            include_stop_loss=False,
            include_time_exit=False,
        )
        result = _review(spec)
        # No time exit → no inferred horizon → no short-horizon-specific checks
        assert not _has_error(result, "missing_robust_exit_for_short_horizon")


# ===================================================================
# 5. Static reviewer: passive mode without cancel timeout
# ===================================================================

class TestPassiveNoCancelTimeout:

    def test_passive_no_cancel_timeout_warning(self):
        """passive_join with cancel_after_ticks=0 → queue_latency_mismatch warning."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=0,
            max_reprices=1,
            holding_ticks_threshold=100.0,
        )
        result = _review(spec)
        assert _has_warning(result, "queue_latency_mismatch")

    def test_passive_with_cancel_timeout_no_warning(self):
        """passive_join with cancel_after_ticks=20 → no queue_latency_mismatch."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=20,
            max_reprices=1,
            holding_ticks_threshold=100.0,
        )
        result = _review(spec)
        assert not _has_warning(result, "queue_latency_mismatch")


# ===================================================================
# 6. Static reviewer: ultra-short horizon passive repricing → error
# ===================================================================

class TestUltraShortHorizon:

    def test_horizon_1_passive_repricing_error(self):
        """horizon=1, passive, max_reprices=2 → execution_policy_too_aggressive."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=2,
            holding_ticks_threshold=1.0,
        )
        result = _review(spec)
        assert _has_error(result, "execution_policy_too_aggressive")

    def test_horizon_3_passive_repricing_error(self):
        """horizon=3, passive, max_reprices=2 → execution_policy_too_aggressive."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=2,
            holding_ticks_threshold=3.0,
        )
        result = _review(spec)
        assert _has_error(result, "execution_policy_too_aggressive")


# ===================================================================
# 7. Generation prompt includes churn-avoidance content
# ===================================================================

class TestGenerationPromptChurnAvoidance:

    def test_prompt_contains_churn_avoidance_phrases(self):
        prompt = build_user_prompt(
            research_goal="test",
            backtest_environment={
                "resample": "1s",
                "canonical_tick_interval_ms": 1000.0,
                "market_data_delay_ms": 50.0,
                "decision_compute_ms": 10.0,
                "effective_delay_ms": 60.0,
                "latency": {"order_submit_ms": 100.0, "order_ack_ms": 50.0, "cancel_ms": 80.0},
                "queue": {"queue_model": "pro_rata", "queue_position_assumption": 0.5},
                "semantics": {
                    "submit_latency_gating": True,
                    "cancel_latency_gating": True,
                    "replace_model": "minimal_immediate",
                },
            },
        )
        assert "Prefer low-churn execution policies" in prompt
        assert "cancel/repost loops" in prompt
        assert "repricing bounded" in prompt
        assert "passive fills" in prompt

    def test_prompt_contains_friction_summary(self):
        prompt = build_user_prompt(
            research_goal="test",
            backtest_environment={
                "resample": "1s",
                "canonical_tick_interval_ms": 1000.0,
                "latency": {"order_submit_ms": 600.0, "cancel_ms": 400.0},
                "queue": {"queue_model": "fifo", "queue_position_assumption": 0.5},
                "semantics": {"replace_model": "minimal_immediate"},
            },
        )
        assert "Backtest constraint summary (canonical)" in prompt
        assert "tick = resample step" in prompt
        assert "submit/cancel latency compounds churn cost" in prompt
        assert "replace is minimal immediate, not staged venue replace" in prompt

    def test_prompt_without_environment_still_has_churn_rules(self):
        """Even without backtest_environment, the template has churn-avoidance rules."""
        prompt = build_user_prompt(research_goal="test")
        assert "Prefer low-churn execution policies" in prompt


# ===================================================================
# 8. LLM reviewer mock includes churn focus areas
# ===================================================================

class TestLLMReviewerChurnFocusAreas:

    def test_mock_includes_churn_focus_when_static_has_churn_issues(self):
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=2,
            max_reprices=5,
            holding_ticks_threshold=10.0,
        )
        static_review = _review(spec)
        reviewer = LLMReviewerV2(client_mode="mock")
        report = reviewer.review(spec=spec, static_review=static_review)

        assert "churn_risk" in report.focus_areas or "queue_latency_risk" in report.focus_areas

    def test_mock_includes_execution_policy_focus(self):
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=5,
            holding_ticks_threshold=100.0,
        )
        static_review = _review(spec)
        reviewer = LLMReviewerV2(client_mode="mock")
        report = reviewer.review(spec=spec, static_review=static_review)

        assert "execution_policy" in report.focus_areas


# ===================================================================
# 9. Repair planner prioritizes execution policy relaxation
# ===================================================================

class TestRepairPlannerPriority:

    def test_churn_ops_come_first_in_mock_plan(self):
        """When churn issues exist, cancel/repricing ops should precede exit ops."""
        # Short horizon + aggressive execution → churn errors
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=2,
            max_reprices=5,
            holding_ticks_threshold=10.0,
            include_stop_loss=True,
            include_time_exit=True,
        )
        static_review = _review(spec)
        # Verify churn issues are actually detected
        assert any(
            i.category in ("execution_policy_too_aggressive", "churn_risk_high")
            for i in static_review.issues
        ), f"Expected churn issues but got: {[i.category for i in static_review.issues]}"

        reviewer = LLMReviewerV2(client_mode="mock")
        llm_review = reviewer.review(spec=spec, static_review=static_review)
        planner = RepairPlannerV2(client_mode="mock")
        plan = planner.plan(
            spec=spec,
            static_review=static_review,
            llm_review=llm_review,
        )

        op_names = [op.op for op in plan.operations]
        # set_cancel_after_ticks and set_max_reprices must appear
        assert "set_cancel_after_ticks" in op_names
        assert "set_max_reprices" in op_names

        # They should come before exit ops (if any)
        if "add_stop_loss_exit" in op_names:
            cancel_idx = op_names.index("set_cancel_after_ticks")
            stop_idx = op_names.index("add_stop_loss_exit")
            assert cancel_idx < stop_idx

    def test_plan_expected_effect_mentions_churn(self):
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=2,
            max_reprices=5,
            holding_ticks_threshold=10.0,
        )
        static_review = _review(spec)
        reviewer = LLMReviewerV2(client_mode="mock")
        llm_review = reviewer.review(spec=spec, static_review=static_review)
        planner = RepairPlannerV2(client_mode="mock")
        plan = planner.plan(
            spec=spec,
            static_review=static_review,
            llm_review=llm_review,
        )

        assert "churn" in plan.expected_effect.lower()


# ===================================================================
# 10. Patcher applies churn-reduction ops deterministically
# ===================================================================

class TestPatcherChurnReduction:

    def test_patcher_increases_cancel_after_ticks(self):
        spec = _spec_with_execution(
            cancel_after_ticks=2,
            max_reprices=5,
            holding_ticks_threshold=10.0,
        )
        plan = RepairPlan(
            summary="reduce churn",
            operations=[
                RepairOperation(op="set_cancel_after_ticks", target="execution_policy", value=20, reason="x"),
            ],
        )
        patched = StrategyRepairPatcherV2().apply(spec, plan)
        assert patched.execution_policy.cancel_after_ticks == 20

    def test_patcher_decreases_max_reprices(self):
        spec = _spec_with_execution(
            cancel_after_ticks=10,
            max_reprices=10,
            holding_ticks_threshold=100.0,
        )
        plan = RepairPlan(
            summary="reduce churn",
            operations=[
                RepairOperation(op="set_max_reprices", target="execution_policy", value=3, reason="x"),
            ],
        )
        patched = StrategyRepairPatcherV2().apply(spec, plan)
        assert patched.execution_policy.max_reprices == 3

    def test_patcher_summary_helper(self):
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=20,
            max_reprices=3,
            holding_ticks_threshold=100.0,
        )
        summary = StrategyRepairPatcherV2.execution_policy_summary(spec)
        assert summary["placement_mode"] == "passive_join"
        assert summary["cancel_after_ticks"] == 20
        assert summary["max_reprices"] == 3
        assert summary["repricing_budget"] == 3
        assert summary["has_time_exit"] is True
        assert summary["has_stop_loss_exit"] is True
        assert summary["inferred_holding_horizon"] == 100


# ===================================================================
# 11. Pipeline end-to-end: churn-heavy → repair → mitigated
# ===================================================================

class TestPipelineChurnRepair:

    def test_churn_heavy_spec_repaired_and_re_reviewed(self):
        """A churn-heavy spec should be repaired with execution policy relaxation
        and the final static re-review should show reduced risk."""
        # Use a spec with time exit (so horizon is inferred as short)
        # and aggressive execution params → triggers churn errors
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=2,
            max_reprices=5,
            holding_ticks_threshold=10.0,
            include_stop_loss=True,
            include_time_exit=True,
        )
        result = run_auto_repair(
            spec=spec,
            backtest_environment={},
            client_mode="mock",
        )

        assert result.repair_applied is True
        assert result.repair_plan is not None
        op_names = [op.op for op in result.repair_plan.operations]
        assert "set_cancel_after_ticks" in op_names or "set_max_reprices" in op_names

        # Final review should have reduced churn errors
        if result.repaired_spec is not None:
            repaired = StrategySpecV2.from_dict(result.repaired_spec)
            repaired_review = _review(repaired)
            # The repaired spec should not have the original churn error
            assert not _has_error(repaired_review, "churn_risk_high")


# ===================================================================
# 12. Regression: valid specs still pass
# ===================================================================

class TestValidSpecsStillPass:

    def test_spec_without_execution_policy_passes_with_implicit_risk_warning(self):
        """Spec with no execution policy should pass hard gates but emit implicit execution-risk warning."""
        spec = StrategySpecV2(
            name="no_exec_policy",
            entry_policies=[
                EntryPolicyV2(
                    name="long_entry",
                    side="long",
                    trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                    strength=ConstExpr(value=0.5),
                ),
            ],
            exit_policies=[ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="stop_loss",
                    priority=1,
                    condition=ComparisonExpr(
                        left=PositionAttrExpr("unrealized_pnl_bps"),
                        op="<=",
                        threshold=-25.0,
                    ),
                    action=ExitActionV2(type="close_all"),
                ),
            ])],
            risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
        )
        result = _review(spec)
        assert not _has_error(result, "execution_policy_too_aggressive")
        assert not _has_error(result, "churn_risk_high")
        assert not _has_warning(result, "queue_latency_mismatch")
        assert _has_warning(result, "execution_policy_implicit_risk")

    def test_conservative_execution_policy_passes(self):
        """Conservative execution policy should pass all churn checks."""
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=20,
            max_reprices=2,
            holding_ticks_threshold=100.0,
        )
        result = _review(spec)
        assert not _has_error(result, "execution_policy_too_aggressive")
        assert not _has_error(result, "churn_risk_high")
        assert not _has_error(result, "missing_robust_exit_for_short_horizon")


# ===================================================================
# 13. Env-aware deterministic severity adjustment
# ===================================================================

class TestEnvAwareExecutionPolicySeverity:

    def test_same_cancel_ticks_can_fail_in_500ms_but_not_1s(self):
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=5,
            max_reprices=1,
            holding_ticks_threshold=10.0,
        )

        result_1s = _review(
            spec,
            backtest_environment=_env_context(
                resample="1s",
                tick_ms=1000.0,
                submit_ms=50.0,
                cancel_ms=50.0,
            ),
        )
        result_500ms = _review(
            spec,
            backtest_environment=_env_context(
                resample="500ms",
                tick_ms=500.0,
                submit_ms=50.0,
                cancel_ms=50.0,
            ),
        )

        assert not _has_error(result_1s, "churn_risk_high")
        assert _has_error(result_500ms, "churn_risk_high")

    def test_high_latency_to_tick_ratio_tightens_passive_repricing_budget(self):
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=3,
            holding_ticks_threshold=10.0,
        )

        result_low_ratio = _review(
            spec,
            backtest_environment=_env_context(
                resample="1s",
                tick_ms=1000.0,
                submit_ms=60.0,
                cancel_ms=40.0,
            ),
        )
        result_high_ratio = _review(
            spec,
            backtest_environment=_env_context(
                resample="500ms",
                tick_ms=500.0,
                submit_ms=300.0,
                cancel_ms=200.0,
            ),
        )

        assert not _has_error(result_low_ratio, "execution_policy_too_aggressive")
        assert _has_error(result_high_ratio, "execution_policy_too_aggressive")

    def test_without_env_fallback_is_kept(self):
        spec = _spec_with_execution(
            placement_mode="passive_join",
            cancel_after_ticks=5,
            max_reprices=1,
            holding_ticks_threshold=10.0,
        )
        result = _review(spec)
        assert not _has_error(result, "churn_risk_high")
