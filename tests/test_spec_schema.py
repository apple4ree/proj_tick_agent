"""
tests/test_spec_schema.py
--------------------------
StrategySpec v2.2 schema round-trip and contract tests.
"""
from __future__ import annotations

import pytest

from strategy_loop.spec_schema import (
    DerivedFeature,
    SpecCondition,
    StrategySpec,
    TunableParam,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def fc(source: str, op: str, threshold: float) -> SpecCondition:
    """Short-hand: feature-type condition."""
    return SpecCondition(source_type="feature", source=source, op=op, threshold=threshold)


def dc(derived: str, op: str, threshold: float) -> SpecCondition:
    """Short-hand: derived-feature-type condition."""
    return SpecCondition(source_type="derived_feature", source=derived, op=op, threshold=threshold)


# ── DerivedFeature ────────────────────────────────────────────────────────────

class TestDerivedFeature:
    def test_to_dict_round_trip(self):
        df = DerivedFeature(
            name="spread_ticks",
            formula="(ask_1_price - bid_1_price) / tick_size",
            inputs=["ask_1_price", "bid_1_price", "tick_size"],
        )
        d = df.to_dict()
        df2 = DerivedFeature.from_dict(d)
        assert df2.name == "spread_ticks"
        assert df2.formula == "(ask_1_price - bid_1_price) / tick_size"
        assert df2.inputs == ["ask_1_price", "bid_1_price", "tick_size"]

    def test_from_dict_missing_inputs_defaults_empty(self):
        df = DerivedFeature.from_dict({"name": "x", "formula": "1.0"})
        assert df.inputs == []

    def test_to_dict_contains_all_keys(self):
        df = DerivedFeature(name="x", formula="y", inputs=["ask_1_price"])
        d = df.to_dict()
        assert set(d.keys()) == {"name", "formula", "inputs"}


# ── SpecCondition ─────────────────────────────────────────────────────────────

class TestSpecCondition:
    def test_feature_type_round_trip(self):
        c = fc("order_imbalance", ">", 0.3)
        d = c.to_dict()
        assert d["source_type"] == "feature"
        assert d["source"] == "order_imbalance"
        c2 = SpecCondition.from_dict(d)
        assert c2.source_type == "feature"
        assert c2.source == "order_imbalance"
        assert c2.op == ">"
        assert c2.threshold == pytest.approx(0.3)

    def test_derived_feature_type_round_trip(self):
        c = dc("spread_ticks", "<", 2.0)
        d = c.to_dict()
        assert d["source_type"] == "derived_feature"
        assert d["source"] == "spread_ticks"
        c2 = SpecCondition.from_dict(d)
        assert c2.source_type == "derived_feature"
        assert c2.source == "spread_ticks"

    def test_backward_compat_old_feature_dict(self):
        """v2.1 dict with 'feature' key is normalized to source_type='feature'."""
        c = SpecCondition.from_dict(
            {"feature": "spread_bps", "op": "<", "threshold": "50.0"}
        )
        assert c.source_type == "feature"
        assert c.source == "spread_bps"
        assert isinstance(c.threshold, float)
        assert c.threshold == pytest.approx(50.0)

    def test_feature_property_backward_compat(self):
        """c.feature still returns c.source for feature-type conditions."""
        c = fc("order_imbalance", ">", 0.3)
        assert c.feature == "order_imbalance"

    def test_to_dict_uses_source_type_source_keys(self):
        """New canonical to_dict() output uses source_type + source."""
        c = fc("order_imbalance", ">", 0.3)
        d = c.to_dict()
        assert "source_type" in d
        assert "source" in d
        assert "feature" not in d


# ── TunableParam ─────────────────────────────────────────────────────────────

class TestTunableParam:
    def test_to_dict_round_trip(self):
        p = TunableParam(
            name="ORDER_IMBALANCE_THRESHOLD", default=0.3,
            type="float", range=(0.1, 0.9),
        )
        d = p.to_dict()
        p2 = TunableParam.from_dict(d)
        assert p2.name == "ORDER_IMBALANCE_THRESHOLD"
        assert p2.default == pytest.approx(0.3)
        assert p2.type == "float"
        assert p2.range == pytest.approx((0.1, 0.9))

    def test_range_stored_as_tuple(self):
        p = TunableParam.from_dict(
            {"name": "X", "default": 1.0, "type": "int", "range": [5, 120]}
        )
        assert isinstance(p.range, tuple)
        assert p.range == (5.0, 120.0)


# ── StrategySpec ─────────────────────────────────────────────────────────────

def _make_minimal_spec() -> StrategySpec:
    return StrategySpec(
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
        features_used=["order_imbalance", "spread_bps"],
        rationale="Buy on sustained buy-side pressure.",
    )


def _make_spec_with_derived() -> StrategySpec:
    """Spec that uses a derived feature in an entry condition."""
    return StrategySpec(
        version="2.2",
        archetype=1,
        archetype_name="liquidity imbalance continuation",
        derived_features=[
            DerivedFeature(
                name="spread_ticks",
                formula="(ask_1_price - bid_1_price) / tick_size",
                inputs=["ask_1_price", "bid_1_price", "tick_size"],
            )
        ],
        entry_conditions=[
            fc("order_imbalance", ">", 0.3),
            dc("spread_ticks", "<", 3.0),
        ],
        exit_time_ticks=20,
        exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
        features_used=["order_imbalance"],
        rationale="Spread-ticks filtered imbalance.",
    )


class TestStrategySpec:
    def test_round_trip_from_dict_to_dict(self):
        spec = _make_minimal_spec()
        d = spec.to_dict()
        spec2 = StrategySpec.from_dict(d)

        assert spec2.version == "2.2"
        assert spec2.archetype == 1
        assert spec2.archetype_name == "liquidity imbalance continuation"
        assert len(spec2.entry_conditions) == 2
        assert spec2.exit_time_ticks == 20
        assert len(spec2.exit_signal_conditions) == 1
        assert len(spec2.tunable_params) == 2
        assert spec2.features_used == ["order_imbalance", "spread_bps"]

    def test_from_dict_none_archetype(self):
        d = _make_minimal_spec().to_dict()
        d["archetype"] = None
        spec = StrategySpec.from_dict(d)
        assert spec.archetype is None

    def test_from_dict_missing_optional_fields_use_defaults(self):
        spec = StrategySpec.from_dict({
            "entry_conditions": [
                {"feature": "order_imbalance", "op": ">", "threshold": 0.3}
            ],
        })
        assert spec.version == "2.3"   # default is now 2.3
        assert spec.archetype is None
        assert spec.exit_time_ticks == 20
        assert spec.exit_signal_conditions == []
        assert spec.tunable_params == []
        assert spec.derived_features == []
        assert spec.features_used == []
        assert spec.rationale == ""

    def test_from_dict_v21_old_condition_shape(self):
        """from_dict accepts v2.1 'feature' key in conditions."""
        d = {
            "version": "2.1",
            "entry_conditions": [
                {"feature": "order_imbalance", "op": ">", "threshold": 0.3}
            ],
        }
        spec = StrategySpec.from_dict(d)
        assert spec.entry_conditions[0].source_type == "feature"
        assert spec.entry_conditions[0].source == "order_imbalance"

    def test_to_dict_contains_derived_features_key(self):
        d = _make_minimal_spec().to_dict()
        assert "derived_features" in d

    def test_derived_features_round_trip(self):
        spec = _make_spec_with_derived()
        d = spec.to_dict()
        spec2 = StrategySpec.from_dict(d)
        assert len(spec2.derived_features) == 1
        df = spec2.derived_features[0]
        assert df.name == "spread_ticks"
        assert "ask_1_price" in df.inputs
        assert "tick_size" in df.inputs

    # ── all_referenced_features ───────────────────────────────────────

    def test_all_referenced_features_feature_conditions(self):
        spec = _make_minimal_spec()
        all_f = spec.all_referenced_features()
        assert "order_imbalance" in all_f
        assert "spread_bps" in all_f

    def test_all_referenced_features_derived_inputs_included(self):
        """Derived feature inputs (raw BUILTIN_FEATURES) are included."""
        spec = _make_spec_with_derived()
        all_f = spec.all_referenced_features()
        assert "ask_1_price" in all_f
        assert "bid_1_price" in all_f
        assert "tick_size" in all_f

    def test_all_referenced_features_derived_name_not_included(self):
        """Derived feature NAMES are not in the returned set — only raw inputs."""
        spec = _make_spec_with_derived()
        all_f = spec.all_referenced_features()
        assert "spread_ticks" not in all_f

    def test_all_referenced_features_derived_condition_source_not_included(self):
        """source of a derived_feature condition is not in all_referenced_features()."""
        spec = _make_spec_with_derived()
        all_f = spec.all_referenced_features()
        # "spread_ticks" is a derived name, not a builtin feature
        assert "spread_ticks" not in all_f

    def test_all_referenced_features_is_set(self):
        spec = _make_minimal_spec()
        assert isinstance(spec.all_referenced_features(), set)

    def test_derived_feature_names_returns_correct_set(self):
        spec = _make_spec_with_derived()
        names = spec.derived_feature_names()
        assert names == {"spread_ticks"}

    def test_all_referenced_features_exit_conditions_included(self):
        """Features only in exit_signal_conditions are included."""
        spec = StrategySpec(
            entry_conditions=[fc("order_imbalance", ">", 0.3)],
            exit_signal_conditions=[fc("depth_imbalance", "<", -0.1)],
        )
        all_f = spec.all_referenced_features()
        assert "depth_imbalance" in all_f

    def test_to_dict_contains_all_expected_keys(self):
        d = _make_minimal_spec().to_dict()
        for key in (
            "version", "archetype", "archetype_name",
            "entry_conditions", "exit_time_ticks", "exit_signal_conditions",
            "tunable_params", "derived_features", "features_used", "rationale",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_entry_conditions_are_dicts(self):
        d = _make_minimal_spec().to_dict()
        for c in d["entry_conditions"]:
            assert isinstance(c, dict)
            assert "source_type" in c and "source" in c and "op" in c and "threshold" in c

    def test_default_version_is_23(self):
        """Default StrategySpec.version is '2.3'."""
        assert StrategySpec().version == "2.3"


# ── SpecCondition threshold_param ─────────────────────────────────────────────

class TestThresholdParam:
    def test_threshold_param_round_trip(self):
        c = SpecCondition(
            source_type="feature", source="order_imbalance",
            op=">", threshold=0.3, threshold_param="ORDER_IMBALANCE_THRESHOLD",
        )
        d = c.to_dict()
        assert d["threshold_param"] == "ORDER_IMBALANCE_THRESHOLD"
        c2 = SpecCondition.from_dict(d)
        assert c2.threshold_param == "ORDER_IMBALANCE_THRESHOLD"

    def test_threshold_param_none_not_in_dict(self):
        """When threshold_param is None, to_dict() does not include the key."""
        c = fc("order_imbalance", ">", 0.3)
        d = c.to_dict()
        assert "threshold_param" not in d

    def test_threshold_param_default_is_none(self):
        c = fc("order_imbalance", ">", 0.3)
        assert c.threshold_param is None

    def test_from_dict_reads_threshold_param(self):
        d = {
            "source_type": "feature", "source": "spread_bps",
            "op": "<", "threshold": 50.0, "threshold_param": "SPREAD_MAX_BPS",
        }
        c = SpecCondition.from_dict(d)
        assert c.threshold_param == "SPREAD_MAX_BPS"

    def test_from_dict_missing_threshold_param_is_none(self):
        d = {"source_type": "feature", "source": "order_imbalance", "op": ">", "threshold": 0.3}
        c = SpecCondition.from_dict(d)
        assert c.threshold_param is None

    def test_backward_compat_v21_threshold_param_none(self):
        """v2.1 dict has no threshold_param → from_dict gives None."""
        c = SpecCondition.from_dict({"feature": "order_imbalance", "op": ">", "threshold": 0.3})
        assert c.threshold_param is None


# ── effective_condition_features ──────────────────────────────────────────────

class TestEffectiveConditionFeatures:
    def test_returns_feature_condition_sources(self):
        spec = _make_minimal_spec()
        eff = spec.effective_condition_features()
        assert "order_imbalance" in eff
        assert "spread_bps" in eff

    def test_excludes_features_used_only(self):
        """features_used entries NOT appearing in conditions must not be included."""
        spec = StrategySpec(
            entry_conditions=[fc("order_imbalance", ">", 0.3)],
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
            features_used=["spread_bps", "order_imbalance_ema"],  # not in conditions
        )
        eff = spec.effective_condition_features()
        assert "spread_bps" not in eff
        assert "order_imbalance_ema" not in eff
        assert "order_imbalance" in eff

    def test_includes_inputs_of_used_derived_features(self):
        """Inputs of derived features actually used in conditions ARE included."""
        spec = _make_spec_with_derived()  # spread_ticks used in entry condition
        eff = spec.effective_condition_features()
        assert "ask_1_price" in eff
        assert "bid_1_price" in eff
        assert "tick_size" in eff

    def test_excludes_inputs_of_unused_derived_features(self):
        """Inputs of declared-but-never-used derived features must NOT be included."""
        spec = StrategySpec(
            derived_features=[
                DerivedFeature(
                    name="unused_derived",
                    formula="ask_1_price / bid_1_price",
                    inputs=["ask_1_price", "bid_1_price"],   # these are unused
                )
            ],
            entry_conditions=[fc("order_imbalance", ">", 0.3)],
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
        )
        eff = spec.effective_condition_features()
        assert "ask_1_price" not in eff
        assert "bid_1_price" not in eff
        assert "order_imbalance" in eff

    def test_derived_name_not_included(self):
        """Derived feature names themselves should not appear in the result set."""
        spec = _make_spec_with_derived()
        eff = spec.effective_condition_features()
        assert "spread_ticks" not in eff

    def test_exit_condition_features_included(self):
        spec = StrategySpec(
            entry_conditions=[fc("order_imbalance", ">", 0.3)],
            exit_signal_conditions=[fc("depth_imbalance", "<", -0.1)],
        )
        eff = spec.effective_condition_features()
        assert "depth_imbalance" in eff

    def test_empty_spec_returns_empty_set(self):
        assert StrategySpec().effective_condition_features() == set()

    def test_returns_set(self):
        assert isinstance(_make_minimal_spec().effective_condition_features(), set)
