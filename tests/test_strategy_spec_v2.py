"""Tests for StrategySpec v2 schema and AST nodes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.ast_nodes import (
    AllExpr,
    AnyExpr,
    ComparisonExpr,
    ConstExpr,
    CrossExpr,
    FeatureExpr,
    NotExpr,
    expr_from_dict,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryConstraints,
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    PositionSizingV2,
    PreconditionV2,
    RiskPolicyV2,
    StrategySpecV2,
)


# ── Helper to build a minimal valid spec ──────────────────────────────

def _minimal_spec(**overrides) -> StrategySpecV2:
    defaults = dict(
        name="test_v2",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.5),
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
    )
    defaults.update(overrides)
    return StrategySpecV2(**defaults)


# ── AST node tests ────────────────────────────────────────────────────

class TestASTNodes:

    def test_const_roundtrip(self):
        node = ConstExpr(value=42.0)
        d = node.to_dict()
        assert d == {"type": "const", "value": 42.0}
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, ConstExpr)
        assert rebuilt.value == 42.0

    def test_feature_roundtrip(self):
        node = FeatureExpr(name="spread_bps")
        d = node.to_dict()
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, FeatureExpr)
        assert rebuilt.name == "spread_bps"

    def test_comparison_roundtrip(self):
        node = ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3)
        d = node.to_dict()
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, ComparisonExpr)
        assert rebuilt.feature == "order_imbalance"
        assert rebuilt.op == ">"
        assert rebuilt.threshold == 0.3

    def test_all_roundtrip(self):
        node = AllExpr(children=[
            ComparisonExpr(feature="a", op=">", threshold=1.0),
            ComparisonExpr(feature="b", op="<", threshold=2.0),
        ])
        d = node.to_dict()
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, AllExpr)
        assert len(rebuilt.children) == 2

    def test_any_roundtrip(self):
        node = AnyExpr(children=[
            ComparisonExpr(feature="a", op=">", threshold=1.0),
        ])
        rebuilt = expr_from_dict(node.to_dict())
        assert isinstance(rebuilt, AnyExpr)

    def test_not_roundtrip(self):
        node = NotExpr(child=ComparisonExpr(feature="x", op=">", threshold=0.5))
        rebuilt = expr_from_dict(node.to_dict())
        assert isinstance(rebuilt, NotExpr)
        assert isinstance(rebuilt.child, ComparisonExpr)

    def test_cross_roundtrip(self):
        node = CrossExpr(feature="trade_flow_imbalance", threshold=0.0, direction="above")
        d = node.to_dict()
        rebuilt = expr_from_dict(d)
        assert isinstance(rebuilt, CrossExpr)
        assert rebuilt.direction == "above"

    def test_collect_features(self):
        tree = AllExpr(children=[
            ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
            NotExpr(child=ComparisonExpr(feature="spread_bps", op=">", threshold=30.0)),
            CrossExpr(feature="trade_flow_imbalance", threshold=0.0, direction="above"),
        ])
        features = tree.collect_features()
        assert features == {"order_imbalance", "spread_bps", "trade_flow_imbalance"}

    def test_unknown_node_type_raises(self):
        with pytest.raises(ValueError, match="Unknown expression node type"):
            expr_from_dict({"type": "magic_node"})


# ── StrategySpecV2 tests ─────────────────────────────────────────────

class TestStrategySpecV2:

    def test_minimal_spec_validates(self):
        spec = _minimal_spec()
        errors = spec.validate()
        assert errors == [], f"Validation errors: {errors}"

    def test_roundtrip_json(self, tmp_path):
        spec = _minimal_spec()
        json_str = spec.to_json()
        rebuilt = StrategySpecV2.from_json(json_str)
        assert rebuilt.name == spec.name
        assert rebuilt.spec_format == "v2"
        assert len(rebuilt.entry_policies) == 1
        assert len(rebuilt.exit_policies) == 1

    def test_save_load(self, tmp_path):
        spec = _minimal_spec()
        path = tmp_path / "test_v2.json"
        spec.save(path)
        loaded = StrategySpecV2.load(path)
        assert loaded.name == "test_v2"
        assert loaded.entry_policies[0].side == "long"

    def test_to_dict_from_dict(self):
        spec = _minimal_spec()
        d = spec.to_dict()
        rebuilt = StrategySpecV2.from_dict(d)
        assert rebuilt.name == spec.name
        assert rebuilt.risk_policy.max_position == spec.risk_policy.max_position

    def test_missing_name_fails_validation(self):
        spec = _minimal_spec(name="")
        errors = spec.validate()
        assert any("name is required" in e for e in errors)

    def test_missing_entry_fails_validation(self):
        spec = _minimal_spec(entry_policies=[])
        errors = spec.validate()
        assert any("entry policy" in e for e in errors)

    def test_missing_exit_fails_validation(self):
        spec = _minimal_spec(exit_policies=[])
        errors = spec.validate()
        assert any("exit policy" in e for e in errors)

    def test_invalid_side_fails_validation(self):
        spec = _minimal_spec(entry_policies=[
            EntryPolicyV2(
                name="bad", side="up",
                trigger=ConstExpr(1.0), strength=ConstExpr(0.5),
            ),
        ])
        errors = spec.validate()
        assert any("side must be" in e for e in errors)

    def test_invalid_comparison_op_fails_validation(self):
        spec = _minimal_spec(entry_policies=[
            EntryPolicyV2(
                name="bad", side="long",
                trigger=ComparisonExpr(feature="x", op="!=", threshold=1.0),
                strength=ConstExpr(0.5),
            ),
        ])
        errors = spec.validate()
        assert any("invalid comparison op" in e for e in errors)

    def test_invalid_exit_action_fails_validation(self):
        spec = _minimal_spec(exit_policies=[
            ExitPolicyV2(name="x", rules=[
                ExitRuleV2(
                    name="bad", priority=1,
                    condition=ConstExpr(1.0),
                    action=ExitActionV2(type="explode"),
                ),
            ]),
        ])
        errors = spec.validate()
        assert any("action" in e and "type" in e for e in errors)

    def test_negative_max_position_fails_validation(self):
        spec = _minimal_spec(risk_policy=RiskPolicyV2(max_position=-1))
        errors = spec.validate()
        assert any("max_position" in e for e in errors)

    def test_collect_all_features(self):
        spec = _minimal_spec(
            preconditions=[
                PreconditionV2(
                    name="spread_filter",
                    condition=ComparisonExpr(feature="spread_bps", op="<", threshold=30.0),
                ),
            ],
        )
        features = spec.collect_all_features()
        assert "order_imbalance" in features
        assert "spread_bps" in features
