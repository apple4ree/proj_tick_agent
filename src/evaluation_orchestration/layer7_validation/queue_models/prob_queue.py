"""prob_queue.py — Trade + partial depth-drop credit."""
from __future__ import annotations

from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel


class ProbQueueQueue(QueueModel):
    """Probabilistic queue model.

    Trade volume advances the queue (common to all models).
    Unexplained depth drops additionally advance the queue by a fraction
    weighted by ``(1 - queue_position_assumption)``.
    """

    def advance_depth(self, unexplained_depth_drop: float) -> float:
        if unexplained_depth_drop <= 0.0:
            return 0.0
        return unexplained_depth_drop * (1.0 - self._queue_position_assumption)
