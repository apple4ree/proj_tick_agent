from __future__ import annotations

from pathlib import Path

from strategy_block.strategy_registry.trial_accounting import TrialAccounting
from strategy_block.strategy_registry.trial_registry import TrialRecord, TrialRegistry


def _record(
    *,
    trial_id: str,
    family_id: str | None,
    stage: str = "DRAFT",
    status: str = "ACTIVE",
    reject_reason: str | None = None,
) -> TrialRecord:
    return TrialRecord(
        trial_id=trial_id,
        strategy_name="demo_strategy",
        strategy_version="2.0",
        source_spec_path="strategies/demo_strategy_v2.0.json",
        parent_trial_id=None,
        family_id=family_id,
        stage=stage,
        status=status,
        reject_reason=reject_reason,
        metadata={"seed": 1},
    )


def test_trial_accounting_builds_snapshot_from_registry_list_all(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "trials")
    registry.create(_record(trial_id="trial-a1", family_id="fam-a"))
    registry.create(_record(trial_id="trial-a2", family_id="fam-a", stage="BACKTESTED"))
    registry.create(_record(trial_id="trial-b1", family_id="fam-b", stage="BACKTESTED"))
    registry.create(_record(trial_id="trial-c1", family_id=None, stage="REVIEWED"))

    registry.update_stage("trial-a2", "WF_PASSED")
    registry.reject("trial-b1", "REJECTED_WALK_FORWARD")

    accounting = TrialAccounting()
    records = registry.list_all()
    snapshot = accounting.build_snapshot(records)

    assert snapshot.total_trials == 4
    assert snapshot.active_trials == 3
    assert snapshot.rejected_trials == 1
    assert snapshot.family_trial_counts == {"fam-a": 2, "fam-b": 1}
    assert snapshot.family_active_counts == {"fam-a": 2}
    assert snapshot.stage_counts == {
        "BACKTESTED": 1,
        "DRAFT": 1,
        "REVIEWED": 1,
        "WF_PASSED": 1,
    }
    assert snapshot.reject_reason_counts == {"REJECTED_WALK_FORWARD": 1}
    assert accounting.family_count(records, "fam-a") == 2
    assert accounting.active_family_count(records, "fam-a") == 2
    assert accounting.active_family_count(records, "fam-b") == 0
    assert [record.trial_id for record in registry.list_active()] == [
        "trial-a1",
        "trial-a2",
        "trial-c1",
    ]


def test_trial_accounting_handles_empty_and_partial_registry_gracefully(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "trials")
    accounting = TrialAccounting()

    empty_snapshot = accounting.build_snapshot(registry.list_all())
    assert empty_snapshot.total_trials == 0
    assert empty_snapshot.active_trials == 0
    assert empty_snapshot.rejected_trials == 0
    assert empty_snapshot.family_trial_counts == {}
    assert empty_snapshot.family_active_counts == {}
    assert empty_snapshot.stage_counts == {}
    assert empty_snapshot.reject_reason_counts == {}

    registry.create(_record(trial_id="trial-x1", family_id=None))
    partial_records = registry.list_all()
    partial_snapshot = accounting.build_snapshot(partial_records)

    assert partial_snapshot.total_trials == 1
    assert partial_snapshot.family_trial_counts == {}
    assert partial_snapshot.family_active_counts == {}
    assert partial_snapshot.stage_counts == {"DRAFT": 1}
    assert partial_snapshot.reject_reason_counts == {}
    assert accounting.family_count(partial_records, None) == 0
    assert accounting.active_family_count(partial_records, None) == 0
