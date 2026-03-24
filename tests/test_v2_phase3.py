"""Tests for v2 Phase 3: exit-first semantics + minimal stateful extensions."""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.ast_nodes import (
    ComparisonExpr,
    ConstExpr,
    StateVarExpr,
    PositionAttrExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    ExecutionAdaptationOverrideV2,
    ExecutionAdaptationRuleV2,
    ExecutionPolicyV2,
    PreconditionV2,
    RegimeV2,
    RiskDegradationActionV2,
    RiskDegradationRuleV2,
    RiskPolicyV2,
    PositionSizingV2,
    StateEventV2,
    StateGuardV2,
    StatePolicyV2,
    StateUpdateV2,
    StrategySpecV2,
)
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_compiler.v2.compiler_v2 import StrategyCompilerV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_generation.v2.templates_v2 import get_v2_template
from strategy_block.strategy_generation.v2.lowering import lower_to_spec_v2
from strategy_block.strategy_registry.registry import StrategyRegistry


@dataclass
class MockLOBLevel:
    price: float = 0.0
    volume: int = 100


@dataclass
class MockLOB:
    mid_price: float | None = 100.0
    best_bid: float = 99.0
    best_ask: float = 101.0
    order_imbalance: float = 0.0
    bid_levels: list = field(default_factory=lambda: [MockLOBLevel(99.0, 100)])
    ask_levels: list = field(default_factory=lambda: [MockLOBLevel(101.0, 100)])


def _mock_state(order_imbalance=0.0, spread_bps=5.0, depth_imbalance=0.0,
                trade_flow_imbalance=0.0, mid_price=100.0):
    state = MagicMock()
    state.symbol = "TEST"
    state.timestamp = "2026-01-01T09:00:00"
    state.lob = MockLOB(mid_price=mid_price, order_imbalance=order_imbalance)
    state.spread_bps = spread_bps
    state.features = {
        "order_imbalance": order_imbalance,
        "spread_bps": spread_bps,
        "depth_imbalance": depth_imbalance,
        "trade_flow_imbalance": trade_flow_imbalance,
    }
    state.trades = None
    return state


def _base_spec(**kw) -> StrategySpecV2:
    defaults = dict(
        name="phase3_test",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(1.0),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="stop",
                        priority=1,
                        condition=ComparisonExpr(feature="order_imbalance", op="<", threshold=-0.2),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            ),
        ],
        risk_policy=RiskPolicyV2(
            max_position=500,
            inventory_cap=1000,
            position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=500),
        ),
    )
    defaults.update(kw)
    return StrategySpecV2(**defaults)


class TestExitFirstSemantics:

    def test_exit_works_when_regime_no_match(self):
        spec = _base_spec(
            regimes=[
                RegimeV2(
                    name="only_narrow_spread",
                    priority=1,
                    when=ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                    entry_policy_refs=["long_entry"],
                    exit_policy_ref="exits",
                ),
            ]
        )
        strategy = StrategyCompilerV2.compile(spec)

        # Enter in matching regime.
        s1 = _mock_state(order_imbalance=0.6, spread_bps=5.0)
        sig1 = strategy.generate_signal(s1)
        assert sig1 is not None

        # Regime no-match now, but exit must still execute.
        s2 = _mock_state(order_imbalance=-0.4, spread_bps=20.0)
        sig2 = strategy.generate_signal(s2)
        assert sig2 is not None
        assert sig2.tags.get("exit_type") == "stop"

    def test_exit_works_when_precondition_fails(self):
        spec = _base_spec(
            preconditions=[
                PreconditionV2(
                    name="tight_spread",
                    condition=ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                )
            ]
        )
        strategy = StrategyCompilerV2.compile(spec)

        s1 = _mock_state(order_imbalance=0.6, spread_bps=5.0)
        assert strategy.generate_signal(s1) is not None

        # precondition fails (spread wide), exit must still execute.
        s2 = _mock_state(order_imbalance=-0.4, spread_bps=20.0)
        sig2 = strategy.generate_signal(s2)
        assert sig2 is not None
        assert sig2.tags.get("exit_type") == "stop"

    def test_exit_works_when_do_not_trade_true(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                do_not_trade_when=ComparisonExpr(feature="spread_bps", op=">", threshold=10.0)
            )
        )
        strategy = StrategyCompilerV2.compile(spec)

        s1 = _mock_state(order_imbalance=0.6, spread_bps=5.0)
        assert strategy.generate_signal(s1) is not None

        # do_not_trade_when true, but in-position exit must still execute.
        s2 = _mock_state(order_imbalance=-0.4, spread_bps=20.0)
        sig2 = strategy.generate_signal(s2)
        assert sig2 is not None
        assert sig2.tags.get("exit_type") == "stop"

    def test_flat_state_still_no_trade_under_gates(self):
        spec = _base_spec(
            preconditions=[
                PreconditionV2(
                    name="tight_spread",
                    condition=ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                )
            ],
            execution_policy=ExecutionPolicyV2(
                do_not_trade_when=ComparisonExpr(feature="spread_bps", op=">", threshold=10.0)
            ),
            regimes=[
                RegimeV2(
                    name="only_narrow_spread",
                    priority=1,
                    when=ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                    entry_policy_refs=["long_entry"],
                    exit_policy_ref="exits",
                ),
            ],
        )
        strategy = StrategyCompilerV2.compile(spec)

        # Flat + gated => no entry signal.
        s = _mock_state(order_imbalance=0.6, spread_bps=20.0)
        assert strategy.generate_signal(s) is None


class TestStatePolicy:

    def test_cooldown_guard_blocks_entry(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={"entry_block": 1.0},
                guards=[
                    StateGuardV2(
                        name="blocker",
                        condition=ComparisonExpr(
                            left=StateVarExpr("entry_block"), op=">", threshold=0.0
                        ),
                        effect="block_entry",
                    )
                ],
                events=[],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=5.0))
        assert sig is None

    def test_on_entry_updates_state_var(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={"entry_count": 0.0},
                guards=[],
                events=[
                    StateEventV2(
                        name="count_entry",
                        on="on_entry",
                        updates=[StateUpdateV2(var="entry_count", op="increment", value=1.0)],
                    )
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=5.0))
        assert sig is not None
        rt = strategy._states["TEST"]
        assert rt.state_vars["entry_count"] == 1.0

    def test_on_exit_loss_increments_loss_streak(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={"loss_streak": 0.0},
                guards=[],
                events=[
                    StateEventV2(
                        name="track_loss",
                        on="on_exit_loss",
                        updates=[StateUpdateV2(var="loss_streak", op="increment", value=1.0)],
                    ),
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)

        # Enter at 100.
        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=5.0, mid_price=100.0)) is not None
        # Exit at lower mid => loss.
        assert strategy.generate_signal(_mock_state(order_imbalance=-0.4, spread_bps=5.0, mid_price=90.0)) is not None

        rt = strategy._states["TEST"]
        assert rt.state_vars["loss_streak"] == 1.0

    def test_on_flatten_reset_works(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={"flat_reset_var": 0.0},
                guards=[],
                events=[
                    StateEventV2(
                        name="set_on_loss",
                        on="on_exit_loss",
                        updates=[StateUpdateV2(var="flat_reset_var", op="set", value=7.0)],
                    ),
                    StateEventV2(
                        name="reset_on_flat",
                        on="on_flatten",
                        updates=[StateUpdateV2(var="flat_reset_var", op="reset")],
                    ),
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)

        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, mid_price=100.0)) is not None
        assert strategy.generate_signal(_mock_state(order_imbalance=-0.4, mid_price=90.0)) is not None
        rt = strategy._states["TEST"]
        assert rt.state_vars["flat_reset_var"] == 0.0


class TestDegradationRules:

    def test_scale_strength_applies(self):
        spec = _base_spec(
            risk_policy=RiskPolicyV2(
                max_position=500,
                inventory_cap=1000,
                position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=500),
                degradation_rules=[
                    RiskDegradationRuleV2(
                        condition=ConstExpr(1.0),
                        action=RiskDegradationActionV2(type="scale_strength", factor=0.5),
                    )
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=5.0))
        assert sig is not None
        assert abs(sig.score - 0.5) < 1e-9

    def test_scale_max_position_applies(self):
        spec = _base_spec(
            risk_policy=RiskPolicyV2(
                max_position=500,
                inventory_cap=1000,
                position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=400),
                degradation_rules=[
                    RiskDegradationRuleV2(
                        condition=ConstExpr(1.0),
                        action=RiskDegradationActionV2(type="scale_max_position", factor=0.5),
                    )
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=5.0))
        assert sig is not None
        rt = strategy._states["TEST"]
        assert abs(rt.position_size - 200.0) < 1e-9

    def test_block_new_entries_applies(self):
        spec = _base_spec(
            risk_policy=RiskPolicyV2(
                max_position=500,
                inventory_cap=1000,
                position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=500),
                degradation_rules=[
                    RiskDegradationRuleV2(
                        condition=ConstExpr(1.0),
                        action=RiskDegradationActionV2(type="block_new_entries"),
                    )
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)
        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=5.0)) is None


class TestExecutionAdaptation:

    def test_execution_overrides_reflected_in_tags(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                placement_mode="adaptive",
                cancel_after_ticks=20,
                max_reprices=3,
                adaptation_rules=[
                    ExecutionAdaptationRuleV2(
                        condition=ComparisonExpr(feature="spread_bps", op=">", threshold=10.0),
                        override=ExecutionAdaptationOverrideV2(
                            placement_mode="passive_only",
                            cancel_after_ticks=5,
                            max_reprices=1,
                        ),
                    )
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=15.0))
        assert sig is not None
        assert sig.tags.get("placement_mode") == "passive_only"
        assert sig.tags.get("cancel_after_ticks") == 5
        assert sig.tags.get("max_reprices") == 1


class TestReviewerPhase3:

    def test_undefined_state_var_detected(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={},
                guards=[
                    StateGuardV2(
                        name="bad_ref",
                        condition=ComparisonExpr(left=StateVarExpr("ghost_var"), op=">", threshold=0.0),
                        effect="block_entry",
                    )
                ],
                events=[],
            )
        )
        result = StrategyReviewerV2().review(spec)
        assert any(i.category == "state_reference_integrity" for i in result.issues)

    def test_deadlock_and_guard_conflict_warning(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={"x": 0.0},
                guards=[
                    StateGuardV2(name="dup", condition=ConstExpr(1.0), effect="block_entry"),
                    StateGuardV2(name="dup", condition=ConstExpr(1.0), effect="block_entry"),
                ],
                events=[],
            )
        )
        result = StrategyReviewerV2().review(spec)
        assert any(i.category == "state_deadlock" for i in result.issues)
        assert any(i.category == "guard_conflict" for i in result.issues)

    def test_degradation_conflict_warning(self):
        spec = _base_spec(
            risk_policy=RiskPolicyV2(
                max_position=500,
                inventory_cap=1000,
                position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=500),
                degradation_rules=[
                    RiskDegradationRuleV2(
                        condition=ConstExpr(1.0),
                        action=RiskDegradationActionV2(type="block_new_entries"),
                    )
                ],
            )
        )
        result = StrategyReviewerV2().review(spec)
        assert any(i.category == "degradation_conflict" for i in result.issues)


class TestGenerationPhase3:

    @pytest.mark.parametrize("name", [
        "stateful_cooldown_momentum",
        "loss_streak_degraded_reversion",
        "latency_adaptive_passive_entry",
    ])
    def test_phase3_template_lower_review_compile(self, name):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        assert spec.validate() == []

        review = StrategyReviewerV2().review(spec)
        assert review.passed

        strategy = compile_strategy(spec)
        assert strategy.name.startswith("CompiledV2:")


class TestPhase3EndToEnd:

    def test_registry_save_load_compile_and_signal_smoke(self, tmp_path):
        template = get_v2_template("loss_streak_degraded_reversion")
        spec = lower_to_spec_v2(template)

        registry = StrategyRegistry(registry_dir=tmp_path)
        registry.save_spec(spec)

        loaded = registry.load_spec(spec.name, spec.version)
        strategy = compile_strategy(loaded)

        sig = strategy.generate_signal(
            _mock_state(order_imbalance=-0.5, spread_bps=10.0)
        )
        assert sig is not None
        assert sig.tags.get("spec_format") == "v2"

class TestPhase3Stabilization:

    def test_exit_event_then_flatten_reset_ordering(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={"loss_marker": 0.0},
                guards=[],
                events=[
                    StateEventV2(
                        name="mark_loss",
                        on="on_exit_loss",
                        updates=[StateUpdateV2(var="loss_marker", op="set", value=1.0)],
                    ),
                    StateEventV2(
                        name="clear_flat",
                        on="on_flatten",
                        updates=[StateUpdateV2(var="loss_marker", op="reset")],
                    ),
                ],
            )
        )
        strategy = StrategyCompilerV2.compile(spec)

        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, mid_price=100.0)) is not None
        assert strategy.generate_signal(_mock_state(order_imbalance=-0.4, mid_price=90.0)) is not None

        rt = strategy._states["TEST"]
        assert rt.state_vars["loss_marker"] == 0.0

    def test_reduce_position_does_not_trigger_flatten_event(self):
        spec = _base_spec(
            exit_policies=[
                ExitPolicyV2(
                    name="exits",
                    rules=[
                        ExitRuleV2(
                            name="partial_reduce",
                            priority=1,
                            condition=ComparisonExpr(feature="order_imbalance", op="<", threshold=-0.1),
                            action=ExitActionV2(type="reduce_position", reduce_fraction=0.5),
                        ),
                        ExitRuleV2(
                            name="hard_close",
                            priority=2,
                            condition=ComparisonExpr(feature="spread_bps", op=">", threshold=50.0),
                            action=ExitActionV2(type="close_all"),
                        ),
                    ],
                )
            ],
            state_policy=StatePolicyV2(
                vars={"flatten_count": 0.0},
                guards=[],
                events=[
                    StateEventV2(
                        name="count_flatten",
                        on="on_flatten",
                        updates=[StateUpdateV2(var="flatten_count", op="increment", value=1.0)],
                    )
                ],
            ),
        )
        strategy = StrategyCompilerV2.compile(spec)

        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=5.0, mid_price=100.0)) is not None

        # reduce_position only -> no flatten event
        assert strategy.generate_signal(_mock_state(order_imbalance=-0.2, spread_bps=5.0, mid_price=99.5)) is None
        rt = strategy._states["TEST"]
        assert rt.state_vars["flatten_count"] == 0.0
        assert rt.position_side == "long"
        assert rt.position_size > 0.0

        # explicit close_all -> flatten event fires
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.0, spread_bps=60.0, mid_price=99.0))
        assert sig is not None
        assert sig.tags.get("exit_type") == "hard_close"
        assert rt.state_vars["flatten_count"] == 1.0

    def test_regime_execution_override_plus_adaptation(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                placement_mode="adaptive",
                cancel_after_ticks=20,
                max_reprices=3,
            ),
            regimes=[
                RegimeV2(
                    name="active",
                    priority=1,
                    when=ConstExpr(1.0),
                    entry_policy_refs=["long_entry"],
                    exit_policy_ref="exits",
                    execution_override=ExecutionPolicyV2(
                        placement_mode="aggressive_cross",
                        cancel_after_ticks=2,
                        max_reprices=0,
                        adaptation_rules=[
                            ExecutionAdaptationRuleV2(
                                condition=ComparisonExpr(feature="spread_bps", op=">", threshold=10.0),
                                override=ExecutionAdaptationOverrideV2(
                                    placement_mode="passive_only",
                                    cancel_after_ticks=4,
                                    max_reprices=1,
                                ),
                            )
                        ],
                    ),
                )
            ],
        )
        strategy = StrategyCompilerV2.compile(spec)

        sig = strategy.generate_signal(_mock_state(order_imbalance=0.6, spread_bps=15.0))
        assert sig is not None
        assert sig.tags.get("placement_mode") == "passive_only"
        assert sig.tags.get("cancel_after_ticks") == 4
        assert sig.tags.get("max_reprices") == 1

class TestReviewerPhase3Stabilization:

    def test_position_attr_sanity_warning(self):
        spec = _base_spec(
            preconditions=[
                PreconditionV2(
                    name="bad_entry_path_position_attr",
                    condition=ComparisonExpr(
                        left=PositionAttrExpr("holding_ticks"),
                        op=">",
                        threshold=0.0,
                    ),
                )
            ]
        )
        result = StrategyReviewerV2().review(spec)
        assert any(i.category == "position_attr_sanity" for i in result.issues)

    def test_execution_override_conflict_warning(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                placement_mode="adaptive",
                adaptation_rules=[
                    ExecutionAdaptationRuleV2(
                        condition=ConstExpr(1.0),
                        override=ExecutionAdaptationOverrideV2(
                            placement_mode="passive_only",
                            cancel_after_ticks=5,
                            max_reprices=1,
                        ),
                    ),
                    ExecutionAdaptationRuleV2(
                        condition=ConstExpr(1.0),
                        override=ExecutionAdaptationOverrideV2(
                            placement_mode="aggressive_cross",
                            cancel_after_ticks=2,
                            max_reprices=0,
                        ),
                    ),
                ],
            )
        )
        result = StrategyReviewerV2().review(spec)
        assert any(i.category == "execution_override_conflict" for i in result.issues)

    def test_regime_exit_coverage_warning(self):
        spec = _base_spec(
            exit_policies=[
                ExitPolicyV2(
                    name="exits",
                    rules=[
                        ExitRuleV2(
                            name="only_reduce",
                            priority=1,
                            condition=ConstExpr(1.0),
                            action=ExitActionV2(type="reduce_position", reduce_fraction=0.5),
                        )
                    ],
                )
            ],
            regimes=[
                RegimeV2(
                    name="active",
                    priority=1,
                    when=ConstExpr(1.0),
                    entry_policy_refs=["long_entry"],
                    exit_policy_ref="exits",
                )
            ],
        )
        result = StrategyReviewerV2().review(spec)
        assert any(i.category == "regime_exit_coverage" for i in result.issues)

class TestReviewerSeverityEscalation:

    def test_always_true_guard_is_error(self):
        spec = _base_spec(
            state_policy=StatePolicyV2(
                vars={"x": 0.0},
                guards=[StateGuardV2(name="always", condition=ConstExpr(1.0), effect="block_entry")],
                events=[],
            )
        )
        result = StrategyReviewerV2().review(spec)
        matches = [i for i in result.issues if i.category == "state_deadlock"]
        assert matches
        assert any(i.severity == "error" for i in matches)
        assert result.passed is False

    def test_execution_override_conflict_is_error(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                placement_mode="adaptive",
                adaptation_rules=[
                    ExecutionAdaptationRuleV2(
                        condition=ConstExpr(1.0),
                        override=ExecutionAdaptationOverrideV2(placement_mode="passive_only"),
                    ),
                    ExecutionAdaptationRuleV2(
                        condition=ConstExpr(1.0),
                        override=ExecutionAdaptationOverrideV2(placement_mode="aggressive_cross"),
                    ),
                ],
            )
        )
        result = StrategyReviewerV2().review(spec)
        matches = [i for i in result.issues if i.category == "execution_override_conflict"]
        assert matches
        assert any(i.severity == "error" for i in matches)
        assert result.passed is False
