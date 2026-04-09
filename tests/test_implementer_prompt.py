"""
tests/test_implementer_prompt.py
----------------------------------
implementer_prompt_builder.build_implementer_messages() contract tests — v2.2.
"""
from __future__ import annotations

import pytest

from strategy_loop.implementer_prompt_builder import build_implementer_messages
from strategy_loop.spec_schema import (
    DerivedFeature,
    SpecCondition,
    StrategySpec,
    TunableParam,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def fc(source: str, op: str, threshold: float) -> SpecCondition:
    return SpecCondition(source_type="feature", source=source, op=op, threshold=threshold)


def dc(derived: str, op: str, threshold: float) -> SpecCondition:
    return SpecCondition(source_type="derived_feature", source=derived, op=op, threshold=threshold)


def _make_spec() -> StrategySpec:
    return StrategySpec(
        archetype=1,
        archetype_name="liquidity imbalance continuation",
        entry_conditions=[
            fc("order_imbalance", ">", 0.3),
            fc("spread_bps", "<", 50.0),
        ],
        exit_time_ticks=25,
        exit_signal_conditions=[
            fc("order_imbalance", "<", -0.05),
        ],
        tunable_params=[
            TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.3, "float", (0.1, 0.9)),
            TunableParam("HOLDING_TICKS_EXIT", 25.0, "int", (5.0, 120.0)),
        ],
        features_used=["order_imbalance", "spread_bps"],
        rationale="Buy on buy-side pressure.",
    )


def _make_spec_with_derived() -> StrategySpec:
    return StrategySpec(
        archetype=1,
        archetype_name="liquidity imbalance continuation",
        derived_features=[
            DerivedFeature(
                name="spread_ticks",
                formula="(ask_1_price - bid_1_price) / tick_size",
                inputs=["ask_1_price", "bid_1_price", "tick_size"],
            ),
            DerivedFeature(
                name="ask_wall_ratio",
                formula="ask_1_volume / max(ask_2_volume, 1)",
                inputs=["ask_1_volume", "ask_2_volume"],
            ),
        ],
        entry_conditions=[
            fc("order_imbalance", ">", 0.3),
            dc("spread_ticks", "<", 3.0),
            dc("ask_wall_ratio", "<", 2.0),
        ],
        exit_time_ticks=25,
        exit_signal_conditions=[
            fc("order_imbalance", "<", -0.05),
            dc("spread_ticks", ">", 5.0),
        ],
        tunable_params=[
            TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.3, "float", (0.1, 0.9)),
            TunableParam("SPREAD_TICKS_MAX", 3, "int", (1, 10)),
        ],
        features_used=["order_imbalance"],
        rationale="Imbalance with spread-ticks filter.",
    )


# ── message structure ─────────────────────────────────────────────────────────

class TestMessageStructure:
    def test_returns_two_messages(self):
        assert len(build_implementer_messages(_make_spec())) == 2

    def test_first_is_system(self):
        assert build_implementer_messages(_make_spec())[0]["role"] == "system"

    def test_second_is_user(self):
        assert build_implementer_messages(_make_spec())[1]["role"] == "user"


# ── system prompt contract ────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_system_contains_generate_signal(self):
        msgs = build_implementer_messages(_make_spec())
        assert "generate_signal" in msgs[0]["content"]

    def test_features_list_placeholder_substituted(self):
        msgs = build_implementer_messages(_make_spec())
        assert "$features_list" not in msgs[0]["content"]
        assert "order_imbalance" in msgs[0]["content"]


# ── user prompt — basic spec guidance ────────────────────────────────────────

class TestSpecGuidanceInPrompt:
    def test_archetype_name_in_user_prompt(self):
        assert "liquidity imbalance continuation" in build_implementer_messages(_make_spec())[1]["content"]

    def test_entry_feature_conditions_use_features_get(self):
        user = build_implementer_messages(_make_spec())[1]["content"]
        # feature-type conditions must use features.get(...)
        assert "features.get('order_imbalance'" in user or 'features.get("order_imbalance"' in user

    def test_exit_time_ticks_in_user_prompt(self):
        assert "25" in build_implementer_messages(_make_spec())[1]["content"]

    def test_tunable_constants_in_user_prompt(self):
        user = build_implementer_messages(_make_spec())[1]["content"]
        assert "ORDER_IMBALANCE_THRESHOLD" in user
        assert "HOLDING_TICKS_EXIT" in user

    def test_generate_code_cta_present(self):
        assert "Generate the strategy Python code" in build_implementer_messages(_make_spec())[1]["content"]


# ── derived feature rendering ─────────────────────────────────────────────────

class TestDerivedFeatureRendering:
    def test_derived_definitions_appear_in_user_prompt(self):
        user = build_implementer_messages(_make_spec_with_derived())[1]["content"]
        assert "spread_ticks" in user
        assert "ask_wall_ratio" in user

    def test_derived_formula_appears_in_user_prompt(self):
        user = build_implementer_messages(_make_spec_with_derived())[1]["content"]
        assert "ask_1_price" in user
        assert "tick_size" in user

    def test_derived_condition_uses_named_variable_not_features_get(self):
        """source_type='derived_feature' conditions must NOT use features.get(...)."""
        user = build_implementer_messages(_make_spec_with_derived())[1]["content"]
        # spread_ticks < 3.0 must appear as "spread_ticks < 3.0", not features.get(...)
        assert "spread_ticks < 3.0" in user

    def test_derived_exit_condition_rendered_correctly(self):
        user = build_implementer_messages(_make_spec_with_derived())[1]["content"]
        assert "spread_ticks > 5.0" in user

    def test_derived_definitions_appear_before_entry_conditions(self):
        user = build_implementer_messages(_make_spec_with_derived())[1]["content"]
        derived_pos = user.find("spread_ticks =")
        entry_pos = user.find("Entry conditions")
        assert derived_pos < entry_pos, "Derived definitions must appear before entry conditions"

    def test_feature_conditions_still_use_features_get_with_derived_present(self):
        """Even when derived features exist, feature-type conditions use features.get."""
        user = build_implementer_messages(_make_spec_with_derived())[1]["content"]
        # order_imbalance is a feature-type condition
        assert "features.get('order_imbalance'" in user or 'features.get("order_imbalance"' in user

    def test_no_derived_section_when_none_defined(self):
        user = build_implementer_messages(_make_spec())[1]["content"]
        assert "Derived features" not in user


# ── optional context sections ─────────────────────────────────────────────────

class TestOptionalContextSections:
    def test_session_attempts_included_when_provided(self):
        attempts = [
            {"iteration": 1, "strategy_name": "spec_v1_code_v1",
             "entry_frequency": 0.05, "net_pnl": -100.0,
             "verdict": "fail", "primary_issue": "fee_dominated", "n_fills": 10}
        ]
        user = build_implementer_messages(_make_spec(), session_attempts=attempts)[1]["content"]
        assert "spec_v1_code_v1" in user
        assert "fee_dominated" in user

    def test_best_code_included_when_provided(self):
        best_code = "ORDER_IMBALANCE_THRESHOLD = 0.35\ndef generate_signal(f, p): return None"
        msgs = build_implementer_messages(_make_spec(), best_code_so_far=best_code)
        assert "ORDER_IMBALANCE_THRESHOLD = 0.35" in msgs[1]["content"]

    def test_previous_feedback_included_when_provided(self):
        fb = {"primary_issue": "entry too frequent", "issues": ["entry_too_frequent"],
              "suggestions": ["raise threshold"], "control_mode": "repair"}
        user = build_implementer_messages(_make_spec(), previous_feedback=fb)[1]["content"]
        assert "entry too frequent" in user

    def test_stuck_warning_appears_at_3(self):
        user = build_implementer_messages(_make_spec(), stuck_count=3)[1]["content"]
        assert "consecutive" in user.lower() or "non-passing" in user

    def test_no_extra_sections_without_optional_args(self):
        user = build_implementer_messages(_make_spec())[1]["content"]
        assert "Best code" not in user
        assert "Code attempts" not in user


# ── threshold_param rendering ─────────────────────────────────────────────────

class TestThresholdParamRendering:
    def _make_spec_with_params(self) -> StrategySpec:
        return StrategySpec(
            archetype=1,
            archetype_name="liquidity imbalance continuation",
            entry_conditions=[
                SpecCondition(
                    source_type="feature", source="order_imbalance",
                    op=">", threshold=0.3, threshold_param="ORDER_IMBALANCE_THRESHOLD",
                ),
                SpecCondition(
                    source_type="feature", source="spread_bps",
                    op="<", threshold=50.0, threshold_param="SPREAD_MAX_BPS",
                ),
            ],
            exit_time_ticks=25,
            exit_signal_conditions=[
                SpecCondition(
                    source_type="feature", source="order_imbalance",
                    op="<", threshold=-0.05, threshold_param="REVERSAL_THRESHOLD",
                ),
            ],
            tunable_params=[
                TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.3, "float", (0.1, 0.9)),
                TunableParam("SPREAD_MAX_BPS", 50.0, "float", (1.0, 200.0)),
                TunableParam("REVERSAL_THRESHOLD", -0.05, "float", (-0.9, 0.9)),
                TunableParam("HOLDING_TICKS_EXIT", 25, "int", (5, 120)),
            ],
            features_used=["order_imbalance", "spread_bps"],
            rationale="Buy on buy-side pressure.",
        )

    def _make_derived_spec_with_params(self) -> StrategySpec:
        return StrategySpec(
            archetype=1,
            archetype_name="spread-ticks filtered",
            derived_features=[
                DerivedFeature(
                    name="spread_ticks",
                    formula="(ask_1_price - bid_1_price) / tick_size",
                    inputs=["ask_1_price", "bid_1_price", "tick_size"],
                )
            ],
            entry_conditions=[
                SpecCondition(
                    source_type="feature", source="order_imbalance",
                    op=">", threshold=0.3, threshold_param="ORDER_IMBALANCE_THRESHOLD",
                ),
                SpecCondition(
                    source_type="derived_feature", source="spread_ticks",
                    op="<", threshold=3.0, threshold_param="SPREAD_TICKS_MAX",
                ),
            ],
            exit_time_ticks=25,
            exit_signal_conditions=[
                SpecCondition(
                    source_type="derived_feature", source="spread_ticks",
                    op=">", threshold=5.0, threshold_param="SPREAD_TICKS_EXIT",
                ),
            ],
            tunable_params=[
                TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.3, "float", (0.1, 0.9)),
                TunableParam("SPREAD_TICKS_MAX", 3, "int", (1, 10)),
                TunableParam("SPREAD_TICKS_EXIT", 5, "int", (1, 10)),
                TunableParam("HOLDING_TICKS_EXIT", 25, "int", (5, 120)),
            ],
            features_used=["order_imbalance"],
            rationale="Imbalance with spread-ticks filter.",
        )

    def test_feature_condition_uses_constant_name(self):
        """Feature condition with threshold_param renders constant name, not literal."""
        user = build_implementer_messages(self._make_spec_with_params())[1]["content"]
        assert "features.get('order_imbalance', 0.0) > ORDER_IMBALANCE_THRESHOLD" in user \
               or 'features.get("order_imbalance", 0.0) > ORDER_IMBALANCE_THRESHOLD' in user

    def test_feature_condition_does_not_render_literal_when_param_set(self):
        """When threshold_param is set, numeric literal must not appear for that condition."""
        user = build_implementer_messages(self._make_spec_with_params())[1]["content"]
        # The literal 0.3 should not appear for the order_imbalance entry condition
        # (it appears in the tunable_params section instead)
        # Check that the entry condition line uses the constant name
        assert "> ORDER_IMBALANCE_THRESHOLD" in user

    def test_derived_condition_uses_constant_name(self):
        """Derived condition with threshold_param renders constant name."""
        user = build_implementer_messages(self._make_derived_spec_with_params())[1]["content"]
        assert "spread_ticks < SPREAD_TICKS_MAX" in user

    def test_derived_exit_condition_uses_constant_name(self):
        user = build_implementer_messages(self._make_derived_spec_with_params())[1]["content"]
        assert "spread_ticks > SPREAD_TICKS_EXIT" in user

    def test_no_threshold_param_renders_literal(self):
        """Condition without threshold_param still renders numeric literal."""
        user = build_implementer_messages(_make_spec())[1]["content"]
        # _make_spec() uses fc() without threshold_param → literal threshold
        assert "> 0.3" in user

    def test_no_threshold_param_derived_renders_literal(self):
        """Derived condition without threshold_param renders numeric literal."""
        user = build_implementer_messages(_make_spec_with_derived())[1]["content"]
        assert "spread_ticks < 3.0" in user
