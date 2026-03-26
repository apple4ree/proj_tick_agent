"""random_queue.py — Trade + stochastic depth-drop credit."""
from __future__ import annotations

import numpy as np

from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel


class RandomQueueQueue(QueueModel):
    """Random queue model.

    Trade volume advances the queue (common to all models).
    Unexplained depth drops advance the queue by a uniformly random
    fraction ∈ [0, 1), deterministic under ``rng_seed``.
    """

    def __init__(
        self,
        queue_position_assumption: float = 0.5,
        rng_seed: int | None = None,
    ) -> None:
        super().__init__(queue_position_assumption, rng_seed)
        self._rng = np.random.default_rng(rng_seed)

    def advance_depth(self, unexplained_depth_drop: float) -> float:
        if unexplained_depth_drop <= 0.0:
            return 0.0
        fraction = float(self._rng.random())
        return unexplained_depth_drop * fraction
