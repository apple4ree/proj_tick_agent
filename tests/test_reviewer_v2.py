"""Tests for StrategyReviewerV2."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.ast_nodes import (
    AllExpr, ComparisonExpr, ConstExpr, CrossExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2, ExitActionV2, ExitPolicyV2, ExitRuleV2,
    PreconditionV2, RiskPolicyV2, PositionSizingV2, StrategySpecV2,
    EntryConstraints,
)
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2


def _valid_spec(**overrides) -> StrategySpecV2:
    defaults = dict(
        name="test_v2",
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
    defaults.update(overrides)
    return StrategySpecV2(**defaults)


class TestReviewerV2:

    def test_valid_spec_passes(self):
        reviewer = StrategyReviewerV2()
        result = reviewer.review(_valid_spec())
        assert result.passed

    def test_schema_error_fails(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(name="")
        result = reviewer.review(spec)
        assert not result.passed
        assert any(i.category == "schema" for i in result.issues)

    def test_contradiction_detected(self):
        """all(imbalance > 0.5, imbalance < 0.1) is impossible."""
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(entry_policies=[
            EntryPolicyV2(
                name="contradictory", side="long",
                trigger=AllExpr(children=[
                    ComparisonExpr(feature="order_imbalance", op=">", threshold=0.5),
                    ComparisonExpr(feature="order_imbalance", op="<", threshold=0.1),
                ]),
                strength=ConstExpr(value=0.5),
            ),
        ])
        result = reviewer.review(spec)
        assert any(i.category == "logical_contradiction" for i in result.issues)

    def test_no_contradiction_when_different_features(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(entry_policies=[
            EntryPolicyV2(
                name="ok", side="long",
                trigger=AllExpr(children=[
                    ComparisonExpr(feature="order_imbalance", op=">", threshold=0.5),
                    ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                ]),
                strength=ConstExpr(value=0.5),
            ),
        ])
        result = reviewer.review(spec)
        assert not any(i.category == "logical_contradiction" for i in result.issues)

    def test_risk_inconsistency_inventory_cap(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(risk_policy=RiskPolicyV2(
            max_position=1000, inventory_cap=500,
        ))
        result = reviewer.review(spec)
        assert any(i.category == "risk_inconsistency" for i in result.issues)

    def test_risk_inconsistency_base_gt_max(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(risk_policy=RiskPolicyV2(
            position_sizing=PositionSizingV2(base_size=600, max_size=400),
        ))
        result = reviewer.review(spec)
        assert any(i.category == "risk_inconsistency" for i in result.issues)

    def test_missing_close_all_error(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="reduce", priority=1,
                    condition=ConstExpr(1.0),
                    action=ExitActionV2(type="reduce_position"),
                ),
            ]),
        ])
        result = reviewer.review(spec)
        assert not result.passed
        assert any(
            i.category == "exit_completeness" and i.severity == "error"
            for i in result.issues
        )

    def test_unknown_feature_info(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(entry_policies=[
            EntryPolicyV2(
                name="exotic", side="long",
                trigger=ComparisonExpr(feature="exotic_signal_xyz", op=">", threshold=0.5),
                strength=ConstExpr(value=0.5),
            ),
        ])
        result = reviewer.review(spec)
        assert any(i.category == "feature_availability" for i in result.issues)

    def test_large_cooldown_warning(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(entry_policies=[
            EntryPolicyV2(
                name="slow", side="long",
                trigger=ConstExpr(1.0),
                strength=ConstExpr(0.5),
                constraints=EntryConstraints(cooldown_ticks=50000),
            ),
        ])
        result = reviewer.review(spec)
        assert any(i.category == "unreachable_entry" for i in result.issues)

    def test_precondition_contradiction(self):
        reviewer = StrategyReviewerV2()
        spec = _valid_spec(preconditions=[
            PreconditionV2(
                name="impossible",
                condition=AllExpr(children=[
                    ComparisonExpr(feature="spread_bps", op=">", threshold=50.0),
                    ComparisonExpr(feature="spread_bps", op="<", threshold=10.0),
                ]),
            ),
        ])
        result = reviewer.review(spec)
        assert any(i.category == "logical_contradiction" for i in result.issues)
