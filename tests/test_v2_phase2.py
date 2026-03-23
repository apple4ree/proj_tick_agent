"""Tests for v2 Phase 2: lag, rolling, persist, regimes, execution_policy."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from dataclasses import dataclass, field

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.ast_nodes import (
    AllExpr, ComparisonExpr, ConstExpr, LagExpr, PersistExpr,
    RollingExpr, expr_from_dict,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2, ExitActionV2, ExitPolicyV2, ExitRuleV2,
    ExecutionPolicyV2, PreconditionV2, RegimeV2, RiskPolicyV2,
    StrategySpecV2, EntryConstraints,
)
from strategy_block.strategy_compiler.v2.runtime_v2 import (
    RuntimeStateV2, evaluate_bool, evaluate_float,
)
from strategy_block.strategy_compiler.v2.compiler_v2 import (
    CompiledStrategyV2, StrategyCompilerV2,
)
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_generation.v2.templates_v2 import (
    V2_TEMPLATES, get_v2_template,
)
from strategy_block.strategy_generation.v2.lowering import lower_to_spec_v2
from strategy_block.strategy_compiler import compile_strategy


# ── Helpers ───────────────────────────────────────────────────────────

def _base_spec(**kw) -> StrategySpecV2:
    defaults = dict(
        name="test_p2",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry", side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.5),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="stop", priority=1,
                    condition=ComparisonExpr(feature="order_imbalance", op="<", threshold=-0.2),
                    action=ExitActionV2(type="close_all"),
                ),
            ]),
        ],
        risk_policy=RiskPolicyV2(max_position=500, inventory_cap=1000),
    )
    defaults.update(kw)
    return StrategySpecV2(**defaults)


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
    spread_bps: float = 200.0


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


# ── AST Phase 2 tests ────────────────────────────────────────────────

class TestLagExpr:

    def test_roundtrip(self):
        node = LagExpr(feature="x", steps=3)
        d = node.to_dict()
        assert d == {"type": "lag", "feature": "x", "steps": 3}
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, LagExpr)
        assert rebuilt.feature == "x"
        assert rebuilt.steps == 3

    def test_collect_features(self):
        node = LagExpr(feature="spread_bps", steps=5)
        assert node.collect_features() == {"spread_bps"}

    def test_evaluate_float_with_history(self):
        rt = RuntimeStateV2()
        for i in range(5):
            rt.record_features({"x": float(i * 10)})
        # History: [0, 10, 20, 30, 40]
        # lag(x, 2) => value 2 ticks ago = 20
        node = LagExpr(feature="x", steps=2)
        val = evaluate_float(node, {"x": 40.0}, rt)
        assert val == 20.0

    def test_evaluate_lag_no_history(self):
        rt = RuntimeStateV2()
        node = LagExpr(feature="x", steps=5)
        assert evaluate_float(node, {"x": 1.0}, rt) == 0.0

    def test_validation_steps_lt_1(self):
        spec = _base_spec(entry_policies=[
            EntryPolicyV2(
                name="bad", side="long",
                trigger=LagExpr(feature="x", steps=0),
                strength=ConstExpr(0.5),
            ),
        ])
        errors = spec.validate()
        assert any("lag steps must be >= 1" in e for e in errors)


class TestRollingExpr:

    def test_roundtrip(self):
        node = RollingExpr(feature="x", method="mean", window=10)
        d = node.to_dict()
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, RollingExpr)
        assert rebuilt.method == "mean"
        assert rebuilt.window == 10

    def test_rolling_mean(self):
        rt = RuntimeStateV2()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            rt.record_features({"x": v})
        # rolling mean of last 3: (30+40+50)/3 = 40
        node = RollingExpr(feature="x", method="mean", window=3)
        val = evaluate_float(node, {"x": 50.0}, rt)
        assert abs(val - 40.0) < 1e-9

    def test_rolling_max(self):
        rt = RuntimeStateV2()
        for v in [10.0, 50.0, 30.0]:
            rt.record_features({"x": v})
        node = RollingExpr(feature="x", method="max", window=5)
        val = evaluate_float(node, {"x": 30.0}, rt)
        assert val == 50.0

    def test_rolling_min(self):
        rt = RuntimeStateV2()
        for v in [10.0, 50.0, 30.0]:
            rt.record_features({"x": v})
        node = RollingExpr(feature="x", method="min", window=5)
        val = evaluate_float(node, {"x": 30.0}, rt)
        assert val == 10.0

    def test_validation_window_lt_2(self):
        spec = _base_spec(entry_policies=[
            EntryPolicyV2(
                name="bad", side="long",
                trigger=RollingExpr(feature="x", method="mean", window=1),
                strength=ConstExpr(0.5),
            ),
        ])
        errors = spec.validate()
        assert any("rolling window must be >= 2" in e for e in errors)

    def test_validation_invalid_method(self):
        spec = _base_spec(entry_policies=[
            EntryPolicyV2(
                name="bad", side="long",
                trigger=RollingExpr(feature="x", method="std", window=5),
                strength=ConstExpr(0.5),
            ),
        ])
        errors = spec.validate()
        assert any("rolling method" in e for e in errors)


class TestPersistExpr:

    def test_roundtrip(self):
        inner = ComparisonExpr(feature="x", op=">", threshold=0.5)
        node = PersistExpr(expr=inner, window=5, min_true=3)
        d = node.to_dict()
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, PersistExpr)
        assert rebuilt.window == 5
        assert rebuilt.min_true == 3
        assert isinstance(rebuilt.expr, ComparisonExpr)

    def test_persist_evaluation(self):
        rt = RuntimeStateV2()
        inner = ComparisonExpr(feature="x", op=">", threshold=0.5)
        node = PersistExpr(expr=inner, window=5, min_true=3)

        # Feed 5 ticks: x=0.6 (True), 0.3 (False), 0.7 (True), 0.8 (True), 0.2 (False)
        vals = [0.6, 0.3, 0.7, 0.8, 0.2]
        results = []
        for v in vals:
            features = {"x": v}
            rt.record_features(features)
            results.append(evaluate_bool(node, features, {}, rt))

        # After tick 4 (0.8): True count = 3 out of 4 seen => True
        assert results[3] is True
        # After tick 5 (0.2): history = [True, False, True, True, False] => 3 true => True
        assert results[4] is True

    def test_validation_min_true_gt_window(self):
        spec = _base_spec(entry_policies=[
            EntryPolicyV2(
                name="bad", side="long",
                trigger=PersistExpr(
                    expr=ComparisonExpr(feature="x", op=">", threshold=0.5),
                    window=3, min_true=5,
                ),
                strength=ConstExpr(0.5),
            ),
        ])
        errors = spec.validate()
        assert any("min_true" in e and "window" in e for e in errors)

    def test_collect_features(self):
        node = PersistExpr(
            expr=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
            window=5, min_true=3,
        )
        assert node.collect_features() == {"order_imbalance"}


# ── Regime tests ──────────────────────────────────────────────────────

class TestRegimes:

    def test_higher_priority_regime_selected(self):
        spec = _base_spec(
            entry_policies=[
                EntryPolicyV2(
                    name="aggressive", side="long",
                    trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                    strength=ConstExpr(0.8),
                ),
                EntryPolicyV2(
                    name="conservative", side="long",
                    trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.4),
                    strength=ConstExpr(0.3),
                ),
            ],
            regimes=[
                RegimeV2(
                    name="volatile", priority=1,
                    when=ComparisonExpr(feature="spread_bps", op=">", threshold=10.0),
                    entry_policy_refs=["conservative"],
                    exit_policy_ref="exits",
                ),
                RegimeV2(
                    name="calm", priority=2,
                    when=ComparisonExpr(feature="spread_bps", op="<=", threshold=10.0),
                    entry_policy_refs=["aggressive"],
                    exit_policy_ref="exits",
                ),
            ],
        )
        strategy = StrategyCompilerV2.compile(spec)

        # Spread > 10 => volatile regime => only "conservative" entry
        state = _mock_state(order_imbalance=0.5, spread_bps=15.0)
        signal = strategy.generate_signal(state)
        assert signal is not None
        assert signal.tags.get("entry_policy") == "conservative"

    def test_no_regime_fallback(self):
        """When no regimes defined, all policies are used (Phase 1 behavior)."""
        spec = _base_spec()
        strategy = StrategyCompilerV2.compile(spec)
        state = _mock_state(order_imbalance=0.5)
        signal = strategy.generate_signal(state)
        assert signal is not None

    def test_no_matching_regime_blocks_trade(self):
        """When regimes exist but none match, no trade occurs."""
        spec = _base_spec(
            regimes=[
                RegimeV2(
                    name="impossible", priority=1,
                    when=ComparisonExpr(feature="spread_bps", op=">", threshold=999.0),
                    entry_policy_refs=["long_entry"],
                    exit_policy_ref="exits",
                ),
            ],
        )
        strategy = StrategyCompilerV2.compile(spec)
        state = _mock_state(order_imbalance=0.5, spread_bps=5.0)
        signal = strategy.generate_signal(state)
        assert signal is None

    def test_invalid_regime_refs_detected(self):
        spec = _base_spec(
            regimes=[
                RegimeV2(
                    name="bad_refs", priority=1,
                    when=ConstExpr(1.0),
                    entry_policy_refs=["nonexistent_entry"],
                    exit_policy_ref="nonexistent_exit",
                ),
            ],
        )
        errors = spec.validate()
        assert any("nonexistent_entry" in e for e in errors)
        assert any("nonexistent_exit" in e for e in errors)

    def test_reviewer_catches_invalid_refs(self):
        spec = _base_spec(
            regimes=[
                RegimeV2(
                    name="bad_refs", priority=1,
                    when=ConstExpr(1.0),
                    entry_policy_refs=["ghost_policy"],
                    exit_policy_ref="exits",
                ),
            ],
        )
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert not result.passed
        assert any(i.category == "regime_reference_integrity" for i in result.issues)

    def test_regime_serialization_roundtrip(self, tmp_path):
        spec = _base_spec(
            regimes=[
                RegimeV2(
                    name="r1", priority=1,
                    when=ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                    entry_policy_refs=["long_entry"],
                    exit_policy_ref="exits",
                ),
            ],
        )
        path = tmp_path / "regime_spec.json"
        spec.save(path)
        loaded = StrategySpecV2.load(path)
        assert len(loaded.regimes) == 1
        assert loaded.regimes[0].name == "r1"
        assert loaded.regimes[0].entry_policy_refs == ["long_entry"]


# ── Execution policy tests ────────────────────────────────────────────

class TestExecutionPolicy:

    def test_tags_include_placement_mode(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                placement_mode="adaptive",
                cancel_after_ticks=20,
                max_reprices=3,
            ),
        )
        strategy = StrategyCompilerV2.compile(spec)
        state = _mock_state(order_imbalance=0.5)
        signal = strategy.generate_signal(state)
        assert signal is not None
        assert signal.tags.get("placement_mode") == "adaptive"
        assert signal.tags.get("cancel_after_ticks") == 20
        assert signal.tags.get("max_reprices") == 3

    def test_invalid_placement_mode(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(placement_mode="rocket"),
        )
        errors = spec.validate()
        assert any("placement_mode" in e for e in errors)

    def test_negative_cancel_after_ticks(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(cancel_after_ticks=-1),
        )
        errors = spec.validate()
        assert any("cancel_after_ticks" in e for e in errors)

    def test_do_not_trade_when_blocks(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                do_not_trade_when=ComparisonExpr(
                    feature="spread_bps", op=">", threshold=10.0
                ),
            ),
        )
        strategy = StrategyCompilerV2.compile(spec)

        # Spread > 10 => do_not_trade => no signal
        state = _mock_state(order_imbalance=0.5, spread_bps=15.0)
        signal = strategy.generate_signal(state)
        assert signal is None

        # Spread < 10 => ok to trade
        state2 = _mock_state(order_imbalance=0.5, spread_bps=5.0)
        signal2 = strategy.generate_signal(state2)
        assert signal2 is not None

    def test_serialization_roundtrip(self, tmp_path):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(
                placement_mode="passive_only",
                cancel_after_ticks=10,
                max_reprices=2,
                do_not_trade_when=ComparisonExpr(
                    feature="spread_bps", op=">", threshold=50.0
                ),
            ),
        )
        path = tmp_path / "exec_spec.json"
        spec.save(path)
        loaded = StrategySpecV2.load(path)
        assert loaded.execution_policy is not None
        assert loaded.execution_policy.placement_mode == "passive_only"
        assert loaded.execution_policy.cancel_after_ticks == 10


# ── Reviewer Phase 2 tests ────────────────────────────────────────────

class TestReviewerPhase2:

    def test_dead_regime_detected(self):
        spec = _base_spec(
            regimes=[
                RegimeV2(
                    name="dead", priority=1,
                    when=AllExpr(children=[
                        ComparisonExpr(feature="spread_bps", op=">", threshold=50.0),
                        ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                    ]),
                    entry_policy_refs=["long_entry"],
                    exit_policy_ref="exits",
                ),
            ],
        )
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert any(i.category == "dead_regime" for i in result.issues)

    def test_execution_risk_mismatch_passive_large(self):
        spec = _base_spec(
            risk_policy=RiskPolicyV2(max_position=1000, inventory_cap=2000),
            execution_policy=ExecutionPolicyV2(placement_mode="passive_only"),
        )
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert any(i.category == "execution_risk_mismatch" for i in result.issues)

    def test_latency_warning_large_rolling(self):
        spec = _base_spec(entry_policies=[
            EntryPolicyV2(
                name="big_window", side="long",
                trigger=RollingExpr(feature="x", method="mean", window=300),
                strength=ConstExpr(0.5),
            ),
        ])
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert any(i.category == "latency_structure_warning" for i in result.issues)

    def test_negative_max_reprices(self):
        spec = _base_spec(
            execution_policy=ExecutionPolicyV2(max_reprices=-1),
        )
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert any(i.category == "execution_risk_mismatch" for i in result.issues)


# ── Phase 2 template e2e tests ────────────────────────────────────────

class TestPhase2Templates:

    @pytest.mark.parametrize("name", [
        "regime_filtered_persist_momentum",
        "rolling_mean_reversion",
        "adaptive_execution_imbalance",
    ])
    def test_phase2_template_generates_valid_spec(self, name):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        errors = spec.validate()
        assert errors == [], f"Validation errors for {name}: {errors}"

    @pytest.mark.parametrize("name", [
        "regime_filtered_persist_momentum",
        "rolling_mean_reversion",
        "adaptive_execution_imbalance",
    ])
    def test_phase2_template_passes_review(self, name):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        reviewer = StrategyReviewerV2()
        result = reviewer.review(spec)
        assert result.passed, (
            f"Review failed for {name}: "
            f"{[i.description for i in result.issues if i.severity == 'error']}"
        )

    @pytest.mark.parametrize("name", [
        "regime_filtered_persist_momentum",
        "rolling_mean_reversion",
        "adaptive_execution_imbalance",
    ])
    def test_phase2_template_compiles(self, name):
        template = get_v2_template(name)
        spec = lower_to_spec_v2(template)
        strategy = compile_strategy(spec)
        assert strategy is not None
        assert strategy.name.startswith("CompiledV2:")

    def test_regime_template_has_regimes(self):
        template = get_v2_template("regime_filtered_persist_momentum")
        spec = lower_to_spec_v2(template)
        assert len(spec.regimes) == 2

    def test_adaptive_template_has_execution_policy(self):
        template = get_v2_template("adaptive_execution_imbalance")
        spec = lower_to_spec_v2(template)
        assert spec.execution_policy is not None
        assert spec.execution_policy.placement_mode == "adaptive"

    def test_persist_template_signal_generation(self):
        """Persist template generates signal after sufficient ticks."""
        template = get_v2_template("regime_filtered_persist_momentum")
        spec = lower_to_spec_v2(template)
        strategy = compile_strategy(spec)

        # Feed several ticks with strong imbalance + narrow spread (trending)
        signals = []
        for _ in range(6):
            state = _mock_state(order_imbalance=0.4, spread_bps=5.0)
            sig = strategy.generate_signal(state)
            if sig is not None:
                signals.append(sig)

        # After enough ticks, persist should have activated at least once
        # (3/5 true needed, we sent 6 ticks with imbalance=0.4 > threshold 0.25)
        assert len(signals) > 0, "Expected at least one signal from persist trigger"

    def test_adaptive_execution_do_not_trade(self):
        """Adaptive execution blocks trade when spread > 50."""
        template = get_v2_template("adaptive_execution_imbalance")
        spec = lower_to_spec_v2(template)
        strategy = compile_strategy(spec)

        state = _mock_state(order_imbalance=0.5, spread_bps=60.0, depth_imbalance=0.2)
        signal = strategy.generate_signal(state)
        assert signal is None  # blocked by do_not_trade_when
