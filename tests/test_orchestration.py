"""
tests/test_orchestration.py
---------------------------
Tests for file-based orchestration: job model, file queue, and manager.
Includes atomicity / race-safety tests for the FileQueue.
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path

from evaluation_orchestration.orchestration.models import Job, JobType, JobStatus, VALID_JOB_TRANSITIONS
from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.manager import OrchestrationManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def queue(tmp_path: Path) -> FileQueue:
    return FileQueue(tmp_path / "jobs")


@pytest.fixture()
def manager(tmp_path: Path) -> OrchestrationManager:
    return OrchestrationManager(tmp_path / "jobs")


def _make_job(**kw) -> Job:
    defaults = dict(job_type=JobType.GENERATE_STRATEGY, payload={"goal": "test"})
    defaults.update(kw)
    return Job(**defaults)


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

class TestJobModel:
    def test_defaults(self) -> None:
        job = _make_job()
        assert job.job_id  # auto-generated
        assert job.status == JobStatus.QUEUED
        assert job.created_at
        assert job.updated_at

    def test_round_trip(self, tmp_path: Path) -> None:
        job = _make_job()
        p = tmp_path / "j.json"
        job.save(p)
        loaded = Job.load(p)
        assert loaded.job_id == job.job_id
        assert loaded.job_type == JobType.GENERATE_STRATEGY
        assert loaded.payload == {"goal": "test"}

    def test_invalid_transition_raises(self) -> None:
        job = _make_job()
        job.status = JobStatus.SUCCEEDED
        with pytest.raises(ValueError, match="Cannot transition"):
            job.transition_to(JobStatus.RUNNING)

    def test_valid_transition(self) -> None:
        job = _make_job()
        job.transition_to(JobStatus.RUNNING)
        assert job.status == JobStatus.RUNNING
        job.transition_to(JobStatus.SUCCEEDED)
        assert job.status == JobStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Enqueue / dequeue
# ---------------------------------------------------------------------------

class TestEnqueueDequeue:
    def test_enqueue_creates_file(self, queue: FileQueue) -> None:
        job = _make_job()
        path = queue.enqueue(job)
        assert path.exists()
        assert "queued" in str(path)

    def test_dequeue_returns_oldest(self, queue: FileQueue) -> None:
        j1 = _make_job(job_id="aaa")
        j2 = _make_job(job_id="bbb")
        queue.enqueue(j1)
        queue.enqueue(j2)

        got = queue.dequeue()
        assert got is not None
        assert got.job_id == "aaa"
        assert got.status == JobStatus.RUNNING

    def test_dequeue_empty_returns_none(self, queue: FileQueue) -> None:
        assert queue.dequeue() is None

    def test_dequeue_with_type_filter(self, queue: FileQueue) -> None:
        queue.enqueue(_make_job(job_id="gen1", job_type=JobType.GENERATE_STRATEGY))
        queue.enqueue(_make_job(job_id="bt1", job_type=JobType.SINGLE_BACKTEST))

        got = queue.dequeue(job_type=JobType.SINGLE_BACKTEST)
        assert got is not None
        assert got.job_id == "bt1"

    def test_duplicate_enqueue_raises(self, queue: FileQueue) -> None:
        job = _make_job(job_id="dup1")
        queue.enqueue(job)
        with pytest.raises(FileExistsError):
            queue.enqueue(_make_job(job_id="dup1"))


# ---------------------------------------------------------------------------
# Atomic dequeue guarantees
# ---------------------------------------------------------------------------

class TestDequeueAtomicity:
    def test_dequeue_removes_queued_and_creates_running(self, queue: FileQueue) -> None:
        """After dequeue, queued file is gone and running file exists."""
        job = _make_job(job_id="mv1")
        queue.enqueue(job)

        queue.dequeue()

        assert not queue._job_path("mv1", JobStatus.QUEUED).exists()
        assert queue._job_path("mv1", JobStatus.RUNNING).exists()

    def test_dequeue_same_job_twice_returns_none(self, queue: FileQueue) -> None:
        """A single job cannot be dequeued twice."""
        job = _make_job(job_id="once1")
        queue.enqueue(job)

        got1 = queue.dequeue()
        assert got1 is not None
        assert got1.job_id == "once1"

        got2 = queue.dequeue()
        assert got2 is None

    def test_dequeue_race_pre_claimed(self, queue: FileQueue) -> None:
        """Simulate another worker claiming the file before us."""
        job = _make_job(job_id="race1")
        queue.enqueue(job)

        # Simulate another worker: manually rename queued → running
        src = queue._job_path("race1", JobStatus.QUEUED)
        dst = queue._job_path("race1", JobStatus.RUNNING)
        os.rename(src, dst)

        # Our dequeue should find nothing claimable
        assert queue.dequeue() is None

    def test_dequeue_skips_claimed_picks_next(self, queue: FileQueue) -> None:
        """When first candidate is pre-claimed, dequeue picks the next."""
        queue.enqueue(_make_job(job_id="aaa"))
        queue.enqueue(_make_job(job_id="bbb"))

        # Simulate another worker claiming "aaa"
        src = queue._job_path("aaa", JobStatus.QUEUED)
        dst = queue._job_path("aaa", JobStatus.RUNNING)
        os.rename(src, dst)

        got = queue.dequeue()
        assert got is not None
        assert got.job_id == "bbb"

    def test_running_file_has_running_status(self, queue: FileQueue) -> None:
        """Content of the running file reflects the updated status."""
        job = _make_job(job_id="status1")
        queue.enqueue(job)

        queue.dequeue()

        loaded = Job.load(queue._job_path("status1", JobStatus.RUNNING))
        assert loaded.status == JobStatus.RUNNING


# ---------------------------------------------------------------------------
# Status transitions via queue
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    def test_mark_running(self, queue: FileQueue) -> None:
        job = _make_job(job_id="run1")
        queue.enqueue(job)
        result = queue.mark_running("run1")
        assert result.status == JobStatus.RUNNING
        # file should be in running dir
        assert queue._job_path("run1", JobStatus.RUNNING).exists()
        # file should NOT be in queued dir
        assert not queue._job_path("run1", JobStatus.QUEUED).exists()

    def test_mark_succeeded(self, queue: FileQueue) -> None:
        job = _make_job(job_id="suc1")
        queue.enqueue(job)
        queue.dequeue()  # moves to running
        result = queue.mark_succeeded("suc1", result_path="/out/result.json")
        assert result.status == JobStatus.SUCCEEDED
        assert result.result_path == "/out/result.json"
        assert queue._job_path("suc1", JobStatus.SUCCEEDED).exists()
        assert not queue._job_path("suc1", JobStatus.RUNNING).exists()

    def test_mark_failed(self, queue: FileQueue) -> None:
        job = _make_job(job_id="fail1")
        queue.enqueue(job)
        queue.dequeue()
        result = queue.mark_failed("fail1", error_message="OOM")
        assert result.status == JobStatus.FAILED
        assert result.error_message == "OOM"
        assert queue._job_path("fail1", JobStatus.FAILED).exists()

    def test_cancel_queued(self, queue: FileQueue) -> None:
        job = _make_job(job_id="can1")
        queue.enqueue(job)
        result = queue.cancel("can1")
        assert result.status == JobStatus.CANCELLED

    def test_cancel_running(self, queue: FileQueue) -> None:
        job = _make_job(job_id="can2")
        queue.enqueue(job)
        queue.dequeue()
        result = queue.cancel("can2")
        assert result.status == JobStatus.CANCELLED

    def test_mark_succeeded_not_running_raises(self, queue: FileQueue) -> None:
        job = _make_job(job_id="bad1")
        queue.enqueue(job)
        with pytest.raises(FileNotFoundError):
            queue.mark_succeeded("bad1")


# ---------------------------------------------------------------------------
# Job persistence & queries
# ---------------------------------------------------------------------------

class TestPersistenceAndQueries:
    def test_load_job_from_any_dir(self, queue: FileQueue) -> None:
        job = _make_job(job_id="load1")
        queue.enqueue(job)
        loaded = queue.load_job("load1")
        assert loaded.job_id == "load1"

        queue.dequeue()
        loaded = queue.load_job("load1")
        assert loaded.status == JobStatus.RUNNING

    def test_load_job_not_found(self, queue: FileQueue) -> None:
        with pytest.raises(FileNotFoundError):
            queue.load_job("nonexistent")

    def test_list_jobs_all(self, queue: FileQueue) -> None:
        queue.enqueue(_make_job(job_id="l1"))
        queue.enqueue(_make_job(job_id="l2"))
        queue.dequeue()  # l1 -> running
        jobs = queue.list_jobs()
        assert len(jobs) == 2

    def test_list_jobs_by_status(self, queue: FileQueue) -> None:
        queue.enqueue(_make_job(job_id="s1"))
        queue.enqueue(_make_job(job_id="s2"))
        queue.dequeue()  # s1 -> running

        queued = queue.list_jobs(status=JobStatus.QUEUED)
        assert len(queued) == 1
        assert queued[0].job_id == "s2"

        running = queue.list_jobs(status=JobStatus.RUNNING)
        assert len(running) == 1
        assert running[0].job_id == "s1"

    def test_list_jobs_by_type(self, queue: FileQueue) -> None:
        queue.enqueue(_make_job(job_id="t1", job_type=JobType.GENERATE_STRATEGY))
        queue.enqueue(_make_job(job_id="t2", job_type=JobType.SINGLE_BACKTEST))

        gen_jobs = queue.list_jobs(job_type=JobType.GENERATE_STRATEGY)
        assert len(gen_jobs) == 1
        assert gen_jobs[0].job_id == "t1"


# ---------------------------------------------------------------------------
# Failed job detail preservation
# ---------------------------------------------------------------------------

class TestFailedJobDetail:
    def test_failed_job_preserves_payload_and_error(self, queue: FileQueue) -> None:
        job = _make_job(
            job_id="fp1",
            job_type=JobType.SINGLE_BACKTEST,
            payload={"strategy_name": "alpha", "version": "1.0"},
        )
        queue.enqueue(job)
        queue.dequeue()
        queue.mark_failed("fp1", error_message="Data file missing")

        loaded = queue.load_job("fp1")
        assert loaded.status == JobStatus.FAILED
        assert loaded.error_message == "Data file missing"
        assert loaded.payload["strategy_name"] == "alpha"
        assert loaded.job_type == JobType.SINGLE_BACKTEST


# ---------------------------------------------------------------------------
# Manager convenience
# ---------------------------------------------------------------------------

class TestManager:
    def test_submit_generation(self, manager: OrchestrationManager) -> None:
        job = manager.submit_generation({"goal": "momentum"})
        assert job.job_type == JobType.GENERATE_STRATEGY
        loaded = manager.load_job(job.job_id)
        assert loaded.status == JobStatus.QUEUED

    def test_submit_backtest_universe(self, manager: OrchestrationManager) -> None:
        job = manager.submit_backtest("strat_a", "1.0", universe=True)
        assert job.job_type == JobType.UNIVERSE_BACKTEST
        assert job.payload["strategy_name"] == "strat_a"

    def test_full_flow(self, manager: OrchestrationManager) -> None:
        job = manager.submit_generation({"goal": "spread"})
        picked = manager.dequeue(JobType.GENERATE_STRATEGY)
        assert picked is not None
        assert picked.job_id == job.job_id
        result = manager.mark_succeeded(job.job_id, "/out/spec.json")
        assert result.status == JobStatus.SUCCEEDED
