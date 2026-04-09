"""
tests/test_precode_eval.py
---------------------------
precode_eval.evaluate_spec() contract tests — StrategySpec v2.2.
"""
from __future__ import annotations

import pytest

from strategy_loop.precode_eval import _GO_THRESHOLD, evaluate_spec
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


def _make_ideal_spec() -> StrategySpec:
    """A near-perfect spec that should score ~1.0 and go=True."""
    return StrategySpec(
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
            TunableParam("SPREAD_MAX_BPS", 50.0, "float", (1.0, 200.0)),
        ],
        features_used=[
            "order_imbalance", "order_imbalance_ema",
            "trade_flow_imbalance", "spread_bps",
        ],
        rationale="Buy on buy pressure.",
    )


# ── overall scoring ───────────────────────────────────────────────────────────

class TestOverallScoring:
    def test_ideal_spec_go_true(self):
        assert evaluate_spec(_make_ideal_spec()).go is True

    def test_ideal_spec_overall_above_threshold(self):
        assert evaluate_spec(_make_ideal_spec()).overall >= _GO_THRESHOLD

    def test_all_five_dimensions_present(self):
        pce = evaluate_spec(_make_ideal_spec())
        for dim in (
            "feature_validity", "economic_plausibility",
            "exit_completeness", "param_optunability",
            "archetype_alignment",
        ):
            assert dim in pce.scores, f"Missing dimension: {dim}"

    def test_scores_are_in_zero_to_one(self):
        pce = evaluate_spec(_make_ideal_spec())
        for dim, score in pce.scores.items():
            assert 0.0 <= score <= 1.0, f"{dim}={score} not in [0, 1]"

    def test_overall_is_mean_of_five(self):
        pce = evaluate_spec(_make_ideal_spec())
        expected = sum(pce.scores.values()) / 5
        assert pce.overall == pytest.approx(expected, abs=1e-6)

    def test_to_dict_keys(self):
        d = evaluate_spec(_make_ideal_spec()).to_dict()
        for key in ("version", "scores", "overall", "go", "notes"):
            assert key in d


# ── feature_validity ──────────────────────────────────────────────────────────

class TestFeatureValidity:
    def test_all_valid_features_score_1(self):
        assert evaluate_spec(_make_ideal_spec()).scores["feature_validity"] == pytest.approx(1.0)

    def test_unknown_features_reduce_score(self):
        spec = _make_ideal_spec()
        spec.features_used = ["order_imbalance", "totally_fake_feature_xyz"]
        spec.entry_conditions = [fc("totally_fake_feature_xyz", ">", 0.3)]
        pce = evaluate_spec(spec)
        assert pce.scores["feature_validity"] < 1.0

    def test_no_features_scores_zero(self):
        spec = StrategySpec()
        pce = evaluate_spec(spec)
        assert pce.scores["feature_validity"] == pytest.approx(0.0)

    def test_derived_inputs_count_as_valid_builtin_features(self):
        """Derived feature inputs are BUILTIN_FEATURES and raise feature_validity."""
        spec = StrategySpec(
            archetype=1,
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
        )
        pce = evaluate_spec(spec)
        # All referenced builtin features exist → feature_validity = 1.0
        assert pce.scores["feature_validity"] == pytest.approx(1.0)

    def test_derived_inputs_aggregated_for_archetype_alignment(self):
        """Derived feature inputs count toward archetype alignment scoring."""
        # Archetype 1 canonical: order_imbalance, order_imbalance_ema, trade_flow_imbalance,
        #                        order_imbalance_delta, spread_bps
        # Use spread_bps via a derived feature input
        spec = StrategySpec(
            archetype=1,
            derived_features=[
                DerivedFeature(
                    name="spread_ticks",
                    formula="(ask_1_price - bid_1_price) / tick_size",
                    inputs=["ask_1_price", "bid_1_price", "tick_size"],
                ),
                DerivedFeature(
                    name="spread_bps_excess",
                    formula="spread_bps - spread_bps_ema",
                    inputs=["spread_bps"],   # ← canonical archetype-1 feature
                ),
            ],
            entry_conditions=[
                fc("order_imbalance", ">", 0.3),
                dc("spread_bps_excess", ">", 0.0),
            ],
            exit_time_ticks=20,
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
            features_used=["order_imbalance", "order_imbalance_ema"],
        )
        pce = evaluate_spec(spec)
        # all_referenced_features should include spread_bps (from derived input)
        all_f = spec.all_referenced_features()
        assert "spread_bps" in all_f
        # archetype_alignment should get credit for spread_bps
        assert pce.scores["archetype_alignment"] > 0.0


# ── economic_plausibility ─────────────────────────────────────────────────────

class TestEconomicPlausibility:
    def test_with_cost_filter_and_archetype_scores_1(self):
        assert evaluate_spec(_make_ideal_spec()).scores["economic_plausibility"] == pytest.approx(1.0)

    def test_missing_cost_filter_scores_0_5(self):
        spec = _make_ideal_spec()
        spec.entry_conditions = [fc("order_imbalance", ">", 0.3)]
        spec.features_used = ["order_imbalance"]
        pce = evaluate_spec(spec)
        assert pce.scores["economic_plausibility"] == pytest.approx(0.5)
        assert any("cost" in n.lower() for n in pce.notes)

    def test_cost_filter_via_derived_input(self):
        """spread_bps used as a derived input counts as cost-filter."""
        spec = StrategySpec(
            archetype=1,
            derived_features=[
                DerivedFeature("spread_excess", "spread_bps - 10", ["spread_bps"])
            ],
            entry_conditions=[
                fc("order_imbalance", ">", 0.3),
                dc("spread_excess", "<", 5.0),
            ],
            exit_time_ticks=20,
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
            features_used=["order_imbalance"],
        )
        pce = evaluate_spec(spec)
        assert pce.scores["economic_plausibility"] == pytest.approx(1.0)

    def test_missing_archetype_scores_0_5(self):
        spec = _make_ideal_spec()
        spec.archetype = None
        assert evaluate_spec(spec).scores["economic_plausibility"] == pytest.approx(0.5)

    def test_missing_both_scores_0(self):
        spec = _make_ideal_spec()
        spec.archetype = None
        spec.entry_conditions = [fc("order_imbalance", ">", 0.3)]
        spec.features_used = ["order_imbalance"]
        assert evaluate_spec(spec).scores["economic_plausibility"] == pytest.approx(0.0)


# ── exit_completeness ─────────────────────────────────────────────────────────

class TestExitCompleteness:
    def test_full_exit_scores_1(self):
        assert evaluate_spec(_make_ideal_spec()).scores["exit_completeness"] == pytest.approx(1.0)

    def test_no_signal_exit_scores_0_5(self):
        spec = _make_ideal_spec()
        spec.exit_signal_conditions = []
        assert evaluate_spec(spec).scores["exit_completeness"] == pytest.approx(0.5)

    def test_short_time_exit_scores_0_5(self):
        spec = _make_ideal_spec()
        spec.exit_time_ticks = 3
        assert evaluate_spec(spec).scores["exit_completeness"] == pytest.approx(0.5)

    def test_no_exits_scores_0(self):
        spec = _make_ideal_spec()
        spec.exit_time_ticks = 0
        spec.exit_signal_conditions = []
        assert evaluate_spec(spec).scores["exit_completeness"] == pytest.approx(0.0)


# ── param_optunability ────────────────────────────────────────────────────────

class TestParamOptunability:
    def test_all_good_params_score_1(self):
        assert evaluate_spec(_make_ideal_spec()).scores["param_optunability"] == pytest.approx(1.0)

    def test_no_params_scores_0_5(self):
        spec = _make_ideal_spec()
        spec.tunable_params = []
        assert evaluate_spec(spec).scores["param_optunability"] == pytest.approx(0.5)

    def test_lowercase_param_reduces_score(self):
        spec = _make_ideal_spec()
        spec.tunable_params = [TunableParam("bad_name", 0.3, "float", (0.1, 0.9))]
        assert evaluate_spec(spec).scores["param_optunability"] < 1.0


# ── archetype_alignment ───────────────────────────────────────────────────────

class TestArchetypeAlignment:
    def test_canonical_features_score_high(self):
        pce = evaluate_spec(_make_ideal_spec())
        # archetype 1 canonical (5 total): order_imbalance, order_imbalance_ema,
        #   trade_flow_imbalance, order_imbalance_delta, spread_bps
        # effective_condition_features: order_imbalance (entry/exit), spread_bps (entry) → 2/5
        # (features_used extras like order_imbalance_ema, trade_flow_imbalance
        #  are excluded — they don't appear in conditions)
        assert pce.scores["archetype_alignment"] == pytest.approx(2 / 5)

    def test_no_archetype_scores_0_5(self):
        spec = _make_ideal_spec()
        spec.archetype = None
        assert evaluate_spec(spec).scores["archetype_alignment"] == pytest.approx(0.5)

    def test_no_canonical_features_scores_0(self):
        spec = _make_ideal_spec()
        spec.archetype = 1
        spec.entry_conditions = [fc("depth_imbalance_ema", ">", 0.3)]
        spec.exit_signal_conditions = [fc("depth_imbalance_ema", "<", -0.1)]
        spec.features_used = ["depth_imbalance_ema"]
        spec.derived_features = []
        pce = evaluate_spec(spec)
        assert pce.scores["archetype_alignment"] == pytest.approx(0.0)
        assert any("archetype" in n.lower() or "canonical" in n.lower() for n in pce.notes)

    def test_raw_l1_derived_inputs_count_toward_alignment(self):
        """Derived feature inputs from L1 raw features count if they overlap canonical."""
        # Archetype 1 has spread_bps as canonical
        spec = StrategySpec(
            archetype=1,
            derived_features=[
                DerivedFeature("spread_ticks", "(ask_1_price-bid_1_price)/tick_size",
                               ["ask_1_price", "bid_1_price", "tick_size"])
            ],
            entry_conditions=[
                fc("order_imbalance", ">", 0.3),
                dc("spread_ticks", "<", 3.0),
            ],
            exit_time_ticks=20,
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
            features_used=["order_imbalance", "spread_bps"],
        )
        pce = evaluate_spec(spec)
        # effective_condition_features: order_imbalance (direct) + ask/bid/tick_size
        # (inputs of spread_ticks derived used in condition); order_imbalance overlaps canonical
        assert pce.scores["archetype_alignment"] > 0.0


# ── effective_condition_features inflation prevention ─────────────────────────

class TestInflationPrevention:
    def test_unused_features_used_do_not_inflate_feature_validity(self):
        """features_used extras not in conditions must not contribute to feature_validity."""
        # Only order_imbalance appears in conditions; 'fake_unused_xyz' only in features_used
        # But BUILTIN check requires real features — use a real feature that's not in conditions
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        extra = sorted(BUILTIN_FEATURES - {"order_imbalance", "spread_bps"})[0]
        spec = StrategySpec(
            archetype=1,
            entry_conditions=[fc("order_imbalance", ">", 0.3)],
            exit_time_ticks=20,
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
            features_used=["order_imbalance", extra],  # extra not in conditions
        )
        pce = evaluate_spec(spec)
        # effective_condition_features = {order_imbalance} only → valid
        assert pce.scores["feature_validity"] == pytest.approx(1.0)

    def test_unused_derived_inputs_do_not_inflate_archetype_alignment(self):
        """Inputs of declared-but-never-used derived features must not inflate alignment."""
        # Declare a derived feature with canonical archetype-1 inputs but never use it
        spec = StrategySpec(
            archetype=1,
            derived_features=[
                DerivedFeature(
                    name="unused_derived",
                    formula="order_imbalance_ema + trade_flow_imbalance",
                    inputs=["order_imbalance_ema", "trade_flow_imbalance"],  # canonical
                )
            ],
            entry_conditions=[fc("order_imbalance", ">", 0.3)],
            exit_time_ticks=20,
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
            features_used=["order_imbalance"],
        )
        pce = evaluate_spec(spec)
        # effective: {order_imbalance} only (unused_derived's inputs excluded)
        # archetype 1 canonical overlap = {order_imbalance} = 1/5
        assert pce.scores["archetype_alignment"] == pytest.approx(1 / 5)

    def test_used_derived_inputs_do_contribute_to_alignment(self):
        """Inputs of derived features USED in conditions do count."""
        spec = StrategySpec(
            archetype=1,
            derived_features=[
                DerivedFeature(
                    name="combo",
                    formula="order_imbalance_ema + trade_flow_imbalance",
                    inputs=["order_imbalance_ema", "trade_flow_imbalance"],  # canonical
                )
            ],
            entry_conditions=[
                fc("order_imbalance", ">", 0.3),
                dc("combo", ">", 0.0),   # used in condition
            ],
            exit_time_ticks=20,
            exit_signal_conditions=[fc("order_imbalance", "<", -0.05)],
        )
        pce = evaluate_spec(spec)
        # effective: order_imbalance + ema + tfi = 3/5 canonical overlap
        assert pce.scores["archetype_alignment"] == pytest.approx(3 / 5)


# ── go threshold ──────────────────────────────────────────────────────────────

class TestGoThreshold:
    def test_weak_spec_go_false(self):
        pce = evaluate_spec(StrategySpec())
        assert pce.go is False

    def test_go_threshold_is_correct_constant(self):
        assert _GO_THRESHOLD == pytest.approx(0.50)
