"""Tests for StrategyReviewerV2 hard gate promotions.

Verifies that critical semantic errors are severity="error" (hard gate)
so that broken specs cannot reach backtest:

1. exit_completeness: no close_all exit → error, passed=False
2. exit_semantics_risk: entry gates + no unconditional close_all → error
3. regime_exit_coverage: regime entries + no global close_all → error
4. state_deadlock: loss_streak never reset → error
5. dead_exit_path: exit rule uses position_attr as feature → error
6. Regression: valid specs still pass
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_specs.v2.ast_nodes import (
    AllExpr,
    ComparisonExpr,
    ConstExpr,
    PositionAttrExpr,
    StateVarExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryConstraints,
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    PreconditionV2,
    RegimeV2,
    RiskPolicyV2,
    StatePolicyV2,
    StateEventV2,
    StateGuardV2,
    StateUpdateV2,
    StrategySpecV2,
)
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_spec(**overrides) -> StrategySpecV2:
    """A fully valid spec that should always pass review."""
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
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="stop_loss",
                    priority=1,
                    condition=ComparisonExpr(
                        left=PositionAttrExpr("unrealized_pnl_bps"),
                        op="<=",
                        threshold=-25.0,
                    ),
                    action=ExitActionV2(type="close_all"),
                ),
                ExitRuleV2(
                    name="time_exit",
                    priority=2,
                    condition=ComparisonExpr(
                        left=PositionAttrExpr("holding_ticks"),
                        op=">=",
                        threshold=100.0,
                    ),
                    action=ExitActionV2(type="close_all"),
                ),
            ]),
        ],
        risk_policy=RiskPolicyV2(max_position=500, inventory_cap=1000),
    )
    defaults.update(overrides)
    return StrategySpecV2(**defaults)


def _review(spec: StrategySpecV2):
    return StrategyReviewerV2().review(spec)


def _has_error(result, category: str) -> bool:
    return any(
        i.category == category and i.severity == "error"
        for i in result.issues
    )


def _has_warning(result, category: str) -> bool:
    return any(
        i.category == category and i.severity == "warning"
        for i in result.issues
    )


# ===================================================================
# 0. Regression: valid spec passes
# ===================================================================

class TestValidSpecStillPasses:

    def test_minimal_valid_spec(self):
        result = _review(_valid_spec())
        assert result.passed, [i.to_dict() for i in result.issues if i.severity == "error"]

    def test_valid_spec_with_precondition(self):
        """Precondition present but has close_all exit → still passes."""
        spec = _valid_spec(preconditions=[
            PreconditionV2(
                name="spread_filter",
                condition=ComparisonExpr(feature="spread_bps", op="<", threshold=30.0),
            ),
        ])
        result = _review(spec)
        assert result.passed, [i.to_dict() for i in result.issues if i.severity == "error"]

    def test_valid_spec_with_regime(self):
        """Regime with entry refs but global exit has close_all → passes."""
        spec = _valid_spec(regimes=[
            RegimeV2(
                name="normal",
                priority=10,
                when=ComparisonExpr(feature="spread_bps", op="<", threshold=20.0),
                entry_policy_refs=["long_entry"],
            ),
        ])
        result = _review(spec)
        assert result.passed, [i.to_dict() for i in result.issues if i.severity == "error"]

    def test_valid_spec_with_state_policy_and_reset(self):
        """loss_streak incremented AND reset → passes (no deadlock)."""
        spec = _valid_spec(state_policy=StatePolicyV2(
            vars={"loss_streak": 0.0},
            events=[
                StateEventV2(
                    name="inc_loss",
                    on="on_exit_loss",
                    updates=[StateUpdateV2(var="loss_streak", op="increment", value=1.0)],
                ),
                StateEventV2(
                    name="reset_loss",
                    on="on_exit_profit",
                    updates=[StateUpdateV2(var="loss_streak", op="reset")],
                ),
            ],
        ))
        result = _review(spec)
        assert result.passed, [i.to_dict() for i in result.issues if i.severity == "error"]


# ===================================================================
# 1. exit_completeness: no close_all → error
# ===================================================================

class TestExitCompletenessHardGate:

    def test_no_close_all_is_error(self):
        spec = _valid_spec(exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="reduce_only",
                    priority=1,
                    condition=ConstExpr(1.0),
                    action=ExitActionV2(type="reduce_position"),
                ),
            ]),
        ])
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "exit_completeness")

    def test_has_close_all_no_error(self):
        result = _review(_valid_spec())
        assert not _has_error(result, "exit_completeness")


# ===================================================================
# 2. exit_semantics_risk: entry gates + no unconditional close_all → error
# ===================================================================

class TestExitSemanticsRiskHardGate:

    def test_preconditions_with_market_only_exit_is_error(self):
        """Preconditions present, exit close_all only uses market features → error.

        Market-feature-only exits are not robust fail-safes because they depend
        on external conditions, not position health.
        """
        spec = _valid_spec(
            preconditions=[
                PreconditionV2(
                    name="spread_gate",
                    condition=ComparisonExpr(feature="spread_bps", op="<", threshold=20.0),
                ),
            ],
            exit_policies=[
                ExitPolicyV2(name="exits", rules=[
                    ExitRuleV2(
                        name="spread_exit",
                        priority=1,
                        condition=ComparisonExpr(
                            feature="spread_bps", op=">", threshold=30.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                ]),
            ],
        )
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "exit_semantics_risk")

    def test_preconditions_with_robust_exit_no_error(self):
        """Preconditions present + stop-loss on unrealized_pnl_bps → passes."""
        spec = _valid_spec(
            preconditions=[
                PreconditionV2(
                    name="spread_gate",
                    condition=ComparisonExpr(feature="spread_bps", op="<", threshold=20.0),
                ),
            ],
        )
        result = _review(spec)
        assert not _has_error(result, "exit_semantics_risk")

    def test_do_not_trade_with_conditional_exit_is_error(self):
        """do_not_trade_when present, conditional close_all → error."""
        from strategy_block.strategy_specs.v2.schema_v2 import ExecutionPolicyV2
        spec = _valid_spec(
            execution_policy=ExecutionPolicyV2(
                do_not_trade_when=ComparisonExpr(feature="spread_bps", op=">", threshold=50.0),
            ),
            exit_policies=[
                ExitPolicyV2(name="exits", rules=[
                    ExitRuleV2(
                        name="stop",
                        priority=1,
                        condition=ComparisonExpr(feature="spread_bps", op=">", threshold=30.0),
                        action=ExitActionV2(type="close_all"),
                    ),
                ]),
            ],
        )
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "exit_semantics_risk")

    def test_no_gates_no_error(self):
        """No preconditions/regimes/do_not_trade → no exit_semantics_risk error."""
        result = _review(_valid_spec())
        assert not _has_error(result, "exit_semantics_risk")


# ===================================================================
# 3. regime_exit_coverage: regime entries + no global close_all → error
# ===================================================================

class TestRegimeExitCoverageHardGate:

    def test_regime_entry_no_global_close_all_is_error(self):
        spec = _valid_spec(
            exit_policies=[
                ExitPolicyV2(name="exits", rules=[
                    ExitRuleV2(
                        name="reduce_only",
                        priority=1,
                        condition=ConstExpr(1.0),
                        action=ExitActionV2(type="reduce_position"),
                    ),
                ]),
            ],
            regimes=[
                RegimeV2(
                    name="normal",
                    priority=10,
                    when=ComparisonExpr(feature="spread_bps", op="<", threshold=20.0),
                    entry_policy_refs=["long_entry"],
                ),
            ],
        )
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "regime_exit_coverage")

    def test_regime_entry_with_global_close_all_no_error(self):
        """Regime entries + global close_all → no regime_exit_coverage error."""
        spec = _valid_spec(regimes=[
            RegimeV2(
                name="normal",
                priority=10,
                when=ComparisonExpr(feature="spread_bps", op="<", threshold=20.0),
                entry_policy_refs=["long_entry"],
            ),
        ])
        result = _review(spec)
        assert not _has_error(result, "regime_exit_coverage")

    def test_regime_without_entry_refs_no_error(self):
        """Regime without entry_policy_refs → no regime_exit_coverage error."""
        spec = _valid_spec(regimes=[
            RegimeV2(
                name="passive",
                priority=10,
                when=ComparisonExpr(feature="spread_bps", op=">", threshold=40.0),
            ),
        ])
        result = _review(spec)
        assert not _has_error(result, "regime_exit_coverage")


# ===================================================================
# 4. state_deadlock: loss_streak never reset → error
# ===================================================================

class TestStateDeadlockHardGate:

    def test_loss_streak_no_reset_is_error(self):
        spec = _valid_spec(state_policy=StatePolicyV2(
            vars={"loss_streak": 0.0},
            events=[
                StateEventV2(
                    name="inc_loss",
                    on="on_exit_loss",
                    updates=[StateUpdateV2(var="loss_streak", op="increment", value=1.0)],
                ),
            ],
        ))
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "state_deadlock")

    def test_loss_streak_with_reset_no_error(self):
        spec = _valid_spec(state_policy=StatePolicyV2(
            vars={"loss_streak": 0.0},
            events=[
                StateEventV2(
                    name="inc_loss",
                    on="on_exit_loss",
                    updates=[StateUpdateV2(var="loss_streak", op="increment", value=1.0)],
                ),
                StateEventV2(
                    name="reset_on_profit",
                    on="on_exit_profit",
                    updates=[StateUpdateV2(var="loss_streak", op="reset")],
                ),
            ],
        ))
        result = _review(spec)
        assert not _has_error(result, "state_deadlock")

    def test_no_state_policy_no_error(self):
        result = _review(_valid_spec())
        assert not _has_error(result, "state_deadlock")


# ===================================================================
# 5. dead_exit_path: exit condition uses position_attr as feature → error
# ===================================================================

class TestDeadExitPathHardGate:

    def test_holding_ticks_as_feature_in_exit_is_error(self):
        """Exit rule uses feature='holding_ticks' → dead (always 0.0)."""
        spec = _valid_spec(exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="stop_loss",
                    priority=1,
                    condition=ComparisonExpr(
                        left=PositionAttrExpr("unrealized_pnl_bps"),
                        op="<=",
                        threshold=-25.0,
                    ),
                    action=ExitActionV2(type="close_all"),
                ),
                ExitRuleV2(
                    name="dead_time_exit",
                    priority=2,
                    # BUG: holding_ticks in feature, not position_attr
                    condition=ComparisonExpr(
                        feature="holding_ticks", op=">=", threshold=100.0,
                    ),
                    action=ExitActionV2(type="close_all"),
                ),
            ]),
        ])
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "dead_exit_path")

    def test_unrealized_pnl_as_feature_in_exit_is_error(self):
        """Exit rule uses feature='unrealized_pnl_bps' → dead."""
        spec = _valid_spec(exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="dead_stop",
                    priority=1,
                    # BUG: unrealized_pnl_bps in feature
                    condition=ComparisonExpr(
                        feature="unrealized_pnl_bps", op="<=", threshold=-25.0,
                    ),
                    action=ExitActionV2(type="close_all"),
                ),
                ExitRuleV2(
                    name="time_exit",
                    priority=2,
                    condition=ComparisonExpr(
                        left=PositionAttrExpr("holding_ticks"),
                        op=">=",
                        threshold=100.0,
                    ),
                    action=ExitActionV2(type="close_all"),
                ),
            ]),
        ])
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "dead_exit_path")

    def test_correct_position_attr_exit_no_error(self):
        """Exit using proper PositionAttrExpr → no dead_exit_path error."""
        result = _review(_valid_spec())
        assert not _has_error(result, "dead_exit_path")

    def test_multiple_dead_features_in_composite(self):
        """Composite exit condition with multiple dead features."""
        spec = _valid_spec(exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="dead_composite",
                    priority=1,
                    condition=AllExpr(children=[
                        ComparisonExpr(feature="holding_ticks", op=">=", threshold=50.0),
                        ComparisonExpr(feature="unrealized_pnl_bps", op="<=", threshold=-10.0),
                    ]),
                    action=ExitActionV2(type="close_all"),
                ),
            ]),
        ])
        result = _review(spec)
        assert not result.passed
        assert _has_error(result, "dead_exit_path")
        # Should also mention both dead features
        dead_issue = next(
            i for i in result.issues
            if i.category == "dead_exit_path" and i.severity == "error"
        )
        assert "holding_ticks" in dead_issue.description
        assert "unrealized_pnl_bps" in dead_issue.description

    def test_market_feature_exit_no_dead_path(self):
        """Exit using market features (spread_bps) → no dead_exit_path error."""
        spec = _valid_spec(exit_policies=[
            ExitPolicyV2(name="exits", rules=[
                ExitRuleV2(
                    name="spread_exit",
                    priority=1,
                    condition=ComparisonExpr(feature="spread_bps", op=">", threshold=40.0),
                    action=ExitActionV2(type="close_all"),
                ),
            ]),
        ])
        result = _review(spec)
        assert not _has_error(result, "dead_exit_path")


# ===================================================================
# 6. Combined: multiple hard gates on one spec
# ===================================================================

class TestCombinedHardGates:

    def test_multiple_errors_all_reported(self):
        """A badly broken spec should accumulate multiple errors."""
        spec = _valid_spec(
            exit_policies=[
                ExitPolicyV2(name="exits", rules=[
                    ExitRuleV2(
                        name="dead_stop",
                        priority=1,
                        # position_attr as feature → dead
                        condition=ComparisonExpr(
                            feature="unrealized_pnl_bps", op="<=", threshold=-25.0,
                        ),
                        action=ExitActionV2(type="reduce_position"),
                    ),
                ]),
            ],
            preconditions=[
                PreconditionV2(
                    name="gate",
                    condition=ComparisonExpr(feature="spread_bps", op="<", threshold=20.0),
                ),
            ],
        )
        result = _review(spec)
        assert not result.passed
        error_cats = {i.category for i in result.issues if i.severity == "error"}
        # No close_all at all
        assert "exit_completeness" in error_cats
        # Precondition gate + no unconditional close_all
        assert "exit_semantics_risk" in error_cats
        # Dead exit path (unrealized_pnl_bps as feature)
        assert "dead_exit_path" in error_cats


# ===================================================================
# 7. Regression: warnings that should stay warnings
# ===================================================================

class TestWarningsRemainWarnings:

    def test_risk_inconsistency_stays_warning(self):
        spec = _valid_spec(risk_policy=RiskPolicyV2(
            max_position=1000, inventory_cap=500,
        ))
        result = _review(spec)
        assert _has_warning(result, "risk_inconsistency")
        # Should NOT be an error
        assert not _has_error(result, "risk_inconsistency")

    def test_large_cooldown_stays_warning(self):
        spec = _valid_spec(entry_policies=[
            EntryPolicyV2(
                name="slow_entry",
                side="long",
                trigger=ConstExpr(1.0),
                strength=ConstExpr(0.5),
                constraints=EntryConstraints(cooldown_ticks=50000),
            ),
        ])
        result = _review(spec)
        assert _has_warning(result, "unreachable_entry")
        assert not _has_error(result, "unreachable_entry")

    def test_position_attr_in_entry_stays_warning(self):
        """position_attr in entry path (not as misused feature) stays warning."""
        spec = _valid_spec(entry_policies=[
            EntryPolicyV2(
                name="pnl_gated_entry",
                side="long",
                trigger=AllExpr(children=[
                    ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                    ComparisonExpr(
                        left=PositionAttrExpr("unrealized_pnl_bps"),
                        op=">",
                        threshold=5.0,
                    ),
                ]),
                strength=ConstExpr(value=0.5),
            ),
        ])
        result = _review(spec)
        assert _has_warning(result, "position_attr_sanity")
        assert not _has_error(result, "position_attr_sanity")

    def test_regime_no_exit_ref_stays_warning(self):
        """Regime with entry refs but no exit_policy_ref → stays warning."""
        spec = _valid_spec(regimes=[
            RegimeV2(
                name="normal",
                priority=10,
                when=ComparisonExpr(feature="spread_bps", op="<", threshold=20.0),
                entry_policy_refs=["long_entry"],
            ),
        ])
        result = _review(spec)
        # This specific sub-check (no exit ref on regime) stays warning
        regime_issues = [
            i for i in result.issues
            if i.category == "regime_exit_coverage"
            and "no explicit exit policy ref" in i.description
        ]
        assert all(i.severity == "warning" for i in regime_issues)
