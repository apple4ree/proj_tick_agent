from __future__ import annotations

from pathlib import Path

from strategy_block.strategy_registry.family_fingerprint import (
    FamilyFingerprintBuilder,
    fingerprint_similarity,
)
from strategy_block.strategy_registry.family_index import FamilyIndex
from strategy_block.strategy_specs.v2.ast_nodes import AllExpr, ComparisonExpr, ConstExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExecutionPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    StrategySpecV2,
)


def _strategy_spec(
    *,
    name: str,
    version: str,
    features: tuple[str, ...],
    sides: tuple[str, ...] = ("long",),
    placement_mode: str | None = "passive_join",
    cancel_after_ticks: int = 10,
    max_reprices: int = 2,
    holding_ticks: int = 12,
) -> StrategySpecV2:
    children = [ComparisonExpr(feature=feat, op=">", threshold=0.0) for feat in features]
    trigger = children[0] if len(children) == 1 else AllExpr(children=children)

    entry_policies = [
        EntryPolicyV2(
            name=f"entry_{side}",
            side=side,
            trigger=trigger,
            strength=ConstExpr(1.0),
        )
        for side in sides
    ]
    exit_policies = [
        ExitPolicyV2(
            name="exit_policy",
            rules=[
                ExitRuleV2(
                    name="time_exit",
                    priority=1,
                    condition=ComparisonExpr(
                        op=">=",
                        threshold=float(holding_ticks),
                        left=PositionAttrExpr("holding_ticks"),
                    ),
                    action=ExitActionV2(type="close_all"),
                )
            ],
        )
    ]
    execution_policy = None
    if placement_mode is not None:
        execution_policy = ExecutionPolicyV2(
            placement_mode=placement_mode,
            cancel_after_ticks=cancel_after_ticks,
            max_reprices=max_reprices,
        )

    return StrategySpecV2(
        name=name,
        version=version,
        entry_policies=entry_policies,
        exit_policies=exit_policies,
        execution_policy=execution_policy,
        metadata={"note": "fixture"},
    )


def test_fingerprint_is_stable_for_name_version_and_feature_order() -> None:
    builder = FamilyFingerprintBuilder()
    spec_a = _strategy_spec(
        name="alpha_family_a",
        version="2.0",
        features=("order_imbalance", "microprice_delta"),
    )
    spec_b = _strategy_spec(
        name="alpha_family_b",
        version="9.9",
        features=("microprice_delta", "order_imbalance"),
    )

    fp_a = builder.build(spec_a, metadata={"seed": 1})
    fp_b = builder.build(spec_b, metadata={"seed": 2})

    assert fp_a.family_id == fp_b.family_id
    assert fp_a.feature_signature == fp_b.feature_signature


def test_fingerprint_splits_families_for_execution_style_and_horizon() -> None:
    builder = FamilyFingerprintBuilder()
    short_passive = builder.build(
        _strategy_spec(
            name="short_passive",
            version="2.0",
            features=("order_imbalance",),
            placement_mode="passive_join",
            max_reprices=3,
            holding_ticks=8,
        )
    )
    long_aggressive = builder.build(
        _strategy_spec(
            name="long_aggressive",
            version="2.0",
            features=("order_imbalance",),
            placement_mode="aggressive_cross",
            max_reprices=0,
            holding_ticks=260,
        )
    )

    assert short_passive.execution_style != long_aggressive.execution_style
    assert short_passive.horizon_bucket != long_aggressive.horizon_bucket
    assert short_passive.family_id != long_aggressive.family_id


def test_fingerprint_similarity_duplicate_neighbor_and_far() -> None:
    builder = FamilyFingerprintBuilder()
    base = builder.build(
        _strategy_spec(
            name="base",
            version="2.0",
            features=("order_imbalance", "microprice_delta"),
            placement_mode="passive_join",
            max_reprices=2,
            holding_ticks=12,
        )
    )
    neighbor = builder.build(
        _strategy_spec(
            name="neighbor",
            version="2.0",
            features=("order_imbalance", "microprice_delta"),
            placement_mode="aggressive_cross",
            max_reprices=0,
            holding_ticks=12,
        )
    )
    far = builder.build(
        _strategy_spec(
            name="far",
            version="2.0",
            features=("spread_zscore",),
            sides=("short",),
            placement_mode="aggressive_cross",
            max_reprices=0,
            holding_ticks=300,
        )
    )

    assert fingerprint_similarity(base, base) == 1.0
    assert fingerprint_similarity(base, neighbor) >= 0.75
    assert fingerprint_similarity(base, far) < 0.75


def test_family_index_upsert_and_duplicate_neighbor_lookup(tmp_path: Path) -> None:
    index = FamilyIndex(tmp_path / "family_index")
    builder = FamilyFingerprintBuilder()

    base = builder.build(
        _strategy_spec(
            name="base",
            version="2.0",
            features=("order_imbalance", "microprice_delta"),
            placement_mode="passive_join",
            max_reprices=2,
            holding_ticks=12,
        )
    )
    index.upsert(base, "trial-001", tags=["goal_a"], metadata={"source": "gen"})
    index.upsert(base, "trial-002", tags=["goal_b"])
    index.upsert(base, "trial-002", tags=["goal_a"])  # idempotent trial membership

    reloaded = FamilyIndex(tmp_path / "family_index")
    entry = reloaded.get(base.family_id)
    assert entry is not None
    assert entry.representative_trial_id == "trial-001"
    assert entry.member_trial_ids == ["trial-001", "trial-002"]
    assert set(entry.tags) == {"goal_a", "goal_b"}
    assert reloaded.list_members(base.family_id) == ["trial-001", "trial-002"]

    duplicate = reloaded.find_duplicate_or_neighbor(base)
    assert duplicate is not None
    assert duplicate["match_type"] == "duplicate"
    assert duplicate["family_id"] == base.family_id

    neighbor = builder.build(
        _strategy_spec(
            name="neighbor",
            version="2.0",
            features=("order_imbalance", "microprice_delta"),
            placement_mode="aggressive_cross",
            max_reprices=0,
            holding_ticks=12,
        )
    )
    neighbor_match = reloaded.find_duplicate_or_neighbor(
        neighbor,
        duplicate_threshold=0.99,
        neighbor_threshold=0.75,
    )
    assert neighbor_match is not None
    assert neighbor_match["match_type"] == "neighbor"

    far = builder.build(
        _strategy_spec(
            name="far",
            version="2.0",
            features=("spread_zscore",),
            sides=("short",),
            placement_mode="aggressive_cross",
            max_reprices=0,
            holding_ticks=300,
        )
    )
    assert reloaded.find_duplicate_or_neighbor(
        far,
        duplicate_threshold=0.99,
        neighbor_threshold=0.75,
    ) is None
