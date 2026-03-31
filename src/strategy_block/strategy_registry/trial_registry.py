"""File-based trial registry for strategy candidate lineage foundations."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


VALID_STAGES: frozenset[str] = frozenset({"DRAFT", "REVIEWED", "APPROVED", "BACKTESTED", "WF_PASSED", "PROMOTION_CANDIDATE", "CONTRACT_EXPORTED", "HANDOFF_READY"})
VALID_REJECT_REASONS: frozenset[str] = frozenset({"REJECTED_STATIC", "REJECTED_LEAKAGE", "REJECTED_WALK_FORWARD", "REJECTED_PROMOTION_GATE"})
VALID_STATUS: frozenset[str] = frozenset({"ACTIVE", "REJECTED"})


@dataclass
class TrialRecord:
    trial_id: str
    strategy_name: str
    strategy_version: str
    source_spec_path: str | None
    parent_trial_id: str | None
    family_id: str | None
    stage: str
    status: str
    reject_reason: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrialRecord":
        return cls(**payload)


class TrialRegistry:
    """Minimal file-backed trial registry."""

    def __init__(self, registry_dir: str | Path = "strategies/trials") -> None:
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, trial_id: str) -> Path:
        return self.registry_dir / f"{trial_id}.json"

    def _iter_records(self) -> list[TrialRecord]:
        records: list[TrialRecord] = []
        for path in sorted(self.registry_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.append(TrialRecord.from_dict(payload))
        return records

    def _validate_stage(self, stage: str) -> None:
        if stage not in VALID_STAGES:
            raise ValueError(f"Invalid trial stage: {stage!r}. expected one of {sorted(VALID_STAGES)}")

    def _validate_status(self, status: str) -> None:
        if status not in VALID_STATUS:
            raise ValueError(f"Invalid trial status: {status!r}. expected one of {sorted(VALID_STATUS)}")

    def _validate_reject_reason(self, reason: str | None) -> None:
        if reason is None:
            return
        if reason not in VALID_REJECT_REASONS:
            raise ValueError(
                f"Invalid reject reason: {reason!r}. expected one of {sorted(VALID_REJECT_REASONS)}"
            )

    def _save(self, record: TrialRecord) -> TrialRecord:
        self._validate_stage(record.stage)
        self._validate_status(record.status)
        self._validate_reject_reason(record.reject_reason)
        self._path(record.trial_id).write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return record

    def create(self, record: TrialRecord) -> TrialRecord:
        trial_id = record.trial_id or str(uuid4())
        if self._path(trial_id).exists():
            raise FileExistsError(f"trial already exists: {trial_id}")
        persisted = TrialRecord(
            trial_id=trial_id,
            strategy_name=record.strategy_name,
            strategy_version=record.strategy_version,
            source_spec_path=record.source_spec_path,
            parent_trial_id=record.parent_trial_id,
            family_id=record.family_id,
            stage=record.stage,
            status=record.status,
            reject_reason=record.reject_reason,
            metadata=dict(record.metadata or {}),
        )
        return self._save(persisted)

    def get(self, trial_id: str) -> TrialRecord | None:
        path = self._path(trial_id)
        if not path.exists():
            return None
        return TrialRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def update_stage(self, trial_id: str, stage: str, **metadata: Any) -> TrialRecord:
        record = self.get(trial_id)
        if record is None:
            raise FileNotFoundError(f"trial not found: {trial_id}")
        self._validate_stage(stage)
        record.stage = stage
        record.metadata.update(metadata)
        if record.status != "REJECTED":
            record.status = "ACTIVE"
            record.reject_reason = None
        return self._save(record)

    def reject(self, trial_id: str, reason: str, **metadata: Any) -> TrialRecord:
        record = self.get(trial_id)
        if record is None:
            raise FileNotFoundError(f"trial not found: {trial_id}")
        self._validate_reject_reason(reason)
        record.status = "REJECTED"
        record.reject_reason = reason
        record.metadata.update(metadata)
        return self._save(record)

    def attach_family(self, trial_id: str, family_id: str, **metadata: Any) -> TrialRecord:
        if not family_id:
            raise ValueError("family_id must be non-empty")
        record = self.get(trial_id)
        if record is None:
            raise FileNotFoundError(f"trial not found: {trial_id}")
        record.family_id = family_id
        record.metadata.update(metadata)
        return self._save(record)

    def list_all(self) -> list[TrialRecord]:
        return self._iter_records()

    def list_active(self) -> list[TrialRecord]:
        return [record for record in self._iter_records() if record.status == "ACTIVE"]

    def list_by_family(self, family_id: str) -> list[TrialRecord]:
        return [record for record in self._iter_records() if record.family_id == family_id]
