from __future__ import annotations

import copy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_generation.v2.generation_rescue import GenerationRescue
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    ExecutionPolicyV2,
    RiskPolicyV2,
    StrategySpecV2,
)


def _base_spec(
    *,
    holding_ticks: float = 30.0,
    execution_policy: ExecutionPolicyV2 | None = None,
) -> StrategySpecV2:
    return StrategySpecV2(
        name="generation_rescue_base",
        description="unit test base spec",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                strength=ConstExpr(0.5),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="stop_loss",
                        priority=1,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("unrealized_pnl_bps"),
                            op="<=",
                            threshold=-20.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                    ExitRuleV2(
                        name="time_exit",
                        priority=2,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("holding_ticks"),
                            op=">=",
                            threshold=holding_ticks,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            ),
        ],
        execution_policy=execution_policy,
        risk_policy=RiskPolicyV2(max_position=200, inventory_cap=400),
        metadata={"strategy_style": "momentum", "inferred_short_horizon": True},
    )


def _review_issue(*, category: str, description: str, severity: str = "error") -> dict[str, str]:
    return {
        "severity": severity,
        "category": category,
        "description": description,
    }


def test_rescue_raises_zero_horizon_to_positive_ticks():
    spec = _base_spec(
        holding_ticks=0.0,
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=10,
            max_reprices=1,
        ),
    )
    review_result = {
        "passed": False,
        "issues": [
            _review_issue(
                category="leakage_feature_time_risk",
                description="[FEATURE_TIME_NEAR_ZERO_HORIZON] holding horizon is configured as 0 ticks",
            ),
        ],
    }

    result = GenerationRescue().maybe_rescue(spec=spec, review_result=review_result)

    assert result.applied is True
    assert "raise_non_positive_holding_horizon_to_min_10" in result.operations
    assert result.rescued_spec is not None
    rescued = result.rescued_spec
    horizons = []
    for xp in rescued.exit_policies:
        for rule in xp.rules:
            cond = rule.condition
            if isinstance(cond, ComparisonExpr) and isinstance(cond.left, PositionAttrExpr):
                if cond.left.name == "holding_ticks":
                    horizons.append(float(cond.threshold))
    assert horizons
    assert min(horizons) >= 10.0


def test_rescue_inserts_default_execution_policy_for_short_horizon_missing_ep():
    spec = _base_spec(holding_ticks=10.0, execution_policy=None)
    review_result = {
        "passed": False,
        "issues": [
            _review_issue(
                category="missing_execution_policy_for_short_horizon",
                description="Short horizon but no explicit execution_policy",
            ),
        ],
    }

    result = GenerationRescue().maybe_rescue(spec=spec, review_result=review_result)

    assert result.applied is True
    assert "insert_default_execution_policy_for_short_horizon" in result.operations
    rescued = result.rescued_spec
    assert rescued is not None
    assert rescued.execution_policy is not None
    assert rescued.execution_policy.placement_mode == "passive_join"
    assert rescued.execution_policy.cancel_after_ticks == 10
    assert rescued.execution_policy.max_reprices == 2


def test_rescue_clamps_aggressive_passive_repricing_envelope():
    spec = _base_spec(
        holding_ticks=10.0,
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=1,
            max_reprices=7,
        ),
    )
    review_result = {
        "passed": False,
        "issues": [
            _review_issue(
                category="execution_policy_too_aggressive",
                description="short-horizon passive max_reprices is too high",
            ),
            _review_issue(
                category="churn_risk_high",
                description="cancel_after_ticks is too short for passive short-horizon strategy",
            ),
        ],
    }

    result = GenerationRescue().maybe_rescue(spec=spec, review_result=review_result)

    assert result.applied is True
    assert "clamp_passive_max_reprices_to_2" in result.operations
    assert "raise_passive_cancel_after_ticks_to_10" in result.operations
    rescued = result.rescued_spec
    assert rescued is not None and rescued.execution_policy is not None
    assert rescued.execution_policy.max_reprices == 2
    assert rescued.execution_policy.cancel_after_ticks == 10


def test_non_rescuable_issue_returns_applied_false():
    spec = _base_spec(holding_ticks=10.0, execution_policy=None)
    review_result = {
        "passed": False,
        "issues": [
            _review_issue(
                category="leakage_lookahead_risk",
                description="[LOOKAHEAD_FUTURE_REF] strategy references future ticks",
            ),
        ],
    }

    result = GenerationRescue().maybe_rescue(spec=spec, review_result=review_result)

    assert result.applied is False
    assert result.rescued_spec is None
    assert result.metadata.get("eligible") is False
    assert result.metadata.get("skip_reason") == "non_rescuable_error_present"


def test_operations_and_reasons_are_deterministic():
    spec = _base_spec(
        holding_ticks=0.0,
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=1,
            max_reprices=5,
        ),
    )
    review_result = {
        "passed": False,
        "issues": [
            _review_issue(
                category="leakage_feature_time_risk",
                description="[FEATURE_TIME_NEAR_ZERO_HORIZON] holding horizon is configured as 0 ticks",
            ),
            _review_issue(
                category="execution_policy_too_aggressive",
                description="short-horizon passive repricing budget exceeds safe range",
            ),
        ],
    }

    rescue = GenerationRescue()
    result_1 = rescue.maybe_rescue(spec=copy.deepcopy(spec), review_result=review_result)
    result_2 = rescue.maybe_rescue(spec=copy.deepcopy(spec), review_result=review_result)

    assert result_1.applied is True
    assert result_1.operations == result_2.operations
    assert result_1.reasons == result_2.reasons
    assert result_1.operations == [
        "raise_non_positive_holding_horizon_to_min_10",
        "clamp_passive_max_reprices_to_2",
        "raise_passive_cancel_after_ticks_to_10",
    ]
