"""Tests for position_attr vs feature validation.

Ensures holding_ticks, unrealized_pnl_bps, entry_price, position_size,
position_side are only accepted as position_attr, never as feature.
"""
from __future__ import annotations

import pytest

from strategy_block.strategy_generation.v2.schemas.plan_schema import (
    ConditionPlan,
    EntryPlan,
    ExitPolicyPlan,
    ExitRulePlan,
    PreconditionPlan,
    RiskPlan,
    StrategyPlan,
)
from strategy_block.strategy_generation.v2.utils.response_parser import (
    PlanParseError,
    check_position_attr_misuse,
    parse_plan_response,
    validate_plan,
)
from strategy_block.strategy_review.review_common import POSITION_ATTR_ONLY


# ---------------------------------------------------------------------------
# Helper: build a minimal valid plan
# ---------------------------------------------------------------------------

def _base_plan(**overrides) -> StrategyPlan:
    """Minimal valid plan with customizable exit rules."""
    defaults = dict(
        name="test_plan",
        description="test",
        research_goal="test",
        strategy_style="momentum",
        entry_policies=[
            EntryPlan(
                name="long_entry",
                side="long",
                trigger=ConditionPlan(feature="order_imbalance", op=">", threshold=0.3),
            ),
        ],
        exit_policies=[
            ExitPolicyPlan(
                name="exits",
                rules=[
                    ExitRulePlan(
                        name="stop_loss",
                        priority=1,
                        condition=ConditionPlan(
                            position_attr="unrealized_pnl_bps", op="<=", threshold=-25.0,
                        ),
                        action="close_all",
                    ),
                ],
            ),
        ],
        risk_policy=RiskPlan(),
    )
    defaults.update(overrides)
    return StrategyPlan(**defaults)


# ===================================================================
# 1. feature="holding_ticks" → fail
# ===================================================================

class TestFeatureHoldingTicksFails:

    def test_holding_ticks_as_feature_rejected(self):
        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="time_exit",
                            condition=ConditionPlan(
                                feature="holding_ticks", op=">=", threshold=30.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1
        assert "holding_ticks" in errors[0]
        assert "position_attr" in errors[0].lower() or "position attribute" in errors[0].lower()

    def test_holding_ticks_as_feature_in_entry_rejected(self):
        plan = _base_plan(
            entry_policies=[
                EntryPlan(
                    name="bad_entry",
                    side="long",
                    trigger=ConditionPlan(feature="holding_ticks", op=">", threshold=0),
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1
        assert "holding_ticks" in errors[0]


# ===================================================================
# 2. feature="unrealized_pnl_bps" → fail
# ===================================================================

class TestFeatureUnrealizedPnlBpsFails:

    def test_unrealized_pnl_bps_as_feature_rejected(self):
        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="bad_stop",
                            condition=ConditionPlan(
                                feature="unrealized_pnl_bps", op="<=", threshold=-25.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1
        assert "unrealized_pnl_bps" in errors[0]

    def test_all_position_attr_only_names_rejected_as_feature(self):
        """Every name in POSITION_ATTR_ONLY must fail when used as feature."""
        for attr_name in sorted(POSITION_ATTR_ONLY):
            plan = _base_plan(
                exit_policies=[
                    ExitPolicyPlan(
                        name="exits",
                        rules=[
                            ExitRulePlan(
                                name="bad_rule",
                                condition=ConditionPlan(
                                    feature=attr_name, op=">=", threshold=1.0,
                                ),
                                action="close_all",
                            ),
                        ],
                    ),
                ],
            )
            errors = check_position_attr_misuse(plan)
            assert len(errors) >= 1, f"{attr_name} as feature should be rejected"
            assert attr_name in errors[0]


# ===================================================================
# 3. position_attr="holding_ticks" → pass
# ===================================================================

class TestPositionAttrHoldingTicksPasses:

    def test_holding_ticks_as_position_attr_accepted(self):
        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="time_exit",
                            condition=ConditionPlan(
                                position_attr="holding_ticks", op=">=", threshold=30.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert errors == []


# ===================================================================
# 4. position_attr="unrealized_pnl_bps" → pass
# ===================================================================

class TestPositionAttrUnrealizedPnlBpsPasses:

    def test_unrealized_pnl_bps_as_position_attr_accepted(self):
        plan = _base_plan()  # default uses position_attr="unrealized_pnl_bps"
        errors = check_position_attr_misuse(plan)
        assert errors == []

    def test_all_position_attr_only_names_accepted_as_position_attr(self):
        """Every name in POSITION_ATTR_ONLY must pass when used as position_attr."""
        for attr_name in sorted(POSITION_ATTR_ONLY):
            plan = _base_plan(
                exit_policies=[
                    ExitPolicyPlan(
                        name="exits",
                        rules=[
                            ExitRulePlan(
                                name="test_rule",
                                condition=ConditionPlan(
                                    position_attr=attr_name, op=">=", threshold=1.0,
                                ),
                                action="close_all",
                            ),
                        ],
                    ),
                ],
            )
            errors = check_position_attr_misuse(plan)
            assert errors == [], f"{attr_name} as position_attr should be accepted"


# ===================================================================
# 5. parse → lower → review end-to-end test
# ===================================================================

class TestEndToEndReview:

    def test_bad_plan_fails_review(self):
        """A plan with feature=holding_ticks should fail static review after lowering."""
        from strategy_block.strategy_generation.v2.lowering import lower_plan_to_spec_v2
        from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2

        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="bad_time_exit",
                            condition=ConditionPlan(
                                feature="holding_ticks", op=">=", threshold=30.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )

        # Parser check should catch it first
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1

        # But if somehow it slips through to lowering + review:
        spec = lower_plan_to_spec_v2(plan)
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)

        # Review should fail
        assert result.passed is False
        attr_errors = [
            i for i in result.issues
            if i.category == "position_attr_as_feature" and i.severity == "error"
        ]
        assert len(attr_errors) >= 1
        assert "holding_ticks" in attr_errors[0].description

    def test_good_plan_passes_review(self):
        """A plan using position_attr correctly should pass review."""
        from strategy_block.strategy_generation.v2.lowering import lower_plan_to_spec_v2
        from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2

        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="stop_loss",
                            condition=ConditionPlan(
                                position_attr="unrealized_pnl_bps", op="<=", threshold=-25.0,
                            ),
                            action="close_all",
                        ),
                        ExitRulePlan(
                            name="time_exit",
                            condition=ConditionPlan(
                                position_attr="holding_ticks", op=">=", threshold=30.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )

        errors = check_position_attr_misuse(plan)
        assert errors == []

        spec = lower_plan_to_spec_v2(plan)
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)

        # Should not have any position_attr_as_feature errors
        attr_errors = [
            i for i in result.issues
            if i.category == "position_attr_as_feature"
        ]
        assert attr_errors == []

    def test_mixed_bad_and_good_conditions(self):
        """Plan with both correct position_attr and wrong feature usage."""
        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="good_stop",
                            condition=ConditionPlan(
                                position_attr="unrealized_pnl_bps", op="<=", threshold=-25.0,
                            ),
                            action="close_all",
                        ),
                        ExitRulePlan(
                            name="bad_time",
                            condition=ConditionPlan(
                                feature="holding_ticks", op=">=", threshold=30.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1
        assert "holding_ticks" in errors[0]

    def test_nested_composite_condition_caught(self):
        """Position attr misuse inside a composite (all/any) should be caught."""
        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="composite_exit",
                            condition=ConditionPlan(
                                combine="any",
                                children=[
                                    ConditionPlan(feature="spread_bps", op=">", threshold=20.0),
                                    ConditionPlan(feature="holding_ticks", op=">=", threshold=50.0),
                                ],
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1
        assert "holding_ticks" in errors[0]


# ===================================================================
# 6. OpenAI mock/replay response rejection test
# ===================================================================

class TestOpenAIMockRejection:

    def test_mock_plan_is_clean(self):
        """Default mock plan should not have position_attr misuse."""
        from strategy_block.strategy_generation.v2.openai_generation import _build_mock_plan

        for goal in ["momentum", "mean reversion", "spread fade"]:
            plan = _build_mock_plan(goal)
            errors = check_position_attr_misuse(plan)
            assert errors == [], f"Mock plan for '{goal}' has position_attr misuse: {errors}"

    def test_generate_plan_rejects_bad_mock(self):
        """If a mock plan had misuse, generate_plan_with_openai should raise."""
        from unittest.mock import patch

        from strategy_block.strategy_generation.v2.openai_generation import (
            generate_plan_with_openai,
        )
        from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient

        bad_plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="bad_exit",
                            condition=ConditionPlan(
                                feature="unrealized_pnl_bps", op="<=", threshold=-25.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )

        client = OpenAIStrategyGenClient(mode="mock")

        with patch(
            "strategy_block.strategy_generation.v2.openai_generation._build_mock_plan",
            return_value=bad_plan,
        ):
            with pytest.raises(PlanParseError, match="position_attr"):
                generate_plan_with_openai(
                    client=client,
                    research_goal="test",
                )

    def test_generate_spec_rejects_before_lowering(self):
        """generate_spec_v2_with_openai should raise before lowering bad plans."""
        from unittest.mock import patch

        from strategy_block.strategy_generation.v2.openai_generation import (
            generate_spec_v2_with_openai,
        )
        from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient

        bad_plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="bad_exit",
                            condition=ConditionPlan(
                                feature="position_size", op=">", threshold=0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )

        client = OpenAIStrategyGenClient(mode="mock")

        # Patch _build_mock_plan so generate_plan_with_openai returns the bad plan
        with patch(
            "strategy_block.strategy_generation.v2.openai_generation._build_mock_plan",
            return_value=bad_plan,
        ):
            with pytest.raises(PlanParseError, match="position_attr"):
                generate_spec_v2_with_openai(
                    client=client,
                    research_goal="test",
                )


# ===================================================================
# 7. Reviewer hard error tests (spec-level)
# ===================================================================

class TestReviewerHardError:

    def test_reviewer_catches_feature_holding_ticks(self):
        """Reviewer must flag holding_ticks as feature with severity=error."""
        from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr
        from strategy_block.strategy_specs.v2.schema_v2 import (
            EntryPolicyV2, EntryConstraints, ExitPolicyV2, ExitRuleV2,
            ExitActionV2, RiskPolicyV2, PositionSizingV2, StrategySpecV2,
        )
        from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2

        # Build a spec where holding_ticks is used as plain feature
        spec = StrategySpecV2(
            name="test_bad_spec",
            version="2.0",
            description="test",
            spec_format="v2",
            entry_policies=[
                EntryPolicyV2(
                    name="entry",
                    side="long",
                    trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                    strength=ConstExpr(value=0.5),
                    constraints=EntryConstraints(),
                ),
            ],
            exit_policies=[
                ExitPolicyV2(
                    name="exits",
                    rules=[
                        ExitRuleV2(
                            name="bad_time_exit",
                            priority=1,
                            condition=ComparisonExpr(
                                feature="holding_ticks", op=">=", threshold=30.0,
                            ),
                            action=ExitActionV2(type="close_all"),
                        ),
                    ],
                ),
            ],
            risk_policy=RiskPolicyV2(
                max_position=500,
                inventory_cap=1000,
                position_sizing=PositionSizingV2(),
            ),
        )

        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert result.passed is False

        attr_errors = [
            i for i in result.issues
            if i.category == "position_attr_as_feature" and i.severity == "error"
        ]
        assert len(attr_errors) >= 1
        assert "holding_ticks" in attr_errors[0].description

    def test_reviewer_passes_correct_position_attr(self):
        """Reviewer must NOT flag proper position_attr usage."""
        from strategy_block.strategy_specs.v2.ast_nodes import (
            ComparisonExpr, ConstExpr, PositionAttrExpr,
        )
        from strategy_block.strategy_specs.v2.schema_v2 import (
            EntryPolicyV2, EntryConstraints, ExitPolicyV2, ExitRuleV2,
            ExitActionV2, RiskPolicyV2, PositionSizingV2, StrategySpecV2,
        )
        from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2

        spec = StrategySpecV2(
            name="test_good_spec",
            version="2.0",
            description="test",
            spec_format="v2",
            entry_policies=[
                EntryPolicyV2(
                    name="entry",
                    side="long",
                    trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                    strength=ConstExpr(value=0.5),
                    constraints=EntryConstraints(),
                ),
            ],
            exit_policies=[
                ExitPolicyV2(
                    name="exits",
                    rules=[
                        ExitRuleV2(
                            name="time_exit",
                            priority=1,
                            condition=ComparisonExpr(
                                left=PositionAttrExpr(name="holding_ticks"),
                                op=">=",
                                threshold=30.0,
                            ),
                            action=ExitActionV2(type="close_all"),
                        ),
                    ],
                ),
            ],
            risk_policy=RiskPolicyV2(
                max_position=500,
                inventory_cap=1000,
                position_sizing=PositionSizingV2(),
            ),
        )

        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)

        attr_errors = [
            i for i in result.issues
            if i.category == "position_attr_as_feature"
        ]
        assert attr_errors == []


# ===================================================================
# 8. Cross feature and rolling feature rejection
# ===================================================================

class TestCrossAndRollingRejection:

    def test_cross_feature_with_position_attr_rejected(self):
        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="bad_cross",
                            condition=ConditionPlan(
                                cross_feature="holding_ticks",
                                cross_threshold=50.0,
                                cross_direction="above",
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1
        assert "holding_ticks" in errors[0]

    def test_rolling_feature_with_position_attr_rejected(self):
        plan = _base_plan(
            exit_policies=[
                ExitPolicyPlan(
                    name="exits",
                    rules=[
                        ExitRulePlan(
                            name="bad_rolling",
                            condition=ConditionPlan(
                                rolling_feature="unrealized_pnl_bps",
                                rolling_method="mean",
                                rolling_window=10,
                                op="<",
                                threshold=-20.0,
                            ),
                            action="close_all",
                        ),
                    ],
                ),
            ],
        )
        errors = check_position_attr_misuse(plan)
        assert len(errors) >= 1
        assert "unrealized_pnl_bps" in errors[0]


# ===================================================================
# 9. POSITION_ATTR_ONLY constant sanity
# ===================================================================

class TestPositionAttrOnlyConstant:

    def test_expected_names_present(self):
        assert "holding_ticks" in POSITION_ATTR_ONLY
        assert "unrealized_pnl_bps" in POSITION_ATTR_ONLY
        assert "entry_price" in POSITION_ATTR_ONLY
        assert "position_size" in POSITION_ATTR_ONLY
        assert "position_side" in POSITION_ATTR_ONLY

    def test_market_features_not_in_position_attr_only(self):
        from strategy_block.strategy_review.review_common import KNOWN_FEATURES
        overlap = KNOWN_FEATURES & POSITION_ATTR_ONLY
        assert overlap == set(), f"Overlap between features and position_attr: {overlap}"
