"""
tests/test_backtest_worker.py
------------------------------
Tests for BacktestWorker: execution gate enforcement, version-pinned
loading, job status transitions, and single/universe dispatch.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.backtest_worker import BacktestWorker
from evaluation_orchestration.orchestration.models import Job, JobType, JobStatus
from strategy_block.strategy_registry.registry import StrategyRegistry
from strategy_block.strategy_registry.models import StrategyStatus
from strategy_block.strategy_specs.schema import (
    StrategySpec, SignalRule, PositionRule, ExitRule, FilterRule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_spec(name: str = "alpha", version: str = "1.0") -> StrategySpec:
    return StrategySpec(
        name=name,
        version=version,
        description="test",
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


def _register_and_approve(
    registry: StrategyRegistry,
    name: str = "alpha",
    version: str = "1.0",
    *,
    promote: bool = False,
) -> None:
    """Save a spec, pass it through review → approved (→ promoted)."""
    spec = _valid_spec(name, version)
    registry.save_spec(spec)

    # Mark review passed
    meta = registry.get_metadata(name, version)
    meta.static_review_passed = True
    meta.save(registry._meta_path(name, version))

    registry.update_status(name, version, StrategyStatus.REVIEWED)
    registry.update_status(name, version, StrategyStatus.APPROVED)
    if promote:
        registry.promote_for_backtest(name, version)


@pytest.fixture()
def workspace(tmp_path: Path):
    jobs_dir = tmp_path / "jobs"
    strat_dir = tmp_path / "strategies"
    out_dir = tmp_path / "outputs"
    queue = FileQueue(jobs_dir)
    registry = StrategyRegistry(strat_dir)
    worker = BacktestWorker(queue, registry, output_dir=out_dir, data_dir="/fake")
    return queue, registry, worker, out_dir


# ---------------------------------------------------------------------------
# Execution gate: approved spec only
# ---------------------------------------------------------------------------

class TestExecutionGate:
    def test_approved_spec_passes_gate(self, workspace):
        queue, registry, worker, _ = workspace
        _register_and_approve(registry)

        meta = registry.check_execution_gate("alpha", "1.0")
        assert meta.status == StrategyStatus.APPROVED

    def test_promoted_spec_passes_gate(self, workspace):
        _, registry, _, _ = workspace
        _register_and_approve(registry, promote=True)

        meta = registry.check_execution_gate("alpha", "1.0")
        assert meta.status == StrategyStatus.PROMOTED_TO_BACKTEST

    def test_draft_spec_blocked(self, workspace):
        _, registry, _, _ = workspace
        spec = _valid_spec()
        registry.save_spec(spec)
        # Still DRAFT, review not passed
        with pytest.raises(PermissionError, match="not passed static review"):
            registry.check_execution_gate("alpha", "1.0")

    def test_rejected_spec_blocked(self, workspace):
        _, registry, _, _ = workspace
        spec = _valid_spec()
        registry.save_spec(spec)
        meta = registry.get_metadata("alpha", "1.0")
        meta.static_review_passed = True
        meta.save(registry._meta_path("alpha", "1.0"))
        registry.update_status("alpha", "1.0", StrategyStatus.REJECTED)

        with pytest.raises(PermissionError, match="status is 'rejected'"):
            registry.check_execution_gate("alpha", "1.0")

    def test_reviewed_but_not_approved_blocked(self, workspace):
        _, registry, _, _ = workspace
        spec = _valid_spec()
        registry.save_spec(spec)
        meta = registry.get_metadata("alpha", "1.0")
        meta.static_review_passed = True
        meta.save(registry._meta_path("alpha", "1.0"))
        registry.update_status("alpha", "1.0", StrategyStatus.REVIEWED)

        with pytest.raises(PermissionError, match="status is 'reviewed'"):
            registry.check_execution_gate("alpha", "1.0")

    def test_review_not_passed_blocks_even_if_approved(self, workspace):
        """Edge case: status forced to approved but review flag is False."""
        _, registry, _, _ = workspace
        spec = _valid_spec()
        registry.save_spec(spec)
        # Force status to REVIEWED then APPROVED without setting review flag
        registry.update_status("alpha", "1.0", StrategyStatus.REVIEWED)
        registry.update_status("alpha", "1.0", StrategyStatus.APPROVED)

        with pytest.raises(PermissionError, match="not passed static review"):
            registry.check_execution_gate("alpha", "1.0")


# ---------------------------------------------------------------------------
# Version-pinned loading
# ---------------------------------------------------------------------------

class TestVersionPinned:
    def test_missing_version_in_payload_fails(self, workspace):
        queue, registry, worker, _ = workspace
        _register_and_approve(registry)

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={"strategy_name": "alpha"},  # no version!
        )
        queue.enqueue(job)
        result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.FAILED
        assert "missing 'version'" in result.error_message

    def test_missing_name_in_payload_fails(self, workspace):
        queue, registry, worker, _ = workspace

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={"version": "1.0"},  # no name!
        )
        queue.enqueue(job)
        result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.FAILED
        assert "missing 'strategy_name'" in result.error_message

    def test_nonexistent_version_fails(self, workspace):
        queue, registry, worker, _ = workspace
        _register_and_approve(registry)

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={"strategy_name": "alpha", "version": "9.9"},
        )
        queue.enqueue(job)
        result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# Single backtest job
# ---------------------------------------------------------------------------

class TestSingleBacktest:
    def test_single_backtest_succeeds(self, workspace):
        queue, registry, worker, out_dir = workspace
        _register_and_approve(registry)

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={
                "strategy_name": "alpha",
                "version": "1.0",
                "symbol": "005930",
                "start_date": "2026-03-13",
            },
        )
        queue.enqueue(job)

        # Mock the actual backtest execution (needs real data)
        mock_result = MagicMock()
        mock_result.summary.return_value = {"pnl": 1000.0, "sharpe": 1.5}
        mock_result.run_id = "mock_run"

        with patch(
            "evaluation_orchestration.orchestration.backtest_worker.BacktestWorker._run_single",
        ) as mock_run:
            mock_run.return_value = out_dir / "result_001"
            (out_dir / "result_001").mkdir(parents=True)
            result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.SUCCEEDED

    def test_rejected_spec_fails_job(self, workspace):
        queue, registry, worker, _ = workspace
        spec = _valid_spec()
        registry.save_spec(spec)
        meta = registry.get_metadata("alpha", "1.0")
        meta.static_review_passed = True
        meta.save(registry._meta_path("alpha", "1.0"))
        registry.update_status("alpha", "1.0", StrategyStatus.REJECTED)

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={"strategy_name": "alpha", "version": "1.0"},
        )
        queue.enqueue(job)
        result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.FAILED
        assert "rejected" in result.error_message


# ---------------------------------------------------------------------------
# Universe backtest job
# ---------------------------------------------------------------------------

class TestUniverseBacktest:
    def test_universe_backtest_succeeds(self, workspace):
        queue, registry, worker, out_dir = workspace
        _register_and_approve(registry)

        job = Job(
            job_type=JobType.UNIVERSE_BACKTEST,
            payload={
                "strategy_name": "alpha",
                "version": "1.0",
                "start_date": "2026-03-13",
            },
        )
        queue.enqueue(job)

        with patch(
            "evaluation_orchestration.orchestration.backtest_worker.BacktestWorker._run_universe",
        ) as mock_run:
            mock_run.return_value = out_dir / "univ_001"
            (out_dir / "univ_001").mkdir(parents=True)
            result = worker.run_once()

        assert result is not None
        assert result.status == JobStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Job status transitions
# ---------------------------------------------------------------------------

class TestJobStatus:
    def test_succeeded_job_has_result_path(self, workspace):
        queue, registry, worker, out_dir = workspace
        _register_and_approve(registry)

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={"strategy_name": "alpha", "version": "1.0"},
        )
        queue.enqueue(job)

        with patch(
            "evaluation_orchestration.orchestration.backtest_worker.BacktestWorker._run_single",
        ) as mock_run:
            rpath = out_dir / "r1"
            rpath.mkdir(parents=True)
            mock_run.return_value = rpath
            result = worker.run_once()

        assert result.status == JobStatus.SUCCEEDED
        assert result.result_path  # non-empty

    def test_failed_job_preserves_error(self, workspace):
        queue, registry, worker, _ = workspace
        _register_and_approve(registry)

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={"strategy_name": "alpha", "version": "1.0"},
        )
        queue.enqueue(job)

        with patch(
            "evaluation_orchestration.orchestration.backtest_worker.BacktestWorker._run_single",
            side_effect=RuntimeError("data file missing"),
        ):
            result = worker.run_once()

        assert result.status == JobStatus.FAILED
        assert "data file missing" in result.error_message

    def test_empty_queue_returns_none(self, workspace):
        _, _, worker, _ = workspace
        assert worker.run_once() is None


# ---------------------------------------------------------------------------
# Result metadata saved
# ---------------------------------------------------------------------------

class TestResultMeta:
    def test_run_meta_json_saved(self, workspace):
        queue, registry, worker, out_dir = workspace
        _register_and_approve(registry)

        job = Job(
            job_type=JobType.SINGLE_BACKTEST,
            payload={"strategy_name": "alpha", "version": "1.0"},
        )
        queue.enqueue(job)

        with patch(
            "evaluation_orchestration.orchestration.backtest_worker.BacktestWorker._run_single",
        ) as mock_run:
            rpath = out_dir / "r_meta"
            rpath.mkdir(parents=True)
            mock_run.return_value = rpath
            worker.run_once()

        meta_file = rpath / "run_meta.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta["strategy_name"] == "alpha"
        assert meta["version"] == "1.0"
        assert meta["job_type"] == "single_backtest"


# ---------------------------------------------------------------------------
# load_spec_for_execution integration
# ---------------------------------------------------------------------------

class TestLoadSpecForExecution:
    def test_approved_loads(self, workspace):
        _, registry, _, _ = workspace
        _register_and_approve(registry)

        spec = registry.load_spec_for_execution("alpha", "1.0")
        assert spec.name == "alpha"

    def test_draft_raises(self, workspace):
        _, registry, _, _ = workspace
        spec = _valid_spec()
        registry.save_spec(spec)

        with pytest.raises(PermissionError):
            registry.load_spec_for_execution("alpha", "1.0")
