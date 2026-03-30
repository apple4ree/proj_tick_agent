"""Generation worker tests for environment context forwarding."""
from __future__ import annotations

from pathlib import Path

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.generation_worker import GenerationWorker
from strategy_block.strategy_registry.registry import StrategyRegistry


def _make_worker(tmp_path: Path) -> GenerationWorker:
    queue = FileQueue(tmp_path / "jobs")
    registry = StrategyRegistry(tmp_path / "strategies")
    return GenerationWorker(queue, registry, trace_dir=tmp_path / "traces")


def test_generate_forwards_backtest_environment_to_generator(tmp_path: Path, monkeypatch) -> None:
    worker = _make_worker(tmp_path)
    captured: dict = {}

    class _DummyGenerator:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def generate(self, **kwargs):
            captured["generate_kwargs"] = dict(kwargs)
            return "SPEC", {"ok": True}

    monkeypatch.setattr(
        "evaluation_orchestration.orchestration.generation_worker.StrategyGenerator",
        _DummyGenerator,
    )

    env = {
        "resample": "500ms",
        "canonical_tick_interval_ms": 500.0,
        "latency": {"order_submit_ms": 0.3, "order_ack_ms": 0.7, "cancel_ms": 0.2},
        "queue": {"queue_model": "riskaverse", "queue_position_assumption": "end"},
        "semantics": {"replace_model": "minimal_immediate"},
    }
    payload = {
        "research_goal": "env-aware review",
        "n_ideas": 3,
        "idea_index": 1,
        "latency_ms": 1.0,
        "backtest_environment": env,
        "backend": "template",
        "mode": "mock",
    }

    spec, trace = worker._generate(payload)
    assert spec == "SPEC"
    assert trace == {"ok": True}
    assert captured["init_kwargs"]["backtest_environment"] == env
    assert captured["generate_kwargs"]["research_goal"] == "env-aware review"
    assert captured["generate_kwargs"]["n_ideas"] == 3
    assert captured["generate_kwargs"]["idea_index"] == 1


def test_generate_passes_none_backtest_environment_when_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _make_worker(tmp_path)
    captured: dict = {}

    class _DummyGenerator:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def generate(self, **kwargs):
            return "SPEC", {"ok": True}

    monkeypatch.setattr(
        "evaluation_orchestration.orchestration.generation_worker.StrategyGenerator",
        _DummyGenerator,
    )

    payload = {
        "research_goal": "no env payload",
        "latency_ms": 1.0,
    }
    worker._generate(payload)

    assert "backtest_environment" in captured["init_kwargs"]
    assert captured["init_kwargs"]["backtest_environment"] is None
