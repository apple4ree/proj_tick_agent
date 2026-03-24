"""
orchestration/generation_worker.py
-----------------------------------
Worker that polls generate_strategy jobs from the file queue, runs
StrategyGenerator, and persists results to the registry.

The generator enforces the static review hard gate internally: a returned
spec is guaranteed to have passed review. If review fails (or fallback policy
blocks degraded generation), the worker marks the job as **failed** — no
invalid spec enters the registry in an executable state.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.models import Job, JobType
from strategy_block.strategy_generation.generator import StrategyGenerator
from strategy_block.strategy_registry.registry import StrategyRegistry
from strategy_block.strategy_registry.models import StrategyStatus

logger = logging.getLogger(__name__)


class GenerationWorker:
    """Processes ``generate_strategy`` jobs from a :class:`FileQueue`."""

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

    def run_once(self) -> Job | None:
        """Dequeue and process a single ``generate_strategy`` job."""
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

    def _trace_flags(self, trace: dict[str, Any]) -> dict[str, Any]:
        fallback = dict(trace.get("fallback") or {})
        events = list(fallback.get("events") or [])
        provenance = dict(trace.get("provenance") or {})
        return {
            "generation_outcome": trace.get("generation_outcome", "unknown"),
            "static_review_passed": bool(trace.get("static_review_passed", False)),
            "fallback_used": bool(trace.get("fallback_used", False) or fallback.get("used", False)),
            "fallback_count": int(fallback.get("count", len(events))),
            "fallback_events": events,
            "generation_class": provenance.get("generation_class", "unknown"),
            "requested_backend": provenance.get("requested_backend", ""),
            "effective_backend": provenance.get("effective_backend", ""),
            "requested_mode": provenance.get("requested_mode", ""),
            "effective_mode": provenance.get("effective_mode", ""),
            "spec_format": provenance.get("spec_format", ""),
        }

    def _process(self, job: Job) -> Job:
        """Execute a single generation job end-to-end."""
        payload = job.payload
        try:
            spec, trace = self._generate(payload)

            trace_path = self._save_trace(spec.name, spec.version, trace)

            spec_path = self.registry.save_spec(
                spec,
                generation_backend=payload.get("backend", "template"),
                generation_mode=payload.get("mode", "live"),
                trace_path=str(trace_path),
                extra={"generation": self._trace_flags(trace)},
            )

            meta = self.registry.get_metadata(spec.name, spec.version)
            meta.static_review_passed = True
            meta.save(self.registry._meta_path(spec.name, spec.version))

            self.registry.update_status(
                spec.name, spec.version, StrategyStatus.REVIEWED,
            )
            if payload.get("auto_approve", False):
                self.registry.update_status(
                    spec.name, spec.version, StrategyStatus.APPROVED,
                )

            self.queue.mark_succeeded(job.job_id, result_path=str(spec_path))
            logger.info(
                "Job %s succeeded: %s v%s (outcome=%s fallback=%s)",
                job.job_id,
                spec.name,
                spec.version,
                trace.get("generation_outcome", "success"),
                trace.get("fallback_used", False),
            )
            return self.queue.load_job(job.job_id)

        except Exception as exc:
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
            spec_format=payload.get("spec_format", "v2"),
            allow_template_fallback=payload.get("allow_template_fallback", True),
            allow_heuristic_fallback=payload.get("allow_heuristic_fallback", True),
            fail_on_fallback=payload.get("fail_on_fallback", False),
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
