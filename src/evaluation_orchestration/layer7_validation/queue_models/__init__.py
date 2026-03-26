"""
queue_models — Explicit queue-position model interfaces.

Each model implements the QueueModel protocol, which FillSimulator uses
to gate passive fills and (optionally) cap fill allocation.

Model taxonomy:
  Gate-only   — NoneQueue, PriceTime, RiskAdverse, ProbQueue, RandomQueue
  Gate + cap  — ProRata
"""
from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel
from evaluation_orchestration.layer7_validation.queue_models.none import NoneQueue
from evaluation_orchestration.layer7_validation.queue_models.price_time import PriceTimeQueue
from evaluation_orchestration.layer7_validation.queue_models.risk_adverse import RiskAdverseQueue
from evaluation_orchestration.layer7_validation.queue_models.prob_queue import ProbQueueQueue
from evaluation_orchestration.layer7_validation.queue_models.random_queue import RandomQueueQueue
from evaluation_orchestration.layer7_validation.queue_models.pro_rata import ProRataQueue

QUEUE_MODEL_REGISTRY: dict[str, type[QueueModel]] = {
    "none": NoneQueue,
    "price_time": PriceTimeQueue,
    "risk_adverse": RiskAdverseQueue,
    "prob_queue": ProbQueueQueue,
    "random": RandomQueueQueue,
    "pro_rata": ProRataQueue,
}


def build_queue_model(
    name: str,
    queue_position_assumption: float = 0.5,
    rng_seed: int | None = None,
) -> QueueModel:
    """Factory: instantiate a QueueModel by config name."""
    cls = QUEUE_MODEL_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown queue model '{name}'. "
            f"Available: {sorted(QUEUE_MODEL_REGISTRY)}"
        )
    return cls(
        queue_position_assumption=queue_position_assumption,
        rng_seed=rng_seed,
    )


__all__ = [
    "QueueModel",
    "NoneQueue",
    "PriceTimeQueue",
    "RiskAdverseQueue",
    "ProbQueueQueue",
    "RandomQueueQueue",
    "ProRataQueue",
    "QUEUE_MODEL_REGISTRY",
    "build_queue_model",
]
