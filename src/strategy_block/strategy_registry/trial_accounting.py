"""Deterministic trial-accounting snapshots over the file-backed registry."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .trial_registry import TrialRecord


@dataclass
class TrialAccountingSnapshot:
    total_trials: int
    active_trials: int
    rejected_trials: int
    family_trial_counts: dict[str, int] = field(default_factory=dict)
    family_active_counts: dict[str, int] = field(default_factory=dict)
    stage_counts: dict[str, int] = field(default_factory=dict)
    reject_reason_counts: dict[str, int] = field(default_factory=dict)


class TrialAccounting:
    """Build compact, deterministic accounting views from trial records."""

    def build_snapshot(self, records: list[TrialRecord]) -> TrialAccountingSnapshot:
        normalized = [record for record in records if isinstance(record, TrialRecord)]

        family_trial_counts = Counter()
        family_active_counts = Counter()
        stage_counts = Counter()
        reject_reason_counts = Counter()
        active_trials = 0
        rejected_trials = 0

        for record in normalized:
            family_id = self._family_key(record.family_id)
            if family_id is not None:
                family_trial_counts[family_id] += 1
                if record.status == "ACTIVE":
                    family_active_counts[family_id] += 1

            stage = str(record.stage or "").strip()
            if stage:
                stage_counts[stage] += 1

            reject_reason = str(record.reject_reason or "").strip()
            if reject_reason:
                reject_reason_counts[reject_reason] += 1

            if record.status == "ACTIVE":
                active_trials += 1
            elif record.status == "REJECTED":
                rejected_trials += 1

        return TrialAccountingSnapshot(
            total_trials=len(normalized),
            active_trials=active_trials,
            rejected_trials=rejected_trials,
            family_trial_counts=self._sorted_counts(family_trial_counts),
            family_active_counts=self._sorted_counts(family_active_counts),
            stage_counts=self._sorted_counts(stage_counts),
            reject_reason_counts=self._sorted_counts(reject_reason_counts),
        )

    def family_count(self, records: list[TrialRecord], family_id: str | None) -> int:
        target = self._family_key(family_id)
        if target is None:
            return 0
        return sum(1 for record in records if self._family_key(record.family_id) == target)

    def active_family_count(self, records: list[TrialRecord], family_id: str | None) -> int:
        target = self._family_key(family_id)
        if target is None:
            return 0
        return sum(
            1
            for record in records
            if record.status == "ACTIVE" and self._family_key(record.family_id) == target
        )

    def _family_key(self, family_id: str | None) -> str | None:
        value = str(family_id or "").strip()
        return value or None

    def _sorted_counts(self, counter: Counter[str]) -> dict[str, int]:
        return {key: int(counter[key]) for key in sorted(counter)}
