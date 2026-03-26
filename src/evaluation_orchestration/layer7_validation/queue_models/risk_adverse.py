"""risk_adverse.py — Trade-only advancement (same as price_time)."""
from __future__ import annotations

from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel


class RiskAdverseQueue(QueueModel):
    """Risk-adverse queue model.

    Identical gate logic to price_time: only trade volume advances the queue.
    Depth drops are not credited.
    """

    def advance_depth(self, unexplained_depth_drop: float) -> float:
        return 0.0
