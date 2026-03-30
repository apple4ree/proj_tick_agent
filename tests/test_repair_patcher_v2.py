from __future__ import annotations

from strategy_block.strategy_review.v2.contracts import RepairOperation, RepairPlan
from strategy_block.strategy_review.v2.patcher_v2 import StrategyRepairPatcherV2
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    ExecutionPolicyV2,
    RiskPolicyV2,
    StrategySpecV2,
)


def _base_spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="patch_base",
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
                        name="market_exit",
                        priority=1,
                        condition=ComparisonExpr(feature="spread_bps", op=">", threshold=50.0),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            ),
        ],
        execution_policy=ExecutionPolicyV2(
            placement_mode="passive_join",
            cancel_after_ticks=5,
            max_reprices=1,
        ),
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=300),
    )


def test_patcher_applies_core_operations_deterministically():
    original = _base_spec()
    plan = RepairPlan(
        summary="tune execution and sizing",
        operations=[
            RepairOperation(op="set_cancel_after_ticks", target="execution_policy", value=20, reason="x"),
            RepairOperation(op="set_max_reprices", target="execution_policy", value=3, reason="x"),
            RepairOperation(op="set_placement_mode", target="execution_policy", value="passive_only", reason="x"),
            RepairOperation(op="set_base_size", target="risk_policy", value=50, reason="x"),
            RepairOperation(op="set_max_size", target="risk_policy", value=200, reason="x"),
        ],
    )

    patched = StrategyRepairPatcherV2().apply(original, plan)

    assert patched.execution_policy is not None
    assert patched.execution_policy.cancel_after_ticks == 20
    assert patched.execution_policy.max_reprices == 3
    assert patched.execution_policy.placement_mode == "passive_only"
    assert patched.risk_policy.position_sizing.base_size == 50
    assert patched.risk_policy.position_sizing.max_size == 200
    # original is unchanged
    assert original.execution_policy is not None
    assert original.execution_policy.cancel_after_ticks == 5


def test_patcher_adds_stop_loss_and_time_exit():
    spec = _base_spec()
    plan = RepairPlan(
        summary="add robust exits",
        operations=[
            RepairOperation(op="add_stop_loss_exit", target="exits", value={"threshold_bps": -30.0}, reason="x"),
            RepairOperation(op="add_time_exit", target="exits", value={"holding_ticks": 60}, reason="x"),
        ],
    )

    patched = StrategyRepairPatcherV2().apply(spec, plan)
    rules = patched.exit_policies[0].rules
    names = {r.name for r in rules}
    assert "auto_stop_loss_exit" in names
    assert "auto_time_exit" in names
    assert patched.validate() == []


def test_patcher_tighten_inventory_cap_not_below_max_position():
    spec = _base_spec()
    plan = RepairPlan(
        summary="tighten inventory",
        operations=[
            RepairOperation(op="tighten_inventory_cap", target="risk_policy", value={"factor": 0.2}, reason="x"),
        ],
    )

    patched = StrategyRepairPatcherV2().apply(spec, plan)
    assert patched.risk_policy.inventory_cap >= patched.risk_policy.max_position


def test_patcher_set_holding_ticks_updates_existing_rule_or_adds():
    spec = _base_spec()
    plan = RepairPlan(
        summary="set holding ticks",
        operations=[
            RepairOperation(op="set_holding_ticks", target="exits", value=80, reason="x"),
        ],
    )

    patched = StrategyRepairPatcherV2().apply(spec, plan)
    # Either updated or newly added, but should exist with threshold 80
    found = False
    for rule in patched.exit_policies[0].rules:
        cond = rule.condition
        if getattr(cond, "left", None) is not None and getattr(cond.left, "name", None) == "holding_ticks":
            assert float(cond.threshold) == 80.0
            found = True
    assert found


def test_patcher_creates_execution_policy_when_missing():
    spec = _base_spec()
    spec.execution_policy = None

    plan = RepairPlan(
        summary="insert missing execution policy",
        operations=[
            RepairOperation(op="set_placement_mode", target="execution_policy", value="passive_join", reason="x"),
            RepairOperation(op="set_cancel_after_ticks", target="execution_policy", value=15, reason="x"),
            RepairOperation(op="set_max_reprices", target="execution_policy", value=2, reason="x"),
        ],
    )

    patched = StrategyRepairPatcherV2().apply(spec, plan)
    assert patched.execution_policy is not None
    assert patched.execution_policy.placement_mode == "passive_join"
    assert patched.execution_policy.cancel_after_ticks == 15
    assert patched.execution_policy.max_reprices == 2
