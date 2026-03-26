"""Tests for strengthened prompts and schema descriptions.

Verifies:
1. Prompt content includes critical runtime semantics rules
2. Mock plans adhere to position_attr/feature separation
3. Schema field descriptions contain necessary warnings
4. Prompt builder produces well-formed prompts
5. Existing generation pipeline remains compatible
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_generation.v2.openai_generation import (
    _build_mock_plan,
    generate_plan_with_openai,
    generate_spec_v2_with_openai,
)
from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient
from strategy_block.strategy_generation.v2.schemas.plan_schema import (
    ConditionPlan,
    EntryPlan,
    ExitPolicyPlan,
    ExitRulePlan,
    StrategyPlan,
)
from strategy_block.strategy_generation.v2.utils.prompt_builder import (
    build_system_prompt,
    build_user_prompt,
)
from strategy_block.strategy_generation.v2.utils.response_parser import (
    check_position_attr_misuse,
)
from strategy_block.strategy_review.review_common import POSITION_ATTR_ONLY
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2


POSITION_ATTR_NAMES = {"holding_ticks", "unrealized_pnl_bps", "entry_price",
                        "position_size", "position_side"}


# ===================================================================
# 1. System prompt content verification
# ===================================================================

class TestSystemPromptContent:

    @pytest.fixture(autouse=True)
    def _load_prompt(self):
        self.prompt = build_system_prompt()

    def test_position_attr_vs_feature_namespace_explained(self):
        assert "Two separate namespaces" in self.prompt or "two kinds of runtime values" in self.prompt.lower()

    def test_silent_bug_warning_present(self):
        assert "SILENT BUG" in self.prompt or "silent runtime failure" in self.prompt.lower()

    def test_all_position_attr_names_mentioned(self):
        for name in POSITION_ATTR_NAMES:
            assert name in self.prompt, f"Missing position_attr name: {name}"

    def test_correct_stop_loss_example(self):
        """Prompt must show stop-loss using position_attr, not feature."""
        assert '"position_attr": "unrealized_pnl_bps"' in self.prompt

    def test_correct_time_exit_example(self):
        """Prompt must show time exit using position_attr, not feature."""
        assert '"position_attr": "holding_ticks"' in self.prompt

    def test_wrong_feature_example_shown(self):
        """Prompt must show the WRONG way (feature: holding_ticks) as anti-pattern."""
        assert '"feature": "holding_ticks"' in self.prompt

    def test_exit_first_semantics_documented(self):
        """Prompt explains that exits are independent of entry gates."""
        assert "exit" in self.prompt.lower() and "gate" in self.prompt.lower()

    def test_state_reset_requirement_documented(self):
        assert "reset" in self.prompt.lower() and "increment" in self.prompt.lower()

    def test_validator_rejection_warning(self):
        """Prompt warns that invalid plans will be rejected."""
        assert "rejected" in self.prompt.lower() or "REJECTED" in self.prompt

    def test_regime_exit_coverage_documented(self):
        assert "regime" in self.prompt.lower() and "global" in self.prompt.lower()


# ===================================================================
# 2. User prompt content verification
# ===================================================================

class TestUserPromptContent:

    @pytest.fixture(autouse=True)
    def _load_prompt(self):
        self.prompt = build_user_prompt(
            research_goal="test momentum",
            strategy_style="auto",
            latency_ms=1.0,
        )

    def test_exit_policy_requirements_present(self):
        assert "position_attr" in self.prompt

    def test_namespace_rules_present(self):
        assert "MUST use" in self.prompt or "FORBIDDEN" in self.prompt

    def test_placeholders_filled(self):
        assert "test momentum" in self.prompt
        assert "{research_goal}" not in self.prompt
        assert "{strategy_style}" not in self.prompt
        assert "{latency_ms}" not in self.prompt


# ===================================================================
# 3. Schema field descriptions contain warnings
# ===================================================================

class TestSchemaDescriptions:

    def test_feature_field_warns_about_position_attr(self):
        schema = ConditionPlan.model_json_schema()
        feature_desc = _get_field_description(schema, "feature")
        assert feature_desc is not None
        assert "FORBIDDEN" in feature_desc or "NEVER" in feature_desc or "MUST" in feature_desc

    def test_position_attr_field_mentions_exit_only(self):
        schema = ConditionPlan.model_json_schema()
        desc = _get_field_description(schema, "position_attr")
        assert desc is not None
        assert "exit" in desc.lower() or "0.0" in desc

    def test_cross_feature_warns_about_position_attr(self):
        schema = ConditionPlan.model_json_schema()
        desc = _get_field_description(schema, "cross_feature")
        assert desc is not None
        assert "FORBIDDEN" in desc or "position" in desc.lower()

    def test_rolling_feature_warns_about_position_attr(self):
        schema = ConditionPlan.model_json_schema()
        desc = _get_field_description(schema, "rolling_feature")
        assert desc is not None
        assert "FORBIDDEN" in desc or "position" in desc.lower()

    def test_entry_trigger_warns_about_feature_only(self):
        schema = EntryPlan.model_json_schema()
        trigger_desc = _get_field_description(schema, "trigger")
        assert trigger_desc is not None
        assert "feature" in trigger_desc.lower() or "position_attr" in trigger_desc.lower()

    def test_exit_rule_condition_mentions_position_attr(self):
        schema = ExitRulePlan.model_json_schema()
        desc = _get_field_description(schema, "condition")
        assert desc is not None
        assert "position_attr" in desc

    def test_strategy_plan_exit_policies_description(self):
        schema = StrategyPlan.model_json_schema()
        desc = _get_field_description(schema, "exit_policies")
        assert desc is not None
        assert "close_all" in desc or "position_attr" in desc


def _get_field_description(schema: dict, field_name: str) -> str | None:
    """Extract a field description from a Pydantic JSON schema.

    Handles Pydantic v2's $defs/$ref structure for self-referential models.
    """
    # Direct properties
    props = schema.get("properties", {})

    # If schema uses $defs + $ref (Pydantic v2 recursive models), resolve
    if not props and "$defs" in schema and "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        resolved = schema["$defs"].get(ref, {})
        props = resolved.get("properties", {})

    if field_name not in props:
        return None

    field = props[field_name]
    # Description may be at top level or inside anyOf variants
    if "description" in field:
        return field["description"]
    for variant in field.get("anyOf", []):
        if "description" in variant:
            return variant["description"]
    return None


# ===================================================================
# 4. Mock plan schema adherence
# ===================================================================

class TestMockPlanAdherence:

    @pytest.mark.parametrize("goal", [
        "imbalance momentum",
        "mean reversion on order flow",
        "spread fade strategy",
    ])
    def test_mock_plan_no_position_attr_misuse(self, goal: str):
        """Mock plans must not use position_attr names as features."""
        plan = _build_mock_plan(goal)
        errors = check_position_attr_misuse(plan)
        assert errors == [], f"Mock plan for {goal!r} has position_attr misuse: {errors}"

    @pytest.mark.parametrize("goal", [
        "imbalance momentum",
        "mean reversion on order flow",
        "spread fade strategy",
    ])
    def test_mock_plan_has_close_all_exit(self, goal: str):
        """Mock plans must have at least one close_all exit rule."""
        plan = _build_mock_plan(goal)
        has_close_all = any(
            rule.action == "close_all"
            for xp in plan.exit_policies
            for rule in xp.rules
        )
        assert has_close_all, f"Mock plan for {goal!r} has no close_all exit"

    @pytest.mark.parametrize("goal", [
        "imbalance momentum",
        "mean reversion on order flow",
        "spread fade strategy",
    ])
    def test_mock_plan_exits_use_position_attr(self, goal: str):
        """Mock plan exit conditions should use position_attr for stop/time."""
        plan = _build_mock_plan(goal)
        has_position_attr_exit = False
        for xp in plan.exit_policies:
            for rule in xp.rules:
                if rule.condition.position_attr in POSITION_ATTR_NAMES:
                    has_position_attr_exit = True
                    break
        assert has_position_attr_exit, (
            f"Mock plan for {goal!r} has no position_attr-based exit"
        )

    @pytest.mark.parametrize("goal", [
        "imbalance momentum",
        "mean reversion on order flow",
        "spread fade strategy",
    ])
    def test_mock_plan_entries_use_features_only(self, goal: str):
        """Mock plan entry triggers must not use position_attr."""
        plan = _build_mock_plan(goal)
        for ep in plan.entry_policies:
            _assert_no_position_attr_in_condition(ep.trigger, f"entry '{ep.name}'")

    @pytest.mark.parametrize("goal", [
        "imbalance momentum",
        "mean reversion on order flow",
        "spread fade strategy",
    ])
    def test_mock_plan_preconditions_use_features_only(self, goal: str):
        plan = _build_mock_plan(goal)
        for pc in plan.preconditions:
            _assert_no_position_attr_in_condition(pc.condition, f"precondition '{pc.name}'")


def _assert_no_position_attr_in_condition(cond: ConditionPlan, context: str):
    """Recursively check that no position_attr field is set in a condition tree."""
    assert cond.position_attr is None, (
        f"{context}: uses position_attr={cond.position_attr!r} in entry/precondition"
    )
    if cond.children:
        for i, child in enumerate(cond.children):
            _assert_no_position_attr_in_condition(child, f"{context}.children[{i}]")
    if cond.persist_condition:
        _assert_no_position_attr_in_condition(cond.persist_condition, f"{context}.persist")


# ===================================================================
# 5. Full pipeline compatibility
# ===================================================================

class TestPipelineCompatibility:

    @pytest.fixture()
    def mock_client(self) -> OpenAIStrategyGenClient:
        return OpenAIStrategyGenClient(mode="mock")

    @pytest.fixture()
    def reviewer(self) -> StrategyReviewerV2:
        return StrategyReviewerV2()

    def test_mock_generate_plan_still_works(self, mock_client):
        plan, trace = generate_plan_with_openai(
            client=mock_client,
            research_goal="imbalance momentum",
        )
        assert isinstance(plan, StrategyPlan)
        assert trace["parse_success"] is True

    def test_mock_generate_spec_still_works(self, mock_client, reviewer):
        spec, trace = generate_spec_v2_with_openai(
            client=mock_client,
            research_goal="imbalance momentum",
            reviewer=reviewer,
        )
        assert trace["static_review_passed"] is True

    def test_mock_generate_spec_passes_review(self, mock_client, reviewer):
        spec, trace = generate_spec_v2_with_openai(
            client=mock_client,
            research_goal="mean reversion",
            reviewer=reviewer,
        )
        assert trace["static_review_passed"] is True

    def test_prompt_builder_no_unfilled_placeholders(self):
        system = build_system_prompt()
        assert "{" not in system or "json" in system.lower()  # JSON examples have braces

        user = build_user_prompt(
            research_goal="test",
            strategy_style="momentum",
            latency_ms=1.0,
        )
        assert "{research_goal}" not in user
        assert "{strategy_style}" not in user
        assert "{latency_ms}" not in user
        assert "{constraints}" not in user
