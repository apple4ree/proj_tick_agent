"""
orchestration/manager.py
------------------------
High-level orchestration manager that wraps FileQueue with convenience
methods for common job flows (generate → review → backtest → summarize).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .file_queue import FileQueue
from .models import Job, JobType, JobStatus

logger = logging.getLogger(__name__)


class OrchestrationManager:
    """Convenience layer over :class:`FileQueue`.

    Parameters
    ----------
    queue_dir : str | Path
        Root directory for the job queue.
    """

    def __init__(self, queue_dir: str | Path = "jobs/") -> None:
        self.queue = FileQueue(queue_dir)

    # -- job creation helpers -------------------------------------------------

    def submit_generation(self, payload: dict[str, Any]) -> Job:
        """Submit a strategy-generation job."""
        job = Job(job_type=JobType.GENERATE_STRATEGY, payload=payload)
        self.queue.enqueue(job)
        return job

    def submit_review(self, strategy_name: str, version: str) -> Job:
        """Submit a strategy-review job."""
        job = Job(
            job_type=JobType.REVIEW_STRATEGY,
            payload={"strategy_name": strategy_name, "version": version},
        )
        self.queue.enqueue(job)
        return job

    def submit_backtest(
        self,
        strategy_name: str,
        version: str,
        *,
        universe: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> Job:
        """Submit a single or universe backtest job."""
        jtype = JobType.UNIVERSE_BACKTEST if universe else JobType.SINGLE_BACKTEST
        payload: dict[str, Any] = {
            "strategy_name": strategy_name,
            "version": version,
        }
        if extra:
            payload.update(extra)
        job = Job(job_type=jtype, payload=payload)
        self.queue.enqueue(job)
        return job

    def submit_summarize(self, result_paths: list[str]) -> Job:
        """Submit a result-summarization job."""
        job = Job(
            job_type=JobType.SUMMARIZE_RESULTS,
            payload={"result_paths": result_paths},
        )
        self.queue.enqueue(job)
        return job

    # -- delegation -----------------------------------------------------------

    def dequeue(self, job_type: JobType | None = None) -> Job | None:
        return self.queue.dequeue(job_type)

    def mark_succeeded(self, job_id: str, result_path: str = "") -> Job:
        return self.queue.mark_succeeded(job_id, result_path)

    def mark_failed(self, job_id: str, error_message: str = "") -> Job:
        return self.queue.mark_failed(job_id, error_message)

    def cancel(self, job_id: str) -> Job:
        return self.queue.cancel(job_id)

    def load_job(self, job_id: str) -> Job:
        return self.queue.load_job(job_id)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: JobType | None = None,
    ) -> list[Job]:
        return self.queue.list_jobs(status=status, job_type=job_type)
