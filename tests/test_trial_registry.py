from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_registry.lineage import LineageTracker
from strategy_block.strategy_registry.trial_registry import (
    TrialRecord,
    TrialRegistry,
    VALID_REJECT_REASONS,
    VALID_STAGES,
)


def _record(*, trial_id: str, family_id: str | None, parent_trial_id: str | None = None) -> TrialRecord:
    return TrialRecord(
        trial_id=trial_id,
        strategy_name="demo_strategy",
        strategy_version="2.0",
        source_spec_path="strategies/demo_strategy_v2.0.json",
        parent_trial_id=parent_trial_id,
        family_id=family_id,
        stage="DRAFT",
        status="ACTIVE",
        reject_reason=None,
        metadata={"seed": 1},
    )


def test_trial_registry_create_get_update_reject(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "trials")

    created = registry.create(_record(trial_id="trial-001", family_id="fam-a"))
    assert created.trial_id == "trial-001"
    assert created.stage == "DRAFT"

    loaded = registry.get("trial-001")
    assert loaded is not None
    assert loaded.strategy_name == "demo_strategy"

    reviewed = registry.update_stage("trial-001", "REVIEWED", static_passed=True)
    assert reviewed.stage == "REVIEWED"
    assert reviewed.metadata["static_passed"] is True

    rejected = registry.reject("trial-001", "REJECTED_LEAKAGE", lint_code="LOOKAHEAD_SUSPICIOUS_FEATURE")
    assert rejected.status == "REJECTED"
    assert rejected.reject_reason == "REJECTED_LEAKAGE"
    assert rejected.metadata["lint_code"] == "LOOKAHEAD_SUSPICIOUS_FEATURE"


def test_trial_registry_list_by_family_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "trials"
    registry = TrialRegistry(path)
    registry.create(_record(trial_id="trial-a1", family_id="fam-a"))
    registry.create(_record(trial_id="trial-a2", family_id="fam-a", parent_trial_id="trial-a1"))
    registry.create(_record(trial_id="trial-b1", family_id="fam-b"))

    reloaded = TrialRegistry(path)
    family_trials = reloaded.list_by_family("fam-a")

    assert {r.trial_id for r in family_trials} == {"trial-a1", "trial-a2"}


def test_trial_registry_attach_family_persists_and_is_queryable(tmp_path: Path) -> None:
    path = tmp_path / "trials"
    registry = TrialRegistry(path)
    registry.create(_record(trial_id="trial-x1", family_id=None))

    attached = registry.attach_family("trial-x1", "fam-new", fingerprint_version="v1")
    assert attached.family_id == "fam-new"
    assert attached.metadata["fingerprint_version"] == "v1"

    reloaded = TrialRegistry(path)
    loaded = reloaded.get("trial-x1")
    assert loaded is not None
    assert loaded.family_id == "fam-new"
    assert loaded.metadata["fingerprint_version"] == "v1"
    assert [record.trial_id for record in reloaded.list_by_family("fam-new")] == ["trial-x1"]


def test_lineage_tracker_link_parent_child_ancestors_descendants(tmp_path: Path) -> None:
    tracker = LineageTracker(tmp_path / "lineage_edges.json")

    tracker.link_parent_child("t-parent", "t-child", "generated_from_goal")
    tracker.link_parent_child("t-child", "t-grandchild", "repaired_from")
    tracker.link_parent_child("t-parent", "t-sibling", "reviewed_after")

    # duplicate edge should be ignored
    tracker.link_parent_child("t-parent", "t-child", "generated_from_goal")

    assert tracker.ancestors("t-grandchild") == ["t-child", "t-parent"]
    assert set(tracker.descendants("t-parent")) == {"t-child", "t-grandchild", "t-sibling"}


def test_trial_registry_promotion_stages_and_reject_reasons(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "trials")
    registry.create(_record(trial_id="trial-prom", family_id="fam-prom"))

    wf = registry.update_stage("trial-prom", "WF_PASSED", wf_score=0.12)
    assert wf.stage == "WF_PASSED"
    assert wf.metadata["wf_score"] == 0.12

    cand = registry.update_stage("trial-prom", "PROMOTION_CANDIDATE")
    assert cand.stage == "PROMOTION_CANDIDATE"

    exported = registry.update_stage("trial-prom", "CONTRACT_EXPORTED", bundle_path="outputs/promotion_reports/x")
    assert exported.stage == "CONTRACT_EXPORTED"
    assert exported.metadata["bundle_path"] == "outputs/promotion_reports/x"

    handoff = registry.update_stage("trial-prom", "HANDOFF_READY")
    assert handoff.stage == "HANDOFF_READY"

    rejected = registry.reject("trial-prom", "REJECTED_PROMOTION_GATE", gate_reason="gate_fail")
    assert rejected.status == "REJECTED"
    assert rejected.reject_reason == "REJECTED_PROMOTION_GATE"
    assert rejected.metadata["gate_reason"] == "gate_fail"


def test_trial_registry_exports_stage_and_reject_constants() -> None:
    assert "WF_PASSED" in VALID_STAGES
    assert "PROMOTION_CANDIDATE" in VALID_STAGES
    assert "CONTRACT_EXPORTED" in VALID_STAGES
    assert "HANDOFF_READY" in VALID_STAGES

    assert "REJECTED_WALK_FORWARD" in VALID_REJECT_REASONS
    assert "REJECTED_PROMOTION_GATE" in VALID_REJECT_REASONS
