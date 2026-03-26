"""none.py — No queue gate; immediate fill eligibility."""
from __future__ import annotations

from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel


class NoneQueue(QueueModel):
    """Queue gate disabled — orders are immediately eligible for matching."""

    def advance_depth(self, unexplained_depth_drop: float) -> float:
        return 0.0
