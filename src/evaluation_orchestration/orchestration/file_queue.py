"""
orchestration/file_queue.py
---------------------------
File-based job queue with atomic-move state transitions.

Race safety
~~~~~~~~~~~
Every state transition uses ``os.rename()`` as the single commit point.
On POSIX, ``os.rename()`` within the same filesystem is atomic — if two
workers race to claim the same queued file, exactly one ``rename()``
succeeds and the other raises ``OSError`` / ``FileNotFoundError``.
The loser skips to the next candidate.  This guarantees a job cannot be
dequeued twice.

Directory layout::

    <queue_dir>/
        queued/      ← new jobs land here
        running/     ← picked up by a worker
        succeeded/   ← completed successfully
        failed/      ← completed with error
        cancelled/   ← cancelled before or during execution
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from .models import Job, JobStatus, JobType

logger = logging.getLogger(__name__)

# Map each status to its sub-directory name.
_STATUS_DIR: dict[JobStatus, str] = {
    JobStatus.QUEUED: "queued",
    JobStatus.RUNNING: "running",
    JobStatus.SUCCEEDED: "succeeded",
    JobStatus.FAILED: "failed",
    JobStatus.CANCELLED: "cancelled",
}


class FileQueue:
    """File-based job queue using atomic directory moves.

    Parameters
    ----------
    queue_dir : str | Path
        Root directory for the queue.  Sub-directories for each status are
        created automatically.
    """

    def __init__(self, queue_dir: str | Path = "jobs/") -> None:
        self.queue_dir = Path(queue_dir)
        for subdir in _STATUS_DIR.values():
            (self.queue_dir / subdir).mkdir(parents=True, exist_ok=True)

    # -- helpers --------------------------------------------------------------

    def _dir_for(self, status: JobStatus) -> Path:
        return self.queue_dir / _STATUS_DIR[status]

    def _job_path(self, job_id: str, status: JobStatus) -> Path:
        return self._dir_for(status) / f"{job_id}.json"

    def _find_job_path(self, job_id: str) -> Path | None:
        """Search all status dirs for a job file."""
        for status in JobStatus:
            p = self._job_path(job_id, status)
            if p.exists():
                return p
        return None

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write *content* to *path* atomically via temp-file + rename."""
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        os.rename(tmp, path)

    def _transition(
        self,
        job_id: str,
        from_status: JobStatus,
        to_status: JobStatus,
        updater=None,
    ) -> Job:
        """Move a job between status dirs via atomic rename.

        1. ``os.rename(src, dst)`` — atomic claim; exactly one caller wins.
        2. Read job from *dst*, update status + optional fields.
        3. Atomic write-back so readers never see partial content.

        Parameters
        ----------
        updater : callable(Job) -> None, optional
            Mutate extra fields on the Job before write-back.
        """
        src = self._job_path(job_id, from_status)
        dst = self._job_path(job_id, to_status)
        os.rename(src, dst)  # atomic; raises OSError if src is gone
        job = Job.load(dst)
        job.transition_to(to_status)
        if updater:
            updater(job)
        self._atomic_write(dst, job.to_json())
        return job

    # -- public API -----------------------------------------------------------

    def enqueue(self, job: Job) -> Path:
        """Add a job to the queue.  Returns the path of the queued file."""
        job.status = JobStatus.QUEUED
        dst = self._job_path(job.job_id, JobStatus.QUEUED)
        if dst.exists():
            raise FileExistsError(f"Job {job.job_id} already queued")
        self._atomic_write(dst, job.to_json())
        logger.info("Enqueued job %s (%s)", job.job_id, job.job_type.value)
        return dst

    def dequeue(self, job_type: JobType | None = None) -> Job | None:
        """Claim the oldest queued job (optionally filtered by type).

        Race-safe: ``os.rename(queued/<id>.json, running/<id>.json)`` is
        the sole commit point.  If another worker renames the file first,
        our rename raises ``OSError`` and we skip to the next candidate.
        A job can therefore never be dequeued by two workers.
        """
        queued_dir = self._dir_for(JobStatus.QUEUED)
        for candidate in sorted(queued_dir.glob("*.json")):
            try:
                job = Job.load(candidate)
            except Exception:
                continue
            if job_type is not None and job.job_type != job_type:
                continue

            # Atomic claim: rename queued → running.
            dst = self._job_path(job.job_id, JobStatus.RUNNING)
            try:
                os.rename(candidate, dst)
            except OSError:
                # Another worker already claimed this file.
                continue

            # We own this job.  Update status and write back atomically.
            job.transition_to(JobStatus.RUNNING)
            self._atomic_write(dst, job.to_json())
            logger.info("Dequeued job %s -> running", job.job_id)
            return job
        return None

    def mark_running(self, job_id: str) -> Job:
        """Explicitly move a queued job to running status."""
        try:
            return self._transition(job_id, JobStatus.QUEUED, JobStatus.RUNNING)
        except FileNotFoundError:
            raise FileNotFoundError(f"Queued job not found: {job_id}")

    def mark_succeeded(self, job_id: str, result_path: str = "") -> Job:
        """Move a running job to succeeded."""
        def updater(job: Job) -> None:
            job.result_path = result_path
        try:
            return self._transition(
                job_id, JobStatus.RUNNING, JobStatus.SUCCEEDED, updater,
            )
        except FileNotFoundError:
            raise FileNotFoundError(f"Running job not found: {job_id}")

    def mark_failed(self, job_id: str, error_message: str = "") -> Job:
        """Move a running job to failed."""
        def updater(job: Job) -> None:
            job.error_message = error_message
        try:
            return self._transition(
                job_id, JobStatus.RUNNING, JobStatus.FAILED, updater,
            )
        except FileNotFoundError:
            raise FileNotFoundError(f"Running job not found: {job_id}")

    def cancel(self, job_id: str) -> Job:
        """Cancel a queued or running job."""
        for from_status in (JobStatus.QUEUED, JobStatus.RUNNING):
            try:
                return self._transition(
                    job_id, from_status, JobStatus.CANCELLED,
                )
            except (FileNotFoundError, OSError):
                continue
        raise FileNotFoundError(f"Active job not found: {job_id}")

    # -- queries --------------------------------------------------------------

    def load_job(self, job_id: str) -> Job:
        """Load a job by ID from any status directory."""
        p = self._find_job_path(job_id)
        if p is None:
            raise FileNotFoundError(f"Job not found: {job_id}")
        return Job.load(p)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: JobType | None = None,
    ) -> list[Job]:
        """List jobs, optionally filtered by status and/or type."""
        dirs = [self._dir_for(status)] if status else [
            self._dir_for(s) for s in JobStatus
        ]
        results: list[Job] = []
        for d in dirs:
            for p in sorted(d.glob("*.json")):
                try:
                    job = Job.load(p)
                except Exception:
                    continue
                if job_type is not None and job.job_type != job_type:
                    continue
                results.append(job)
        return results
