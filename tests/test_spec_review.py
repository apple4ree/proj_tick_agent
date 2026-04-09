"""
tests/test_spec_review.py
--------------------------
spec_review.review_spec() contract tests — StrategySpec v2.2.
"""
from __future__ import annotations

import pytest

from strategy_loop.spec_schema import (
    DerivedFeature,
    SpecCondition,
    StrategySpec,
    TunableParam,
)
from strategy_loop.spec_review import review_spec


# ── helpers ───────────────────────────────────────────────────────────────────

def fc(source: str, op: str, threshold: float) -> SpecCondition:
    return SpecCondition(source_type="feature", source=source, op=op, threshold=threshold)


def dc(derived: str, op: str, threshold: float) -> SpecCondition:
    return SpecCondition(source_type="derived_feature", source=derived, op=op, threshold=threshold)


def fc_p(source: str, op: str, threshold: float, param: str) -> SpecCondition:
    """Feature condition with threshold_param set (for v2.3 specs)."""
    return SpecCondition(
        source_type="feature", source=source, op=op,
        threshold=threshold, threshold_param=param,
    )


def dc_p(derived: str, op: str, threshold: float, param: str) -> SpecCondition:
    """Derived-feature condition with threshold_param set (for v2.3 specs)."""
    return SpecCondition(
        source_type="derived_feature", source=derived, op=op,
        threshold=threshold, threshold_param=param,
    )


def _make_valid_spec(**overrides) -> StrategySpec:
    """Build a valid v2.2 spec (no threshold_param required)."""
    base = dict(
        version="2.2",
        archetype=1,
        archetype_name="liquidity imbalance continuation",
        entry_conditions=[
            fc("order_imbalance", ">", 0.3),
            fc("spread_bps", "<", 50.0),
        ],
        exit_time_ticks=20,
        exit_signal_conditions=[
            fc("order_imbalance", "<", -0.05),
        ],
        tunable_params=[
            TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.3, "float", (0.1, 0.9)),
            TunableParam("HOLDING_TICKS_EXIT", 20.0, "int", (5.0, 120.0)),
        ],
        derived_features=[],
        features_used=["order_imbalance", "spread_bps"],
        rationale="Buy on sustained buy-side pressure.",
    )
    base.update(overrides)
    return StrategySpec(**base)


def _make_v23_spec(**overrides) -> StrategySpec:
    """Build a valid v2.3 spec with threshold_param on every condition."""
    base = dict(
        version="2.3",
        archetype=1,
        archetype_name="liquidity imbalance continuation",
        entry_conditions=[
            fc_p("order_imbalance", ">", 0.3, "ORDER_IMBALANCE_THRESHOLD"),
            fc_p("spread_bps", "<", 50.0, "SPREAD_MAX_BPS"),
        ],
        exit_time_ticks=20,
        exit_signal_conditions=[
            fc_p("order_imbalance", "<", -0.05, "REVERSAL_THRESHOLD"),
        ],
        tunable_params=[
            TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.3, "float", (0.1, 0.9)),
            TunableParam("SPREAD_MAX_BPS", 50.0, "float", (1.0, 200.0)),
            TunableParam("HOLDING_TICKS_EXIT", 20.0, "int", (5.0, 120.0)),
            TunableParam("REVERSAL_THRESHOLD", -0.05, "float", (-0.9, 0.9)),
        ],
        derived_features=[],
        features_used=["order_imbalance", "spread_bps"],
        rationale="Buy on sustained buy-side pressure.",
    )
    base.update(overrides)
    return StrategySpec(**base)


# ── valid spec ────────────────────────────────────────────────────────────────

class TestValidSpec:
    def test_valid_spec_passes(self):
        review = review_spec(_make_valid_spec())
        assert review.valid is True
        assert review.errors == []
        assert review.normalized_spec is not None

    def test_normalized_spec_is_same_object(self):
        spec = _make_valid_spec()
        review = review_spec(spec)
        assert review.normalized_spec is spec

    def test_to_dict_valid(self):
        review = review_spec(_make_valid_spec())
        d = review.to_dict()
        assert d["valid"] is True
        assert d["errors"] == []
        assert d["normalized_spec"] is not None


# ── entry conditions ──────────────────────────────────────────────────────────

class TestEntryConditions:
    def test_empty_entry_conditions_fails(self):
        spec = _make_valid_spec(entry_conditions=[])
        review = review_spec(spec)
        assert review.valid is False
        assert any("entry_conditions" in e for e in review.errors)

    def test_invalid_op_fails(self):
        spec = _make_valid_spec(
            entry_conditions=[fc("order_imbalance", ">>", 0.3)]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("op" in e for e in review.errors)

    def test_unknown_feature_in_entry_fails(self):
        spec = _make_valid_spec(
            entry_conditions=[fc("nonexistent_feature_xyz", ">", 0.3)]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert "nonexistent_feature_xyz" in review.unknown_features

    def test_invalid_source_type_fails(self):
        cond = SpecCondition(source_type="nonsense", source="foo", op=">", threshold=0.3)
        spec = _make_valid_spec(entry_conditions=[cond])
        review = review_spec(spec)
        assert review.valid is False
        assert any("source_type" in e for e in review.errors)


# ── exit conditions ───────────────────────────────────────────────────────────

class TestExitConditions:
    def test_exit_time_zero_fails(self):
        spec = _make_valid_spec(exit_time_ticks=0)
        review = review_spec(spec)
        assert review.valid is False

    def test_exit_time_4_warns(self):
        spec = _make_valid_spec(exit_time_ticks=4)
        review = review_spec(spec)
        assert review.valid is True   # warning, not error
        assert any("exit_time_ticks" in w for w in review.warnings)

    def test_empty_exit_signal_conditions_warns(self):
        spec = _make_valid_spec(exit_signal_conditions=[])
        review = review_spec(spec)
        assert review.valid is True
        assert any("exit_signal_conditions" in w for w in review.warnings)

    def test_invalid_op_in_exit_fails(self):
        spec = _make_valid_spec(
            exit_signal_conditions=[fc("order_imbalance", "INVALID", -0.05)]
        )
        review = review_spec(spec)
        assert review.valid is False

    def test_unknown_feature_in_exit_fails(self):
        spec = _make_valid_spec(
            exit_signal_conditions=[fc("bogus_exit_feature", "<", 0.0)]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert "bogus_exit_feature" in review.unknown_features


# ── derived features ──────────────────────────────────────────────────────────

class TestDerivedFeatures:
    def _make_spec_with_derived(self, **overrides) -> StrategySpec:
        dfs = [
            DerivedFeature(
                name="spread_ticks",
                formula="(ask_1_price - bid_1_price) / tick_size",
                inputs=["ask_1_price", "bid_1_price", "tick_size"],
            )
        ]
        return _make_valid_spec(
            derived_features=dfs,
            entry_conditions=[
                fc("order_imbalance", ">", 0.3),
                dc("spread_ticks", "<", 3.0),
            ],
            **overrides,
        )

    def test_valid_derived_feature_passes(self):
        spec = self._make_spec_with_derived()
        review = review_spec(spec)
        assert review.valid is True

    def test_duplicate_derived_name_fails(self):
        spec = _make_valid_spec(
            derived_features=[
                DerivedFeature("spread_ticks", "a / b", ["ask_1_price", "bid_1_price"]),
                DerivedFeature("spread_ticks", "c / d", ["ask_1_price", "tick_size"]),
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("duplicate" in e for e in review.errors)

    def test_derived_name_collides_with_builtin_fails(self):
        """Derived feature name must not shadow a BUILTIN_FEATURE."""
        spec = _make_valid_spec(
            derived_features=[
                DerivedFeature("order_imbalance", "a + b", ["ask_1_price", "bid_1_price"])
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("collides" in e for e in review.errors)

    def test_derived_input_not_in_builtin_fails(self):
        spec = _make_valid_spec(
            derived_features=[
                DerivedFeature("my_signal", "x + y", ["NOT_A_BUILTIN_INPUT_ZZZ"])
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("NOT_A_BUILTIN_INPUT_ZZZ" in e for e in review.errors)

    def test_condition_references_undeclared_derived_fails(self):
        """Condition uses source_type='derived_feature' with undeclared name."""
        spec = _make_valid_spec(
            derived_features=[],   # none declared
            entry_conditions=[
                fc("order_imbalance", ">", 0.3),
                dc("undeclared_derived", "<", 3.0),   # not declared
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("undeclared_derived" in e for e in review.errors)

    def test_condition_references_declared_derived_passes(self):
        spec = self._make_spec_with_derived()
        review = review_spec(spec)
        assert review.valid is True

    def test_derived_feature_in_exit_conditions_passes(self):
        dfs = [DerivedFeature("my_exit_sig", "a - b", ["order_imbalance", "depth_imbalance"])]
        spec = _make_valid_spec(
            derived_features=dfs,
            exit_signal_conditions=[dc("my_exit_sig", "<", -0.1)],
        )
        review = review_spec(spec)
        assert review.valid is True


# ── tunable params ────────────────────────────────────────────────────────────

class TestTunableParams:
    def test_lowercase_param_name_warns(self):
        spec = _make_valid_spec(
            tunable_params=[TunableParam("lower_case_param", 0.3, "float", (0.0, 1.0))]
        )
        review = review_spec(spec)
        assert review.valid is True
        assert any("lower_case_param" in w for w in review.warnings)

    def test_invalid_range_warns(self):
        spec = _make_valid_spec(
            tunable_params=[TunableParam("GOOD_PARAM", 0.3, "float", (0.9, 0.1))]
        )
        review = review_spec(spec)
        assert review.valid is True
        assert any("range" in w or "invalid" in w.lower() for w in review.warnings)


# ── archetype ─────────────────────────────────────────────────────────────────

class TestArchetype:
    def test_valid_archetypes_pass(self):
        for at in (1, 2, 3, 4):
            spec = _make_valid_spec(archetype=at)
            assert review_spec(spec).valid is True

    def test_none_archetype_passes(self):
        assert review_spec(_make_valid_spec(archetype=None)).valid is True

    def test_invalid_archetype_fails(self):
        spec = _make_valid_spec(archetype=99)
        review = review_spec(spec)
        assert review.valid is False
        assert any("archetype" in e for e in review.errors)


# ── features_used ─────────────────────────────────────────────────────────────

class TestFeaturesUsed:
    def test_unknown_feature_in_features_used_fails(self):
        spec = _make_valid_spec(features_used=["order_imbalance", "no_such_feature_abc"])
        review = review_spec(spec)
        assert review.valid is False
        assert "no_such_feature_abc" in review.unknown_features

    def test_all_builtin_features_accepted(self):
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        spec = _make_valid_spec(features_used=list(BUILTIN_FEATURES)[:5])
        assert review_spec(spec).valid is True


# ── threshold_param linkage (v2.3) ────────────────────────────────────────────

class TestThresholdParamLinkage:
    def test_v23_valid_spec_passes(self):
        """v2.3 spec with all threshold_params set and declared is valid."""
        review = review_spec(_make_v23_spec())
        assert review.valid is True
        assert review.errors == []

    def test_v23_missing_threshold_param_is_error(self):
        """v2.3 condition without threshold_param is an error."""
        spec = _make_v23_spec(
            entry_conditions=[
                fc("order_imbalance", ">", 0.3),  # no threshold_param
                fc_p("spread_bps", "<", 50.0, "SPREAD_MAX_BPS"),
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("threshold_param" in e for e in review.errors)

    def test_v22_missing_threshold_param_is_warning_not_error(self):
        """v2.2 condition without threshold_param is only a warning."""
        spec = _make_valid_spec()  # v2.2, no threshold_params set
        review = review_spec(spec)
        assert review.valid is True
        assert any("threshold_param" in w for w in review.warnings)

    def test_v21_missing_threshold_param_is_warning_not_error(self):
        """v2.1 condition without threshold_param is only a warning."""
        spec = _make_valid_spec(version="2.1")
        review = review_spec(spec)
        assert review.valid is True
        assert any("threshold_param" in w for w in review.warnings)

    def test_unknown_threshold_param_is_error(self):
        """threshold_param value must match a declared tunable_params.name."""
        spec = _make_v23_spec(
            entry_conditions=[
                fc_p("order_imbalance", ">", 0.3, "NONEXISTENT_PARAM"),
                fc_p("spread_bps", "<", 50.0, "SPREAD_MAX_BPS"),
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("NONEXISTENT_PARAM" in e for e in review.errors)

    def test_threshold_param_matches_declared_param(self):
        """When threshold_param references a valid tunable_params.name, spec is valid."""
        spec = _make_v23_spec()
        review = review_spec(spec)
        assert review.valid is True

    def test_duplicate_tunable_param_name_is_error(self):
        """Duplicate tunable_param names must be an error."""
        spec = _make_valid_spec(
            tunable_params=[
                TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.3, "float", (0.1, 0.9)),
                TunableParam("ORDER_IMBALANCE_THRESHOLD", 0.5, "float", (0.1, 0.9)),
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("duplicate" in e for e in review.errors)

    def test_v23_exit_condition_missing_threshold_param_is_error(self):
        """v2.3 exit condition without threshold_param → error."""
        spec = _make_v23_spec(
            exit_signal_conditions=[
                fc("order_imbalance", "<", -0.05),  # no threshold_param
            ]
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("threshold_param" in e for e in review.errors)

    def test_v23_derived_feature_condition_threshold_param_enforced(self):
        """v2.3: derived_feature condition also requires threshold_param."""
        from strategy_loop.spec_schema import DerivedFeature
        spec = _make_v23_spec(
            derived_features=[
                DerivedFeature("spread_ticks",
                               "(ask_1_price - bid_1_price) / tick_size",
                               ["ask_1_price", "bid_1_price", "tick_size"])
            ],
            entry_conditions=[
                fc_p("order_imbalance", ">", 0.3, "ORDER_IMBALANCE_THRESHOLD"),
                dc("spread_ticks", "<", 3.0),   # missing threshold_param
            ],
        )
        review = review_spec(spec)
        assert review.valid is False
        assert any("threshold_param" in e for e in review.errors)
