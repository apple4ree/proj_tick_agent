"""
orchestration/generation_worker.py
-----------------------------------
Worker that polls generate_strategy jobs from the file queue, runs
StrategyGenerator, and persists results to the registry.

The generator enforces the static review hard gate internally: a returned
spec is guaranteed to have passed review.  If review fails (and template
fallback also fails), the generator raises ``StaticReviewError`` and the
worker marks the job as **failed** — no invalid spec enters the registry
in an executable state.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.models import Job, JobType, JobStatus
from strategy_block.strategy_generation.generator import StrategyGenerator, StaticReviewError
from strategy_block.strategy_registry.registry import StrategyRegistry
from strategy_block.strategy_registry.models import StrategyStatus

logger = logging.getLogger(__name__)


class GenerationWorker:
    """Processes ``generate_strategy`` jobs from a :class:`FileQueue`.

    Parameters
    ----------
    queue : FileQueue
        Job queue to poll.
    registry : StrategyRegistry
        Where to persist generated specs and metadata.
    trace_dir : str | Path
        Directory for generation trace JSON files.
    """

    def __init__(
        self,
        queue: FileQueue,
        registry: StrategyRegistry,
        trace_dir: str | Path = "outputs/strategy_traces",
    ) -> None:
        self.queue = queue
        self.registry = registry
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    # -- public API -----------------------------------------------------------

    def run_once(self) -> Job | None:
        """Dequeue and process a single ``generate_strategy`` job.

        Returns the finished :class:`Job` (succeeded or failed), or ``None``
        if the queue was empty.
        """
        job = self.queue.dequeue(job_type=JobType.GENERATE_STRATEGY)
        if job is None:
            return None
        return self._process(job)

    def run_loop(self, poll_interval: float = 5.0) -> None:
        """Poll the queue continuously until interrupted."""
        logger.info("Generation worker started (poll_interval=%.1fs)", poll_interval)
        try:
            while True:
                job = self.run_once()
                if job is None:
                    time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Generation worker stopped")

    # -- internal -------------------------------------------------------------

    def _process(self, job: Job) -> Job:
        """Execute a single generation job end-to-end."""
        payload = job.payload
        try:
            # Generator enforces static review hard gate:
            # returns only review-passed specs, raises on failure.
            spec, trace = self._generate(payload)

            # Persist trace
            trace_path = self._save_trace(spec.name, spec.version, trace)

            # Persist spec + metadata to registry
            spec_path = self.registry.save_spec(
                spec,
                generation_backend=payload.get("backend", "template"),
                generation_mode=payload.get("mode", "live"),
                trace_path=str(trace_path),
            )

            # Mark review passed (guaranteed by generator)
            meta = self.registry.get_metadata(spec.name, spec.version)
            meta.static_review_passed = True
            meta.save(self.registry._meta_path(spec.name, spec.version))

            # Status transition: DRAFT → REVIEWED (→ APPROVED if auto)
            self.registry.update_status(
                spec.name, spec.version, StrategyStatus.REVIEWED,
            )
            if payload.get("auto_approve", False):
                self.registry.update_status(
                    spec.name, spec.version, StrategyStatus.APPROVED,
                )

            self.queue.mark_succeeded(job.job_id, result_path=str(spec_path))
            logger.info(
                "Job %s succeeded: %s v%s (outcome=%s)",
                job.job_id,
                spec.name,
                spec.version,
                trace.get("generation_outcome", "success"),
            )
            return self.queue.load_job(job.job_id)

        except Exception as exc:
            # Save trace from StaticReviewError for audit
            trace = getattr(exc, "trace", None)
            if trace:
                self._save_trace("_failed", job.job_id, trace)

            logger.exception("Job %s failed: %s", job.job_id, exc)
            self.queue.mark_failed(job.job_id, error_message=str(exc))
            return self.queue.load_job(job.job_id)

    def _generate(self, payload: dict[str, Any]) -> tuple:
        """Instantiate generator and run generation from job payload."""
        generator = StrategyGenerator(
            latency_ms=payload.get("latency_ms", 1.0),
            backend=payload.get("backend", "template"),
            mode=payload.get("mode", "live"),
            replay_path=payload.get("replay_path"),
        )
        return generator.generate(
            research_goal=payload.get("research_goal", ""),
            n_ideas=payload.get("n_ideas", 3),
            idea_index=payload.get("idea_index", 0),
        )

    def _save_trace(
        self,
        name: str,
        version: str,
        trace: dict[str, Any],
    ) -> Path:
        """Save the generation trace to disk."""
        trace_path = self.trace_dir / f"{name}_v{version}_trace.json"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(trace, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return trace_path
