"""Tests for StrategyCompilerV2 and CompiledStrategyV2."""
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
    AllExpr, AnyExpr, ComparisonExpr, ConstExpr, CrossExpr, NotExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryConstraints, EntryPolicyV2, ExitActionV2, ExitPolicyV2,
    ExitRuleV2, RiskPolicyV2, PositionSizingV2, StrategySpecV2,
)
from strategy_block.strategy_compiler.v2.compiler_v2 import (
    CompiledStrategyV2, StrategyCompilerV2,
)
from strategy_block.strategy_compiler.v2.runtime_v2 import (
    evaluate_bool, evaluate_float,
)
from strategy_block.strategy_compiler import compile_strategy


# ── Helpers ───────────────────────────────────────────────────────────

def _make_spec(**kw) -> StrategySpecV2:
    defaults = dict(
        name="test",
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
        risk_policy=RiskPolicyV2(
            max_position=500, inventory_cap=1000,
            position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=500),
        ),
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


def _mock_state(symbol="TEST", order_imbalance=0.0, spread_bps=5.0,
                depth_imbalance=0.0, trade_flow_imbalance=0.0, mid_price=100.0):
    state = MagicMock()
    state.symbol = symbol
    state.timestamp = "2026-01-01T09:00:00"
    state.lob = MockLOB(
        mid_price=mid_price,
        order_imbalance=order_imbalance,
    )
    state.spread_bps = spread_bps
    state.features = {
        "order_imbalance": order_imbalance,
        "spread_bps": spread_bps,
        "depth_imbalance": depth_imbalance,
        "trade_flow_imbalance": trade_flow_imbalance,
    }
    state.trades = None
    return state


# ── Runtime evaluator tests ──────────────────────────────────────────

class TestEvaluateBool:

    def test_comparison_gt(self):
        node = ComparisonExpr(feature="x", op=">", threshold=0.5)
        assert evaluate_bool(node, {"x": 0.6}, {}) is True
        assert evaluate_bool(node, {"x": 0.4}, {}) is False

    def test_comparison_lt(self):
        node = ComparisonExpr(feature="x", op="<", threshold=0.5)
        assert evaluate_bool(node, {"x": 0.3}, {}) is True

    def test_comparison_eq(self):
        node = ComparisonExpr(feature="x", op="==", threshold=1.0)
        assert evaluate_bool(node, {"x": 1.0}, {}) is True
        assert evaluate_bool(node, {"x": 1.1}, {}) is False

    def test_all_expr(self):
        node = AllExpr(children=[
            ComparisonExpr(feature="a", op=">", threshold=0.3),
            ComparisonExpr(feature="b", op=">", threshold=0.1),
        ])
        assert evaluate_bool(node, {"a": 0.5, "b": 0.2}, {}) is True
        assert evaluate_bool(node, {"a": 0.5, "b": 0.05}, {}) is False

    def test_any_expr(self):
        node = AnyExpr(children=[
            ComparisonExpr(feature="a", op=">", threshold=0.5),
            ComparisonExpr(feature="b", op=">", threshold=0.5),
        ])
        assert evaluate_bool(node, {"a": 0.1, "b": 0.6}, {}) is True
        assert evaluate_bool(node, {"a": 0.1, "b": 0.1}, {}) is False

    def test_not_expr(self):
        node = NotExpr(child=ComparisonExpr(feature="x", op=">", threshold=0.5))
        assert evaluate_bool(node, {"x": 0.3}, {}) is True
        assert evaluate_bool(node, {"x": 0.6}, {}) is False

    def test_cross_above(self):
        node = CrossExpr(feature="x", threshold=0.0, direction="above")
        # prev was below, current is above
        assert evaluate_bool(node, {"x": 0.1}, {"x": -0.1}) is True
        # already above
        assert evaluate_bool(node, {"x": 0.1}, {"x": 0.05}) is False

    def test_cross_below(self):
        node = CrossExpr(feature="x", threshold=0.0, direction="below")
        assert evaluate_bool(node, {"x": -0.1}, {"x": 0.1}) is True
        assert evaluate_bool(node, {"x": -0.1}, {"x": -0.2}) is False

    def test_const_true_false(self):
        assert evaluate_bool(ConstExpr(value=1.0), {}, {}) is True
        assert evaluate_bool(ConstExpr(value=0.0), {}, {}) is False


class TestEvaluateFloat:

    def test_const(self):
        assert evaluate_float(ConstExpr(value=0.7), {}) == 0.7

    def test_feature(self):
        from strategy_block.strategy_specs.v2.ast_nodes import FeatureExpr
        assert evaluate_float(FeatureExpr(name="x"), {"x": 0.42}) == 0.42


# ── Compiler tests ───────────────────────────────────────────────────

class TestCompilerV2:

    def test_compile_valid_spec(self):
        spec = _make_spec()
        strategy = StrategyCompilerV2.compile(spec)
        assert strategy.name == "CompiledV2:test"

    def test_compile_invalid_spec_raises(self):
        spec = _make_spec(name="")
        with pytest.raises(ValueError, match="Invalid v2 strategy spec"):
            StrategyCompilerV2.compile(spec)

    def test_strategy_implements_abc(self):
        from strategy_block.strategy.base import Strategy
        spec = _make_spec()
        strategy = StrategyCompilerV2.compile(spec)
        assert isinstance(strategy, Strategy)

    def test_long_entry_generates_positive_signal(self):
        spec = _make_spec()
        strategy = StrategyCompilerV2.compile(spec)
        state = _mock_state(order_imbalance=0.5)
        signal = strategy.generate_signal(state)
        assert signal is not None
        assert signal.score > 0

    def test_no_entry_when_condition_not_met(self):
        spec = _make_spec()
        strategy = StrategyCompilerV2.compile(spec)
        state = _mock_state(order_imbalance=0.1)
        signal = strategy.generate_signal(state)
        assert signal is None

    def test_short_entry(self):
        spec = _make_spec(entry_policies=[
            EntryPolicyV2(
                name="short_entry", side="short",
                trigger=ComparisonExpr(feature="order_imbalance", op="<", threshold=-0.3),
                strength=ConstExpr(value=0.6),
            ),
        ])
        strategy = StrategyCompilerV2.compile(spec)
        state = _mock_state(order_imbalance=-0.5)
        signal = strategy.generate_signal(state)
        assert signal is not None
        assert signal.score < 0

    def test_exit_closes_position(self):
        spec = _make_spec()
        strategy = StrategyCompilerV2.compile(spec)

        # Enter long
        s1 = _mock_state(order_imbalance=0.5)
        sig1 = strategy.generate_signal(s1)
        assert sig1 is not None and sig1.score > 0

        # Trigger exit
        s2 = _mock_state(order_imbalance=-0.3)
        sig2 = strategy.generate_signal(s2)
        assert sig2 is not None
        assert sig2.score < 0  # closing a long = negative score
        assert sig2.tags.get("exit_type") == "stop"

    def test_cooldown_blocks_reentry(self):
        spec = _make_spec(entry_policies=[
            EntryPolicyV2(
                name="long_entry", side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.5),
                constraints=EntryConstraints(cooldown_ticks=100),
            ),
        ])
        strategy = StrategyCompilerV2.compile(spec)

        # Enter
        s1 = _mock_state(order_imbalance=0.5)
        sig1 = strategy.generate_signal(s1)
        assert sig1 is not None

        # Exit
        s2 = _mock_state(order_imbalance=-0.3)
        strategy.generate_signal(s2)

        # Try to re-enter during cooldown
        s3 = _mock_state(order_imbalance=0.5)
        sig3 = strategy.generate_signal(s3)
        assert sig3 is None  # blocked by cooldown

    def test_precondition_blocks_entry(self):
        from strategy_block.strategy_specs.v2.schema_v2 import PreconditionV2
        spec = _make_spec(preconditions=[
            PreconditionV2(
                name="spread_ok",
                condition=ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
            ),
        ])
        strategy = StrategyCompilerV2.compile(spec)

        # Spread too wide — precondition fails
        state = _mock_state(order_imbalance=0.5, spread_bps=20.0)
        signal = strategy.generate_signal(state)
        assert signal is None

    def test_reset_clears_state(self):
        spec = _make_spec()
        strategy = StrategyCompilerV2.compile(spec)
        strategy.generate_signal(_mock_state(order_imbalance=0.5))
        strategy.reset()
        assert len(strategy._states) == 0


# ── Compiler dispatch tests ──────────────────────────────────────────

class TestCompileDispatch:

    def test_dispatch_v1(self):
        from strategy_block.strategy_specs.schema import StrategySpec, SignalRule, ExitRule
        spec = StrategySpec(
            name="test_v1",
            signal_rules=[
                SignalRule(feature="order_imbalance", operator=">",
                           threshold=0.3, score_contribution=0.5),
            ],
            exit_rules=[
                ExitRule(exit_type="stop_loss", threshold_bps=15.0),
            ],
        )
        strategy = compile_strategy(spec)
        from strategy_block.strategy_compiler.compiler import CompiledStrategy
        assert isinstance(strategy, CompiledStrategy)

    def test_dispatch_v2(self):
        spec = _make_spec()
        strategy = compile_strategy(spec)
        assert isinstance(strategy, CompiledStrategyV2)

    def test_dispatch_unknown_raises(self):
        with pytest.raises(TypeError, match="Cannot compile"):
            compile_strategy({"not": "a spec"})
