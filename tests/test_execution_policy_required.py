"""Tests for execution_policy presence enforcement.

Covers:
1. Generation prompt includes "do not omit execution_policy" rules
2. Mock plan includes explicit execution_policy
3. Lowering marks execution_policy_explicit metadata
4. Reviewer catches short-horizon + no execution_policy → error
5. Reviewer warns on non-short-horizon + no execution_policy
6. Long-horizon + no execution_policy is not over-penalized
7. Repair planner inserts conservative execution_policy when missing
8. Patcher can insert execution_policy on None spec
9. Pipeline end-to-end: missing EP → repair → EP inserted
10. Regression: specs with explicit execution_policy still pass
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

def _spec_no_ep(holding_ticks_threshold: float = 100.0) -> StrategySpecV2:
    """Build a spec with NO execution_policy and a configurable holding horizon."""
    return StrategySpecV2(
        name="no_ep_test",
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
            ExitRuleV2(
                name="time_exit",
                priority=2,
                condition=ComparisonExpr(
                    left=PositionAttrExpr("holding_ticks"),
                    op=">=",
                    threshold=holding_ticks_threshold,
                ),
                action=ExitActionV2(type="close_all"),
            ),
        ])],
        execution_policy=None,
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def _spec_with_ep(holding_ticks_threshold: float = 100.0) -> StrategySpecV2:
    """Build a spec WITH explicit execution_policy."""
    spec = _spec_no_ep(holding_ticks_threshold)
    spec.execution_policy = ExecutionPolicyV2(
        placement_mode="passive_join",
        cancel_after_ticks=15,
        max_reprices=2,
    )
    return spec


def _review(spec, backtest_environment: dict | None = None):
    return StrategyReviewerV2().review(spec, backtest_environment=backtest_environment)


def _env_context(*, resample: str, tick_ms: float, submit_ms: float = 50.0, cancel_ms: float = 50.0) -> dict:
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
# 1. Generation prompt includes execution_policy required phrases
# ===================================================================

class TestPromptExecutionPolicyRequired:

    def test_prompt_has_do_not_omit_rule(self):
        prompt = build_user_prompt(research_goal="test")
        assert "do not omit execution_policy" in prompt.lower()

    def test_prompt_has_explicit_specification_section(self):
        prompt = build_user_prompt(research_goal="test")
        assert "Explicit Specification" in prompt
        assert "placement_mode" in prompt
        assert "cancel_after_ticks" in prompt
        assert "max_reprices" in prompt

    def test_prompt_has_unsafe_warning(self):
        prompt = build_user_prompt(research_goal="test")
        assert "treated as unsafe" in prompt.lower()


# ===================================================================
# 2. Mock plan includes explicit execution_policy
# ===================================================================

class TestMockPlanExecutionPolicy:

    def test_mock_plan_has_execution_policy(self):
        from strategy_block.strategy_generation.v2.openai_generation import (
            _build_mock_plan,
        )
        plan = _build_mock_plan("imbalance momentum")
        assert plan.execution_policy is not None
        assert plan.execution_policy.placement_mode == "passive_join"
        assert plan.execution_policy.cancel_after_ticks > 0
        assert plan.execution_policy.max_reprices > 0

    def test_mock_plan_all_styles_have_execution_policy(self):
        from strategy_block.strategy_generation.v2.openai_generation import (
            _build_mock_plan,
        )
        for goal in ("imbalance momentum", "mean reversion", "spread fade"):
            plan = _build_mock_plan(goal)
            assert plan.execution_policy is not None, (
                f"Mock plan for '{goal}' is missing execution_policy"
            )


# ===================================================================
# 3. Lowering marks execution_policy_explicit metadata
# ===================================================================

class TestLoweringMetadata:

    def test_lowering_marks_explicit_true(self):
        from strategy_block.strategy_generation.v2.openai_generation import (
            _build_mock_plan,
            generate_plan_with_openai,
        )
        from strategy_block.strategy_generation.v2.lowering import lower_plan_to_spec_v2
        from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient

        client = OpenAIStrategyGenClient(mode="mock")
        plan, _ = generate_plan_with_openai(
            client=client, research_goal="momentum"
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.metadata.get("execution_policy_explicit") is True

    def test_lowering_marks_explicit_false_when_none(self):
        from strategy_block.strategy_generation.v2.schemas.plan_schema import (
            ConditionPlan, EntryPlan, ExitPolicyPlan, ExitRulePlan, RiskPlan,
            StrategyPlan,
        )
        from strategy_block.strategy_generation.v2.lowering import lower_plan_to_spec_v2

        plan = StrategyPlan(
            name="no_ep_plan",
            description="test",
            research_goal="test",
            strategy_style="momentum",
            entry_policies=[EntryPlan(
                name="e1", side="long",
                trigger=ConditionPlan(feature="order_imbalance", op=">", threshold=0.3),
            )],
            exit_policies=[ExitPolicyPlan(name="x1", rules=[
                ExitRulePlan(name="sl", priority=1, condition=ConditionPlan(
                    position_attr="unrealized_pnl_bps", op="<=", threshold=-25.0,
                ), action="close_all"),
            ])],
            execution_policy=None,
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.metadata.get("execution_policy_explicit") is False


# ===================================================================
# 4. Reviewer: short-horizon + no execution_policy → error
# ===================================================================

class TestReviewerShortHorizonNoEP:

    def test_style_hint_short_horizon_no_time_exit_is_error(self):
        spec = StrategySpecV2(
            name="style_hint_no_ep",
            entry_policies=[EntryPolicyV2(
                name="e1", side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.5),
            )],
            exit_policies=[ExitPolicyV2(name="exits", rules=[
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
            ])],
            execution_policy=None,
            risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
            metadata={"strategy_style": "momentum"},
        )
        result = _review(spec)
        assert _has_error(result, "missing_execution_policy_for_short_horizon")

    def test_short_horizon_no_ep_is_error(self):
        """holding=10, no execution_policy → missing_execution_policy error."""
        spec = _spec_no_ep(holding_ticks_threshold=10.0)
        result = _review(spec)
        assert _has_error(result, "missing_execution_policy_for_short_horizon")

    def test_short_horizon_with_ep_no_error(self):
        """holding=10, with execution_policy → no missing error."""
        spec = _spec_with_ep(holding_ticks_threshold=10.0)
        result = _review(spec)
        assert not _has_error(result, "missing_execution_policy_for_short_horizon")


# ===================================================================
# 5. Reviewer: non-short-horizon + no execution_policy → warning
# ===================================================================

class TestReviewerLongHorizonNoEP:

    def test_long_horizon_no_ep_is_warning(self):
        """holding=100, no execution_policy → warning only."""
        spec = _spec_no_ep(holding_ticks_threshold=100.0)
        result = _review(spec)
        assert _has_warning(result, "execution_policy_implicit_risk")
        assert not _has_error(result, "missing_execution_policy_for_short_horizon")

    def test_long_horizon_with_ep_no_warning(self):
        """holding=100, with execution_policy → no implicit risk warning."""
        spec = _spec_with_ep(holding_ticks_threshold=100.0)
        result = _review(spec)
        assert not _has_issue(result, "execution_policy_implicit_risk")


# ===================================================================
# 6. No time exit = inferred implicit execution risk on microstructure alpha
# ===================================================================

class TestNoTimeExitImplicitRisk:

    def test_no_time_exit_no_ep_warns_implicit_risk(self):
        """Without explicit holding horizon, microstructure-sensitive no-EP spec should still warn."""
        spec = StrategySpecV2(
            name="no_exit_no_ep",
            entry_policies=[EntryPolicyV2(
                name="e1", side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.5),
            )],
            exit_policies=[ExitPolicyV2(name="exits", rules=[
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
            ])],
            execution_policy=None,
            risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
        )
        result = _review(spec)
        assert not _has_error(result, "missing_execution_policy_for_short_horizon")
        assert _has_warning(result, "execution_policy_implicit_risk")


# ===================================================================
# 7. Repair planner inserts conservative EP
# ===================================================================

class TestRepairPlannerInsertsEP:

    def test_missing_ep_short_horizon_generates_ep_ops(self):
        spec = _spec_no_ep(holding_ticks_threshold=10.0)
        static_review = _review(spec)
        reviewer = LLMReviewerV2(client_mode="mock")
        llm_review = reviewer.review(spec=spec, static_review=static_review)
        planner = RepairPlannerV2(client_mode="mock")
        plan = planner.plan(
            spec=spec,
            static_review=static_review,
            llm_review=llm_review,
        )

        op_names = [op.op for op in plan.operations]
        assert "set_placement_mode" in op_names
        assert "set_cancel_after_ticks" in op_names
        assert "set_max_reprices" in op_names


# ===================================================================
# 8. Patcher inserts execution_policy on None spec
# ===================================================================

class TestPatcherInsertsEPOnNone:

    def test_patcher_creates_ep_from_none(self):
        """Patcher's _ensure_execution_policy() should create EP when None."""
        spec = _spec_no_ep(holding_ticks_threshold=10.0)
        assert spec.execution_policy is None

        plan = RepairPlan(
            summary="add missing execution policy",
            operations=[
                RepairOperation(op="set_placement_mode", target="execution_policy",
                                value="passive_join", reason="x"),
                RepairOperation(op="set_cancel_after_ticks", target="execution_policy",
                                value=15, reason="x"),
                RepairOperation(op="set_max_reprices", target="execution_policy",
                                value=2, reason="x"),
            ],
        )
        patched = StrategyRepairPatcherV2().apply(spec, plan)
        assert patched.execution_policy is not None
        assert patched.execution_policy.placement_mode == "passive_join"
        assert patched.execution_policy.cancel_after_ticks == 15
        assert patched.execution_policy.max_reprices == 2

    def test_original_spec_unchanged(self):
        spec = _spec_no_ep(holding_ticks_threshold=10.0)
        plan = RepairPlan(
            summary="add ep",
            operations=[
                RepairOperation(op="set_max_reprices", target="execution_policy",
                                value=3, reason="x"),
            ],
        )
        StrategyRepairPatcherV2().apply(spec, plan)
        assert spec.execution_policy is None  # original untouched


# ===================================================================
# 9. Pipeline end-to-end: missing EP → repair → EP inserted
# ===================================================================

class TestPipelineMissingEPRepair:

    def test_short_horizon_no_ep_repaired(self):
        spec = _spec_no_ep(holding_ticks_threshold=10.0)
        result = run_auto_repair(
            spec=spec,
            backtest_environment={},
            client_mode="mock",
        )

        assert result.repair_applied is True
        assert result.repair_plan is not None
        op_names = [op.op for op in result.repair_plan.operations]
        assert "set_placement_mode" in op_names or "set_cancel_after_ticks" in op_names

        # Repaired spec should have execution_policy
        if result.repaired_spec is not None:
            repaired = StrategySpecV2.from_dict(result.repaired_spec)
            assert repaired.execution_policy is not None
            # And the missing_execution_policy error should be gone
            repaired_review = _review(repaired)
            assert not _has_error(repaired_review,
                                  "missing_execution_policy_for_short_horizon")


# ===================================================================
# 10. Regression: specs with explicit EP still pass
# ===================================================================

class TestRegressionExplicitEP:

    def test_conservative_ep_short_horizon_passes(self):
        spec = _spec_with_ep(holding_ticks_threshold=10.0)
        result = _review(spec)
        assert not _has_error(result, "missing_execution_policy_for_short_horizon")
        assert not _has_error(result, "execution_policy_too_aggressive")
        assert not _has_error(result, "churn_risk_high")

    def test_conservative_ep_long_horizon_passes(self):
        spec = _spec_with_ep(holding_ticks_threshold=100.0)
        result = _review(spec)
        assert not _has_error(result, "missing_execution_policy_for_short_horizon")
        assert not _has_issue(result, "execution_policy_implicit_risk")



def test_missing_execution_policy_wall_clock_short_horizon_differs_by_cadence():
    spec = _spec_no_ep(holding_ticks_threshold=50.0)

    result_1s = _review(
        spec,
        backtest_environment=_env_context(resample="1s", tick_ms=1000.0),
    )
    result_500ms = _review(
        spec,
        backtest_environment=_env_context(resample="500ms", tick_ms=500.0),
    )

    assert not _has_error(result_1s, "missing_execution_policy_for_short_horizon")
    assert _has_warning(result_1s, "execution_policy_implicit_risk")
    assert _has_error(result_500ms, "missing_execution_policy_for_short_horizon")
