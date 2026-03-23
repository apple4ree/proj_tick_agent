"""
tests/test_generation_worker.py
-------------------------------
Tests for GenerationWorker: job processing, registry integration,
review-based status, and failure handling.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.generation_worker import GenerationWorker
from evaluation_orchestration.orchestration.models import Job, JobType, JobStatus
from strategy_block.strategy_registry.registry import StrategyRegistry
from strategy_block.strategy_registry.models import StrategyStatus
from strategy_block.strategy_specs.schema import (
    StrategySpec, SignalRule, PositionRule, ExitRule, FilterRule,
)
from strategy_block.strategy_generation.generator import StaticReviewError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_spec(name: str = "test_strat", version: str = "1.0") -> StrategySpec:
    """A spec that will pass static review."""
    return StrategySpec(
        name=name,
        version=version,
        description="unit-test",
        signal_rules=[
            SignalRule(feature="order_imbalance", operator=">",
                       threshold=0.3, score_contribution=1.0),
            SignalRule(feature="order_imbalance", operator="<",
                       threshold=-0.3, score_contribution=-1.0),
        ],
        filters=[
            FilterRule(feature="spread_bps", operator=">",
                       threshold=5.0, action="block"),
        ],
        position_rule=PositionRule(
            max_position=100, sizing_mode="fixed", fixed_size=10,
            inventory_cap=100,
        ),
        exit_rules=[
            ExitRule(exit_type="stop_loss", threshold_bps=15.0),
            ExitRule(exit_type="time_exit", timeout_ticks=300),
        ],
    )


def _good_trace() -> dict:
    return {
        "pipeline": "template_generator_v1",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "fallback_used": False,
        "static_review_passed": True,
        "generation_outcome": "success",
        "static_review": {"passed": True, "issues": []},
    }


def _fallback_trace() -> dict:
    return {
        "pipeline": "template_generator_v1",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "fallback_used": True,
        "fallback_reason": "API timeout",
        "original_backend": "openai",
        "static_review_passed": True,
        "generation_outcome": "fallback_success",
        "static_review": {"passed": True, "issues": []},
    }


@pytest.fixture()
def workspace(tmp_path: Path):
    """Create queue, registry, and trace dirs in a temp directory."""
    jobs_dir = tmp_path / "jobs"
    strat_dir = tmp_path / "strategies"
    trace_dir = tmp_path / "traces"
    queue = FileQueue(jobs_dir)
    registry = StrategyRegistry(strat_dir)
    worker = GenerationWorker(queue, registry, trace_dir=trace_dir)
    return queue, registry, worker, trace_dir


# ---------------------------------------------------------------------------
# Happy path: spec passes review
# ---------------------------------------------------------------------------

class TestSuccessfulGeneration:
    def test_job_succeeds_and_spec_saved(self, workspace):
        queue, registry, worker, trace_dir = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "imbalance momentum", "backend": "template"},
        )
        queue.enqueue(job)

        with patch.object(worker, "_generate", return_value=(_valid_spec(), _good_trace())):
            result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.SUCCEEDED
        assert result.result_path  # spec path populated

        # Spec is in registry
        spec = registry.load_spec("test_strat", "1.0")
        assert spec.name == "test_strat"

    def test_metadata_has_review_passed(self, workspace):
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test", "backend": "template"},
        )
        queue.enqueue(job)

        with patch.object(worker, "_generate", return_value=(_valid_spec(), _good_trace())):
            worker.run_once()

        meta = registry.get_metadata("test_strat", "1.0")
        assert meta.static_review_passed is True
        assert meta.status == StrategyStatus.REVIEWED

    def test_auto_approve_sets_approved(self, workspace):
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test", "auto_approve": True},
        )
        queue.enqueue(job)

        with patch.object(worker, "_generate", return_value=(_valid_spec(), _good_trace())):
            worker.run_once()

        meta = registry.get_metadata("test_strat", "1.0")
        assert meta.status == StrategyStatus.APPROVED

    def test_trace_saved(self, workspace):
        queue, registry, worker, trace_dir = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test"},
        )
        queue.enqueue(job)

        with patch.object(worker, "_generate", return_value=(_valid_spec(), _good_trace())):
            worker.run_once()

        trace_files = list(trace_dir.glob("*.json"))
        assert len(trace_files) == 1
        trace = json.loads(trace_files[0].read_text())
        assert trace["static_review_passed"] is True
        assert trace["generation_outcome"] == "success"


# ---------------------------------------------------------------------------
# Static review hard gate: review failure → job FAILED
# ---------------------------------------------------------------------------

class TestStaticReviewHardGate:
    def test_review_failure_marks_job_failed(self, workspace):
        """When generator raises StaticReviewError, job must be FAILED."""
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test"},
        )
        queue.enqueue(job)

        err = StaticReviewError(
            "spec failed static review",
            trace={"generation_outcome": "failed", "static_review_passed": False},
        )
        with patch.object(worker, "_generate", side_effect=err):
            result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.FAILED
        assert "failed static review" in result.error_message

    def test_review_failure_does_not_create_spec(self, workspace):
        """No spec should enter the registry when review fails."""
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test"},
        )
        queue.enqueue(job)

        err = StaticReviewError("review fail", trace={"generation_outcome": "failed"})
        with patch.object(worker, "_generate", side_effect=err):
            worker.run_once()

        assert registry.list_specs() == []

    def test_auto_approve_irrelevant_when_review_fails(self, workspace):
        """auto_approve payload is ignored when generation raises."""
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test", "auto_approve": True},
        )
        queue.enqueue(job)

        err = StaticReviewError("review fail", trace={})
        with patch.object(worker, "_generate", side_effect=err):
            result = worker.run_once()

        assert result.status == JobStatus.FAILED
        assert registry.list_specs() == []

    def test_failed_trace_saved_for_audit(self, workspace):
        """StaticReviewError.trace should be saved to disk for audit."""
        queue, registry, worker, trace_dir = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test"},
        )
        queue.enqueue(job)

        err = StaticReviewError(
            "review fail",
            trace={
                "generation_outcome": "failed",
                "static_review_passed": False,
                "fallback_used": True,
            },
        )
        with patch.object(worker, "_generate", side_effect=err):
            worker.run_once()

        trace_files = list(trace_dir.glob("*_failed_*.json"))
        assert len(trace_files) == 1
        trace = json.loads(trace_files[0].read_text())
        assert trace["generation_outcome"] == "failed"
        assert trace["static_review_passed"] is False
        assert trace["fallback_used"] is True


# ---------------------------------------------------------------------------
# Failed job: generation itself throws
# ---------------------------------------------------------------------------

class TestFailedJob:
    def test_generation_exception_marks_failed(self, workspace):
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test"},
        )
        queue.enqueue(job)

        with patch.object(
            worker, "_generate", side_effect=RuntimeError("GPU OOM")
        ):
            result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.FAILED
        assert "GPU OOM" in result.error_message

    def test_failed_job_does_not_create_spec(self, workspace):
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test"},
        )
        queue.enqueue(job)

        with patch.object(
            worker, "_generate", side_effect=ValueError("bad payload")
        ):
            worker.run_once()

        # Registry should be empty
        assert registry.list_specs() == []


# ---------------------------------------------------------------------------
# Empty queue
# ---------------------------------------------------------------------------

class TestEmptyQueue:
    def test_run_once_empty(self, workspace):
        _, _, worker, _ = workspace
        assert worker.run_once() is None


# ---------------------------------------------------------------------------
# Fallback trace recording
# ---------------------------------------------------------------------------

class TestFallbackTrace:
    def test_fallback_fields_in_trace(self, workspace):
        queue, registry, worker, trace_dir = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={"research_goal": "test", "backend": "openai", "mode": "mock"},
        )
        queue.enqueue(job)

        with patch.object(
            worker, "_generate", return_value=(_valid_spec(), _fallback_trace())
        ):
            worker.run_once()

        trace_files = list(trace_dir.glob("*.json"))
        trace = json.loads(trace_files[0].read_text())
        assert trace["fallback_used"] is True
        assert trace["fallback_reason"] == "API timeout"
        assert trace["original_backend"] == "openai"


# ---------------------------------------------------------------------------
# Payload fields forwarded correctly
# ---------------------------------------------------------------------------

class TestPayloadForwarding:
    def test_generate_receives_payload(self, workspace):
        queue, _, worker, _ = workspace

        job = Job(
            job_type=JobType.GENERATE_STRATEGY,
            payload={
                "research_goal": "spread reversion",
                "backend": "openai",
                "mode": "mock",
                "latency_ms": 2.5,
                "n_ideas": 5,
                "idea_index": 2,
            },
        )
        queue.enqueue(job)

        with patch(
            "evaluation_orchestration.orchestration.generation_worker.StrategyGenerator"
        ) as MockGen:
            instance = MockGen.return_value
            instance.generate.return_value = (_valid_spec(), _good_trace())
            worker.run_once()

            MockGen.assert_called_once_with(
                latency_ms=2.5,
                backend="openai",
                mode="mock",
                replay_path=None,
            )
            instance.generate.assert_called_once_with(
                research_goal="spread reversion",
                n_ideas=5,
                idea_index=2,
            )
