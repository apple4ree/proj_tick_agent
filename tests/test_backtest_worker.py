"""Backtest worker tests (v2-only)."""
from __future__ import annotations

import json
from pathlib import Path

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.backtest_worker import BacktestWorker
from evaluation_orchestration.orchestration.models import Job, JobType, JobStatus
from strategy_block.strategy_registry.registry import StrategyRegistry
from strategy_block.strategy_registry.models import StrategyStatus
from strategy_block.strategy_specs.v2.schema_v2 import (
    StrategySpecV2, EntryPolicyV2, ExitPolicyV2, ExitRuleV2, ExitActionV2, RiskPolicyV2,
)
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr


def _valid_spec(name: str = "alpha", version: str = "2.0") -> StrategySpecV2:
    return StrategySpecV2(
        name=name,
        version=version,
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.3),
                strength=ConstExpr(value=0.6),
            )
        ],
        exit_policies=[
            ExitPolicyV2(
                name="default_exit",
                rules=[
                    ExitRuleV2(
                        name="exit_on_reverse",
                        priority=1,
                        condition=ComparisonExpr(feature="order_imbalance", op="<", threshold=-0.2),
                        action=ExitActionV2(type="close_all"),
                    )
                ],
            )
        ],
        risk_policy=RiskPolicyV2(max_position=500, inventory_cap=500),
    )


def test_worker_missing_version_fails(tmp_path):
    queue = FileQueue(tmp_path / "jobs")
    registry = StrategyRegistry(tmp_path / "strategies")
    worker = BacktestWorker(queue, registry, output_dir=tmp_path / "out", data_dir=tmp_path / "data")

    job = Job(job_type=JobType.SINGLE_BACKTEST, payload={"strategy_name": "alpha"})
    queue.enqueue(job)

    result = worker.run_once()
    assert result is not None
    assert result.status == JobStatus.FAILED
    assert "missing 'version'" in (result.error_message or "")


def test_worker_gate_blocks_unapproved_strategy(tmp_path):
    queue = FileQueue(tmp_path / "jobs")
    registry = StrategyRegistry(tmp_path / "strategies")
    worker = BacktestWorker(queue, registry, output_dir=tmp_path / "out", data_dir=tmp_path / "data")

    registry.save_spec(_valid_spec(name="alpha", version="2.0"))

    job = Job(
        job_type=JobType.SINGLE_BACKTEST,
        payload={"strategy_name": "alpha", "version": "2.0", "symbol": "005930", "start_date": "20260313"},
    )
    queue.enqueue(job)

    result = worker.run_once()
    assert result is not None
    assert result.status == JobStatus.FAILED
    assert "has not passed static review" in (result.error_message or "")


def test_worker_runs_single_backtest_when_gate_passes(tmp_path, monkeypatch):
    queue = FileQueue(tmp_path / "jobs")
    registry = StrategyRegistry(tmp_path / "strategies")
    worker = BacktestWorker(queue, registry, output_dir=tmp_path / "out", data_dir=tmp_path / "data")

    registry.save_spec(_valid_spec(name="alpha", version="2.0"))
    registry.update_status("alpha", "2.0", StrategyStatus.REVIEWED)
    meta = registry.get_metadata("alpha", "2.0")
    meta.static_review_passed = True
    meta.save(tmp_path / "strategies" / "alpha_v2.0.meta.json")
    registry.update_status("alpha", "2.0", StrategyStatus.APPROVED)

    def _fake_run_single(*args, **kwargs):
        out = Path(kwargs.get("payload", {}).get("output_dir", tmp_path / "out"))
        run = out / "fake_run"
        run.mkdir(parents=True, exist_ok=True)
        (run / "summary.json").write_text(json.dumps({"n_states": 1}), encoding="utf-8")
        return run

    monkeypatch.setattr(worker, "_run_single", _fake_run_single)

    job = Job(
        job_type=JobType.SINGLE_BACKTEST,
        payload={"strategy_name": "alpha", "version": "2.0", "symbol": "005930", "start_date": "20260313"},
    )
    queue.enqueue(job)

    result = worker.run_once()
    assert result is not None
    assert result.status == JobStatus.SUCCEEDED
    assert result.result_path
