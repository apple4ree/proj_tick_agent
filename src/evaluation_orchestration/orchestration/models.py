"""
orchestration/models.py
-----------------------
Job model and enums for the file-based orchestration layer.
"""
from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JobType(str, enum.Enum):
    GENERATE_STRATEGY = "generate_strategy"
    REVIEW_STRATEGY = "review_strategy"
    SINGLE_BACKTEST = "single_backtest"
    UNIVERSE_BACKTEST = "universe_backtest"
    WALK_FORWARD_EVALUATION = "walk_forward_evaluation"
    SUMMARIZE_RESULTS = "summarize_results"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid status transitions.
VALID_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


@dataclass
class Job:
    """A single orchestration job.

    Attributes
    ----------
    job_id : str
        Unique identifier (UUID4 by default).
    job_type : JobType
        What this job does.
    status : JobStatus
        Current lifecycle status.
    created_at : str
        ISO-8601 creation timestamp (UTC).
    updated_at : str
        ISO-8601 last-update timestamp (UTC).
    payload : dict
        Arbitrary parameters for the job handler.
    result_path : str
        Path to result artifact (populated on success).
    error_message : str
        Error description (populated on failure).
    """

    job_id: str = ""
    job_type: JobType = JobType.GENERATE_STRATEGY
    status: JobStatus = JobStatus.QUEUED
    created_at: str = ""
    updated_at: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    result_path: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        if not self.job_id:
            self.job_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if isinstance(self.job_type, str):
            self.job_type = JobType(self.job_type)
        if isinstance(self.status, str):
            self.status = JobStatus(self.status)

    # -- lifecycle ------------------------------------------------------------

    def can_transition_to(self, new_status: JobStatus) -> bool:
        return new_status in VALID_JOB_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: JobStatus) -> None:
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Cannot transition job {self.job_id} from "
                f"{self.status.value!r} to {new_status.value!r}"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["job_type"] = self.job_type.value
        d["status"] = self.status.value
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        d = dict(d)
        if "job_type" in d:
            d["job_type"] = JobType(d["job_type"])
        if "status" in d:
            d["status"] = JobStatus(d["status"])
        return cls(**d)

    @classmethod
    def load(cls, path: str | Path) -> Job:
        path = Path(path)
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
