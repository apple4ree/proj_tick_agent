"""
orchestration/backtest_worker.py
---------------------------------
Worker that polls single_backtest / universe_backtest jobs from the file
queue, validates the strategy via the registry execution gate, runs the
backtest, and records results.

The execution plane never uses ``latest`` — every job must carry an explicit
``version``.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.models import Job, JobType, JobStatus
from strategy_block.strategy_registry.registry import StrategyRegistry
from strategy_block.strategy_compiler import compile_strategy

logger = logging.getLogger(__name__)

_BACKTEST_JOB_TYPES = {JobType.SINGLE_BACKTEST, JobType.UNIVERSE_BACKTEST}


class BacktestWorker:
    """Processes ``single_backtest`` and ``universe_backtest`` jobs.

    Parameters
    ----------
    queue : FileQueue
        Job queue to poll.
    registry : StrategyRegistry
        Strategy registry (execution gate source).
    output_dir : str | Path
        Root directory for backtest result artifacts.
    data_dir : str | Path
        H0STASP0 data root.
    """

    def __init__(
        self,
        queue: FileQueue,
        registry: StrategyRegistry,
        output_dir: str | Path = "outputs/backtests",
        data_dir: str | Path = "/home/dgu/tick/open-trading-api/data/realtime/H0STASP0",
    ) -> None:
        self.queue = queue
        self.registry = registry
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = str(data_dir)

    # -- public API -----------------------------------------------------------

    def run_once(self) -> Job | None:
        """Dequeue and process a single backtest job.

        Tries ``single_backtest`` first, then ``universe_backtest``.
        Returns the finished Job, or ``None`` if the queue was empty.
        """
        for jtype in (JobType.SINGLE_BACKTEST, JobType.UNIVERSE_BACKTEST):
            job = self.queue.dequeue(job_type=jtype)
            if job is not None:
                return self._process(job)
        return None

    def run_loop(self, poll_interval: float = 5.0) -> None:
        """Poll the queue continuously until interrupted."""
        logger.info("Backtest worker started (poll=%.1fs)", poll_interval)
        try:
            while True:
                job = self.run_once()
                if job is None:
                    time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Backtest worker stopped")

    # -- internal -------------------------------------------------------------

    def _process(self, job: Job) -> Job:
        """Execute a single backtest job end-to-end."""
        payload = job.payload
        try:
            # 1) Validate payload
            name, version = self._extract_strategy_ref(payload)

            # 2) Execution gate
            self.registry.check_execution_gate(name, version)

            # 3) Load spec (version-pinned, gate-checked)
            spec = self.registry.load_spec_for_execution(name, version)

            # 4) Compile (v2-only)
            def strategy_factory():
                return compile_strategy(spec)

            # 5) Dispatch
            run_id = uuid.uuid4().hex[:12]
            if job.job_type == JobType.SINGLE_BACKTEST:
                result_path = self._run_single(
                    job, spec, strategy_factory, run_id, payload,
                )
            else:
                result_path = self._run_universe(
                    job, spec, strategy_factory, run_id, payload,
                )

            # 6) Save job-level result metadata
            meta_path = self._save_result_meta(run_id, job, name, version, result_path)

            self.queue.mark_succeeded(job.job_id, result_path=str(result_path))
            logger.info("Job %s succeeded: %s", job.job_id, result_path)
            return self.queue.load_job(job.job_id)

        except PermissionError as exc:
            logger.warning("Job %s gate-blocked: %s", job.job_id, exc)
            self.queue.mark_failed(job.job_id, error_message=str(exc))
            return self.queue.load_job(job.job_id)

        except Exception as exc:
            logger.exception("Job %s failed: %s", job.job_id, exc)
            self.queue.mark_failed(job.job_id, error_message=str(exc))
            return self.queue.load_job(job.job_id)

    def _extract_strategy_ref(self, payload: dict) -> tuple[str, str]:
        """Extract and validate (name, version) from payload."""
        name = payload.get("strategy_name")
        version = payload.get("version")
        if not name:
            raise ValueError("payload missing 'strategy_name'")
        if not version:
            raise ValueError(
                "payload missing 'version' — execution plane requires "
                "explicit version (no implicit latest)"
            )
        return name, version

    # -- single backtest ------------------------------------------------------

    def _run_single(
        self,
        job: Job,
        spec: Any,
        strategy_factory: Any,
        run_id: str,
        payload: dict,
    ) -> Path:
        """Run a single-symbol backtest via the backtest module."""
        from scripts.backtest import (
            build_states_for_range,
            run_backtest_with_states,
        )
        from evaluation_orchestration.layer7_validation import BacktestConfig

        symbol = payload.get("symbol", "005930")
        start_date = payload.get("start_date", "2026-03-13")
        end_date = payload.get("end_date", start_date)
        data_dir = payload.get("data_dir", self.data_dir)
        out = payload.get("output_dir", str(self.output_dir))

        config = BacktestConfig(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_cash=payload.get("initial_cash", 1e8),
            seed=payload.get("seed", 42),
        )

        states = build_states_for_range(
            data_dir=data_dir,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        strategy = strategy_factory()
        result = run_backtest_with_states(
            config=config,
            states=states,
            data_dir=data_dir,
            output_dir=out,
            strategy=strategy,
        )

        summary = result.summary()
        result_dir = Path(out) / run_id
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=float), encoding="utf-8",
        )
        return result_dir

    # -- universe backtest ----------------------------------------------------

    def _run_universe(
        self,
        job: Job,
        spec: Any,
        strategy_factory: Any,
        run_id: str,
        payload: dict,
    ) -> Path:
        """Run a universe backtest via the universe module."""
        from scripts.backtest_strategy_universe import (
            discover_symbols,
            build_states,
            run_single_backtest,
        )

        data_dir = payload.get("data_dir", self.data_dir)
        start_date = payload.get("start_date", "2026-03-13")
        end_date = payload.get("end_date", start_date)
        out = Path(payload.get("output_dir", str(self.output_dir))) / run_id
        out.mkdir(parents=True, exist_ok=True)

        symbols = discover_symbols(data_dir, start_date, end_date)
        latencies = payload.get("latencies", [0.0, 50.0, 100.0])

        all_results: list[dict] = []
        failures: list[dict] = []

        for symbol in symbols:
            try:
                states = build_states(
                    data_dir, symbol, start_date, end_date, "1s",
                )
            except Exception as exc:
                for lat in latencies:
                    failures.append({
                        "symbol": symbol, "latency_ms": lat,
                        "error": str(exc),
                    })
                continue

            for lat in latencies:
                rr = run_single_backtest(
                    strategy_cls=strategy_factory,
                    symbol=symbol,
                    states=states,
                    data_dir=data_dir,
                    latency_ms=lat,
                    initial_cash=payload.get("initial_cash", 1e8),
                    seed=payload.get("seed", 42),
                    compute_attribution=True,
                    start_date=start_date,
                    end_date=end_date,
                    summary_only=True,
                )
                if rr.ok:
                    all_results.append(rr.summary)
                else:
                    failures.append({
                        "symbol": symbol, "latency_ms": lat,
                        "error": rr.error,
                    })

        (out / "universe_results.json").write_text(
            json.dumps(all_results, indent=2, default=float, ensure_ascii=False),
            encoding="utf-8",
        )
        if failures:
            (out / "failed_runs.json").write_text(
                json.dumps(failures, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return out

    # -- result metadata ------------------------------------------------------

    def _save_result_meta(
        self,
        run_id: str,
        job: Job,
        strategy_name: str,
        version: str,
        result_path: Path,
    ) -> Path:
        """Write a small result-metadata JSON alongside the results."""
        meta = {
            "run_id": run_id,
            "job_id": job.job_id,
            "job_type": job.job_type.value,
            "strategy_name": strategy_name,
            "version": version,
            "result_path": str(result_path),
        }
        meta_path = result_path / "run_meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        return meta_path
