"""price_time.py — Strict FIFO conservative; trade-only advancement."""
from __future__ import annotations

from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel


class PriceTimeQueue(QueueModel):
    """Strict price-time priority.

    Only trade volume at the queue price advances the queue.
    Unexplained depth drops are ignored (conservative).
    """

    def advance_depth(self, unexplained_depth_drop: float) -> float:
        return 0.0
