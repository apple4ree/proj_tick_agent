from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_review.leakage_lints import (
    FeatureTimeGuard,
    FillAlignmentGuard,
    LatencyFeasibilityGuard,
    LeakageLintRunner,
    LookaheadGuard,
)
from strategy_block.strategy_specs.v2.ast_nodes import AllExpr, ComparisonExpr, ConstExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryConstraints,
    EntryPolicyV2,
    ExecutionPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    RiskPolicyV2,
    StrategySpecV2,
)


def _spec(
    *,
    entry_feature: str = "order_imbalance",
    entry_left=None,
    horizon_ticks: int = 10,
    execution_policy: ExecutionPolicyV2 | None = None,
    cooldown_ticks: int = 0,
) -> StrategySpecV2:
    trigger = (
        ComparisonExpr(left=entry_left, op=">", threshold=0.0)
        if entry_left is not None
        else ComparisonExpr(feature=entry_feature, op=">", threshold=0.2)
    )
    return StrategySpecV2(
        name="lint_spec",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=trigger,
                strength=ConstExpr(value=0.5),
                constraints=EntryConstraints(cooldown_ticks=cooldown_ticks),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="stop",
                        priority=1,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("unrealized_pnl_bps"),
                            op="<=",
                            threshold=-20.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                    ExitRuleV2(
                        name="time",
                        priority=2,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("holding_ticks"),
                            op=">=",
                            threshold=float(horizon_ticks),
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            )
        ],
        execution_policy=execution_policy,
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def _env(*, tick_ms: float = 500.0, submit_ms: float = 300.0, cancel_ms: float = 250.0) -> dict:
    return {
        "resample": "500ms",
        "canonical_tick_interval_ms": tick_ms,
        "market_data_delay_ms": 0.0,
        "decision_compute_ms": 0.0,
        "effective_delay_ms": 0.0,
        "latency": {
            "order_submit_ms": submit_ms,
            "order_ack_ms": 0.0,
            "cancel_ms": cancel_ms,
            "order_ack_used_for_fill_gating": False,
        },
        "queue": {
            "queue_model": "prob_queue",
            "queue_position_assumption": 0.5,
        },
        "semantics": {
            "replace_model": "minimal_immediate",
        },
    }


def test_feature_time_guard_flags_near_zero_horizon() -> None:
    spec = _spec(horizon_ticks=0)
    issues = FeatureTimeGuard().run(spec)
    assert any(i.code == "FEATURE_TIME_NEAR_ZERO_HORIZON" and i.severity == "error" for i in issues)


def test_lookahead_guard_flags_future_named_feature() -> None:
    spec = _spec(entry_feature="future_mid_price")
    issues = LookaheadGuard().run(spec)
    assert any(i.code == "LOOKAHEAD_SUSPICIOUS_FEATURE" and i.severity == "error" for i in issues)


def test_fill_alignment_guard_flags_position_attr_as_feature() -> None:
    spec = _spec(entry_feature="holding_ticks")
    issues = FillAlignmentGuard().run(spec)
    assert any(i.code == "FILL_ALIGNMENT_POSITION_ATTR_AS_FEATURE" and i.severity == "error" for i in issues)


def test_latency_feasibility_guard_env_aware_short_passive_case() -> None:
    spec = _spec(
        horizon_ticks=2,
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=1,
            max_reprices=4,
        ),
    )
    issues = LatencyFeasibilityGuard().run(spec, backtest_environment=_env())
    codes = {i.code for i in issues}
    assert "LATENCY_FEASIBILITY_TINY_CANCEL_HORIZON" in codes
    assert "LATENCY_FEASIBILITY_LATENCY_TICK_RATIO_MISMATCH" in codes


def test_leakage_lint_runner_merges_in_deterministic_guard_order() -> None:
    spec = _spec(
        entry_feature="holding_ticks",
        horizon_ticks=0,
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=1,
            max_reprices=4,
        ),
        cooldown_ticks=0,
    )
    spec.entry_policies[0].trigger = AllExpr(children=[
        ComparisonExpr(feature="future_mid_price", op=">", threshold=0.1),
        ComparisonExpr(feature="holding_ticks", op=">", threshold=1.0),
    ])
    result = LeakageLintRunner().run(spec, backtest_environment=_env())

    assert result.passed is False
    codes = [issue.code for issue in result.issues]
    first_feature = next(i for i, c in enumerate(codes) if c.startswith("FEATURE_TIME"))
    first_lookahead = next(i for i, c in enumerate(codes) if c.startswith("LOOKAHEAD"))
    first_fill = next(i for i, c in enumerate(codes) if c.startswith("FILL_ALIGNMENT"))
    first_latency = next(i for i, c in enumerate(codes) if c.startswith("LATENCY_FEASIBILITY"))
    assert first_feature < first_lookahead < first_fill < first_latency
