from __future__ import annotations

import json
from pathlib import Path

from strategy_block.strategy_promotion.contract_builder import DeploymentContractBuilder
from strategy_block.strategy_registry.trial_registry import TrialRecord
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExecutionPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    StrategySpecV2,
)


def _spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="promo_demo",
        version="2.0",
        entry_policies=[
            EntryPolicyV2(
                name="entry_long",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.1),
                strength=ConstExpr(1.0),
            )
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exit_default",
                rules=[
                    ExitRuleV2(
                        name="time_exit",
                        priority=1,
                        condition=ComparisonExpr(
                            op=">=",
                            threshold=20,
                            left=PositionAttrExpr("holding_ticks"),
                        ),
                        action=ExitActionV2(type="close_all"),
                    )
                ],
            )
        ],
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=12,
            max_reprices=2,
        ),
    )


def _trial() -> TrialRecord:
    return TrialRecord(
        trial_id="trial-prom-001",
        strategy_name="promo_demo",
        strategy_version="2.0",
        source_spec_path="strategies/promo_demo_v2.0.json",
        parent_trial_id=None,
        family_id="fam-alpha",
        stage="WF_PASSED",
        status="ACTIVE",
        reject_reason=None,
        metadata={
            "allowed_symbols": ["005930"],
            "configured_order_submit_ms": 30.0,
            "configured_cancel_ms": 20.0,
            "effective_delay_ms": 10.0,
            "static_review": {
                "issues": [{"category": "execution_policy_too_aggressive"}],
            },
        },
    )


def _walk_forward_report(tmp_path: Path) -> dict:
    run_dir = tmp_path / "wf_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "canonical_tick_interval_ms": 500,
                "configured_order_submit_ms": 30.0,
                "configured_cancel_ms": 20.0,
                "effective_delay_ms": 10.0,
            }
        ),
        encoding="utf-8",
    )

    return {
        "n_windows": 3,
        "mode": "single",
        "symbol": "005930",
        "decision": {
            "passed": True,
            "reasons": ["cost_dominated_share_too_high"],
            "aggregate_score": 0.12,
            "metadata": {
                "n_valid_windows": 3,
                "n_pass_windows": 2,
                "churn_heavy_share": 0.33,
                "cost_dominated_share": 0.33,
                "adverse_selection_dominated_share": 0.0,
            },
        },
        "window_results": [
            {
                "run_dir": str(run_dir),
                "metadata": {
                    "children_per_parent": 6.0,
                    "flags": {
                        "churn_heavy": True,
                        "queue_ineffective": False,
                        "cost_dominated": False,
                        "adverse_selection_dominated": False,
                    },
                },
            },
            {
                "run_dir": str(run_dir),
                "metadata": {
                    "children_per_parent": 4.0,
                    "flags": {
                        "churn_heavy": False,
                        "queue_ineffective": False,
                        "cost_dominated": True,
                        "adverse_selection_dominated": False,
                    },
                },
            },
        ],
    }


def test_contract_builder_populates_core_fields(tmp_path: Path) -> None:
    builder = DeploymentContractBuilder()
    contract = builder.build(
        spec=_spec(),
        trial_record=_trial(),
        walk_forward_report=_walk_forward_report(tmp_path),
        selection_cfg={
            "promotion": {
                "gate": {
                    "min_aggregate_score": -0.2,
                    "max_churn_heavy_share": 0.7,
                    "max_cost_dominated_share": 0.8,
                },
                "contract": {
                    "required_monitoring_metrics": ["aggregate_score", "queue_blocked_count"],
                    "required_disable_conditions": ["disable_if_market_data_stale"],
                    "tick_seconds_default": 1.0,
                },
            }
        },
    )

    assert contract.strategy_name == "promo_demo"
    assert contract.strategy_version == "2.0"
    assert contract.trial_id == "trial-prom-001"
    assert contract.family_id == "fam-alpha"
    assert contract.allowed_symbols == ["005930"]
    assert contract.expected_holding_horizon_s is not None
    assert contract.expected_holding_horizon_s[0] > 0
    assert contract.max_turnover == 5.0
    assert contract.latency_budget_ms == 60.0
    assert "order_imbalance" in contract.required_features
    assert "default" in contract.regime_dependencies
    assert "aggregate_score" in contract.monitoring_metrics
    assert "queue_blocked_count" in contract.monitoring_metrics
    assert any(item.startswith("disable_if_aggregate_score_below:") for item in contract.disable_conditions)
    assert "disable_if_market_data_stale" in contract.disable_conditions
    assert any("walk_forward_reason:" in item for item in contract.known_failure_modes)
    assert any("flag_prevalence:churn_heavy" in item for item in contract.known_failure_modes)
    assert any("review_issue:execution_policy_too_aggressive" == item for item in contract.known_failure_modes)


def test_contract_builder_without_trial_uses_report_scope(tmp_path: Path) -> None:
    builder = DeploymentContractBuilder()
    report = _walk_forward_report(tmp_path)
    report["mode"] = "single"
    report["symbol"] = "000660"

    contract = builder.build(
        spec=_spec(),
        trial_record=None,
        walk_forward_report=report,
        selection_cfg={"promotion": {"contract": {"allowed_symbols_default": ["005930"]}}},
    )

    assert contract.trial_id is None
    assert contract.family_id is None
    assert contract.allowed_symbols == ["000660"]
