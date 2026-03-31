"""File-based family index for duplicate/neighbor candidate lookup."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .family_fingerprint import FamilyFingerprint, fingerprint_similarity


@dataclass
class FamilyEntry:
    family_id: str
    representative_trial_id: str
    member_trial_ids: list[str] = field(default_factory=list)
    fingerprint: FamilyFingerprint = field(default_factory=lambda: FamilyFingerprint(
        family_id="",
        motif="generic",
        side_model="unknown",
        execution_style="unknown",
        horizon_bucket="unknown",
        regime_shape="r0+stateless+risk_static+exec_static",
        feature_signature="none",
        raw_signature="{}",
    ))
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = asdict(self.fingerprint)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FamilyEntry":
        fingerprint_payload = payload.get("fingerprint", {})
        fingerprint = FamilyFingerprint(**fingerprint_payload)
        return cls(
            family_id=payload["family_id"],
            representative_trial_id=payload["representative_trial_id"],
            member_trial_ids=list(payload.get("member_trial_ids", [])),
            fingerprint=fingerprint,
            tags=list(payload.get("tags", [])),
            metadata=dict(payload.get("metadata", {})),
        )


class FamilyIndex:
    def __init__(self, storage_dir: str | Path = "outputs/trials/family_index") -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, family_id: str) -> Path:
        return self.storage_dir / f"{family_id}.json"

    def _save(self, entry: FamilyEntry) -> FamilyEntry:
        self._path(entry.family_id).write_text(
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return entry

    def _iter_entries(self) -> list[FamilyEntry]:
        entries: list[FamilyEntry] = []
        for path in sorted(self.storage_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            entries.append(FamilyEntry.from_dict(payload))
        return entries

    def upsert(
        self,
        fingerprint: FamilyFingerprint,
        trial_id: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FamilyEntry:
        if not trial_id:
            raise ValueError("trial_id must be non-empty")

        existing = self.get(fingerprint.family_id)
        if existing is None:
            entry = FamilyEntry(
                family_id=fingerprint.family_id,
                representative_trial_id=trial_id,
                member_trial_ids=[trial_id],
                fingerprint=fingerprint,
                tags=sorted(set(tags or [])),
                metadata=dict(metadata or {}),
            )
            return self._save(entry)

        if trial_id not in existing.member_trial_ids:
            existing.member_trial_ids.append(trial_id)
        existing.tags = sorted(set(existing.tags).union(tags or []))
        if metadata:
            existing.metadata.update(metadata)
        return self._save(existing)

    def get(self, family_id: str) -> FamilyEntry | None:
        path = self._path(family_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FamilyEntry.from_dict(payload)

    def list_members(self, family_id: str) -> list[str]:
        entry = self.get(family_id)
        if entry is None:
            return []
        return list(entry.member_trial_ids)

    def find_duplicate_or_neighbor(
        self,
        fingerprint: FamilyFingerprint,
        *,
        duplicate_threshold: float = 0.95,
        neighbor_threshold: float = 0.75,
    ) -> dict[str, Any] | None:
        best_duplicate: tuple[float, FamilyEntry] | None = None
        best_neighbor: tuple[float, FamilyEntry] | None = None

        for entry in self._iter_entries():
            similarity = fingerprint_similarity(fingerprint, entry.fingerprint)
            candidate = (similarity, entry)
            if similarity >= duplicate_threshold:
                if self._is_better(candidate, best_duplicate):
                    best_duplicate = candidate
            elif similarity >= neighbor_threshold:
                if self._is_better(candidate, best_neighbor):
                    best_neighbor = candidate

        if best_duplicate is not None:
            return self._match_dict("duplicate", best_duplicate[0], best_duplicate[1])
        if best_neighbor is not None:
            return self._match_dict("neighbor", best_neighbor[0], best_neighbor[1])
        return None

    def _is_better(
        self,
        candidate: tuple[float, FamilyEntry],
        current: tuple[float, FamilyEntry] | None,
    ) -> bool:
        if current is None:
            return True
        cand_score, cand_entry = candidate
        cur_score, cur_entry = current
        if cand_score > cur_score:
            return True
        if cand_score < cur_score:
            return False
        return cand_entry.family_id < cur_entry.family_id

    def _match_dict(self, match_type: str, similarity: float, entry: FamilyEntry) -> dict[str, Any]:
        return {
            "match_type": match_type,
            "family_id": entry.family_id,
            "similarity": round(similarity, 6),
            "representative_trial_id": entry.representative_trial_id,
            "member_trial_ids": list(entry.member_trial_ids),
            "member_count": len(entry.member_trial_ids),
            "tags": list(entry.tags),
        }
