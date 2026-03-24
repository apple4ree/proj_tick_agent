"""Tests for v2 position_attr AST/runtime integration."""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass, field
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.ast_nodes import (
    ComparisonExpr,
    ConstExpr,
    PositionAttrExpr,
    expr_from_dict,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    PositionSizingV2,
    RiskPolicyV2,
    StrategySpecV2,
)
from strategy_block.strategy_compiler.v2.compiler_v2 import StrategyCompilerV2
from strategy_block.strategy_compiler.v2.runtime_v2 import RuntimeStateV2, evaluate_float


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


def _mock_state(order_imbalance=0.0, spread_bps=5.0, mid_price=100.0):
    state = MagicMock()
    state.symbol = "TEST"
    state.timestamp = "2026-01-01T09:00:00"
    state.lob = MockLOB(mid_price=mid_price, order_imbalance=order_imbalance)
    state.spread_bps = spread_bps
    state.features = {
        "order_imbalance": order_imbalance,
        "spread_bps": spread_bps,
    }
    state.trades = None
    return state


def _base_spec(exit_rule: ExitRuleV2, *, side: str = "long") -> StrategySpecV2:
    trigger = ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3)
    if side == "short":
        trigger = ComparisonExpr(feature="order_imbalance", op="<", threshold=-0.3)

    return StrategySpecV2(
        name="position_attr_test",
        entry_policies=[
            EntryPolicyV2(
                name=f"{side}_entry",
                side=side,
                trigger=trigger,
                strength=ConstExpr(1.0),
            )
        ],
        exit_policies=[ExitPolicyV2(name="exits", rules=[exit_rule])],
        risk_policy=RiskPolicyV2(
            max_position=500,
            inventory_cap=1000,
            position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=500),
        ),
    )


class TestPositionAttrRuntimeValues:

    def test_runtime_evaluate_float_position_attrs(self):
        rt = RuntimeStateV2(
            tick_count=10,
            position_side="long",
            position_size=120.0,
            entry_tick=7,
            entry_price=100.0,
        )
        features = {"mid_price": 101.0}

        assert evaluate_float(PositionAttrExpr("holding_ticks"), features, rt) == 3.0
        assert evaluate_float(PositionAttrExpr("entry_price"), features, rt) == 100.0
        assert evaluate_float(PositionAttrExpr("position_size"), features, rt) == 120.0
        assert evaluate_float(PositionAttrExpr("position_side"), features, rt) == 1.0
        assert evaluate_float(PositionAttrExpr("unrealized_pnl_bps"), features, rt) == 100.0

        rt.position_side = "short"
        assert evaluate_float(PositionAttrExpr("position_side"), features, rt) == -1.0
        assert evaluate_float(PositionAttrExpr("unrealized_pnl_bps"), features, rt) == -100.0


class TestPositionAttrStrategyUsage:

    def test_holding_ticks_time_exit(self):
        spec = _base_spec(
            ExitRuleV2(
                name="time_exit",
                priority=1,
                condition=ComparisonExpr(
                    left=PositionAttrExpr("holding_ticks"),
                    op=">=",
                    threshold=2.0,
                ),
                action=ExitActionV2(type="close_all"),
            )
        )
        strategy = StrategyCompilerV2.compile(spec)

        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, mid_price=100.0)) is not None
        assert strategy.generate_signal(_mock_state(order_imbalance=0.0, mid_price=100.0)) is None
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.0, mid_price=100.0))
        assert sig is not None
        assert sig.tags.get("exit_type") == "time_exit"

    def test_unrealized_pnl_bps_stop(self):
        spec = _base_spec(
            ExitRuleV2(
                name="pnl_stop",
                priority=1,
                condition=ComparisonExpr(
                    left=PositionAttrExpr("unrealized_pnl_bps"),
                    op="<=",
                    threshold=-50.0,
                ),
                action=ExitActionV2(type="close_all"),
            )
        )
        strategy = StrategyCompilerV2.compile(spec)

        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, mid_price=100.0)) is not None
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.0, mid_price=99.0))
        assert sig is not None
        assert sig.tags.get("exit_type") == "pnl_stop"

    def test_position_size_condition(self):
        spec = _base_spec(
            ExitRuleV2(
                name="size_check",
                priority=1,
                condition=ComparisonExpr(
                    left=PositionAttrExpr("position_size"),
                    op=">",
                    threshold=0.0,
                ),
                action=ExitActionV2(type="close_all"),
            )
        )
        strategy = StrategyCompilerV2.compile(spec)

        assert strategy.generate_signal(_mock_state(order_imbalance=0.6, mid_price=100.0)) is not None
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.0, mid_price=100.0))
        assert sig is not None
        assert sig.tags.get("exit_type") == "size_check"

    def test_position_side_and_entry_price_condition(self):
        side_rule = ExitRuleV2(
            name="short_side_exit",
            priority=1,
            condition=ComparisonExpr(
                left=PositionAttrExpr("position_side"),
                op="<",
                threshold=0.0,
            ),
            action=ExitActionV2(type="close_all"),
        )
        spec = _base_spec(side_rule, side="short")
        strategy = StrategyCompilerV2.compile(spec)

        assert strategy.generate_signal(_mock_state(order_imbalance=-0.6, mid_price=101.0)) is not None
        sig = strategy.generate_signal(_mock_state(order_imbalance=0.0, mid_price=101.0))
        assert sig is not None
        assert sig.tags.get("exit_type") == "short_side_exit"


class TestPositionAttrSerialization:

    def test_roundtrip(self):
        d = {"type": "position_attr", "name": "holding_ticks"}
        node = expr_from_dict(d)
        assert isinstance(node, PositionAttrExpr)
        assert node.to_dict() == d
