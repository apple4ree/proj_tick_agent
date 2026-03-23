"""
strategy_registry/models.py
----------------------------
Metadata model and status lifecycle for strategy registry entries.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StrategyStatus(str, enum.Enum):
    """Status lifecycle for a strategy in the registry."""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROMOTED_TO_BACKTEST = "promoted_to_backtest"
    PROMOTED_TO_LIVE = "promoted_to_live"
    ARCHIVED = "archived"


# Valid status transitions: current -> set of allowed next statuses.
VALID_TRANSITIONS: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.DRAFT: {
        StrategyStatus.REVIEWED,
        StrategyStatus.REJECTED,
        StrategyStatus.ARCHIVED,
    },
    StrategyStatus.REVIEWED: {
        StrategyStatus.APPROVED,
        StrategyStatus.REJECTED,
        StrategyStatus.ARCHIVED,
    },
    StrategyStatus.APPROVED: {
        StrategyStatus.PROMOTED_TO_BACKTEST,
        StrategyStatus.REJECTED,
        StrategyStatus.ARCHIVED,
    },
    StrategyStatus.REJECTED: {
        StrategyStatus.ARCHIVED,
    },
    StrategyStatus.PROMOTED_TO_BACKTEST: {
        StrategyStatus.PROMOTED_TO_LIVE,
        StrategyStatus.REJECTED,
        StrategyStatus.ARCHIVED,
    },
    StrategyStatus.PROMOTED_TO_LIVE: {
        StrategyStatus.ARCHIVED,
    },
    StrategyStatus.ARCHIVED: set(),
}


@dataclass
class StrategyMetadata:
    """Metadata record stored alongside each strategy spec.

    Attributes
    ----------
    strategy_id : str
        Unique identifier ``<name>_v<version>``.
    name : str
        Human-readable strategy name.
    version : str
        Semantic version string.
    status : StrategyStatus
        Current lifecycle status.
    created_at : str
        ISO-8601 creation timestamp.
    generation_backend : str
        Which LLM / pipeline backend generated the spec (e.g. ``"gpt-4o"``,
        ``"claude-opus-4-6"``).
    generation_mode : str
        Generation mode: ``"multi_agent"``, ``"template"``, ``"manual"``, etc.
    static_review_passed : bool
        Whether static review validation has passed.
    approved_for_backtest : bool
        Whether this version is approved for backtesting.
    approved_for_live : bool
        Whether this version is approved for live trading.
    spec_path : str
        Relative path to the spec JSON file.
    trace_path : str
        Relative path to the generation trace file (may be empty).
    extra : dict
        Arbitrary extra fields.
    """

    strategy_id: str
    name: str
    version: str
    status: StrategyStatus = StrategyStatus.DRAFT
    created_at: str = ""
    generation_backend: str = ""
    generation_mode: str = ""
    static_review_passed: bool = False
    approved_for_backtest: bool = False
    approved_for_live: bool = False
    spec_format: str = "v1"  # "v1" or "v2"
    spec_path: str = ""
    trace_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if isinstance(self.status, str):
            self.status = StrategyStatus(self.status)

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StrategyMetadata:
        d = dict(d)  # shallow copy
        if "status" in d:
            d["status"] = StrategyStatus(d["status"])
        return cls(**d)

    @classmethod
    def load(cls, path: str | Path) -> StrategyMetadata:
        path = Path(path)
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # -- lifecycle helpers ----------------------------------------------------

    def can_transition_to(self, new_status: StrategyStatus) -> bool:
        return new_status in VALID_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: StrategyStatus) -> None:
        """Transition to *new_status*, raising on illegal transitions."""
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Cannot transition from {self.status.value!r} to {new_status.value!r}. "
                f"Allowed: {sorted(s.value for s in VALID_TRANSITIONS.get(self.status, set()))}"
            )
        self.status = new_status
