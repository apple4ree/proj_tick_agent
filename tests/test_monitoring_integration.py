"""
tests/test_monitoring_integration.py
--------------------------------------
Integration tests for the monitoring module (Phase 5).
Tests attach_to_pipeline + run_all_verifiers + export_monitoring_run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from execution_planning.layer1_signal import Signal
from monitoring import MonitorConfig, attach_to_pipeline
from monitoring.verifiers.batch_verifier import run_all_verifiers
from monitoring.reporters.exporter import export_monitoring_run
from strategy_block.strategy import Strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_states(n: int = 12) -> list[MarketState]:
    start = pd.Timestamp("2026-03-12 09:00:00")
    states = []
    for i in range(n):
        ts  = start + pd.Timedelta(seconds=i)
        lob = LOBSnapshot(
            timestamp  = ts,
            bid_levels = [LOBLevel(100.0, 5000), LOBLevel(99.9, 3000)],
            ask_levels = [LOBLevel(100.1, 1200), LOBLevel(100.2, 800)],
        )
        states.append(MarketState(timestamp=ts, symbol="TEST", lob=lob,
                                  tradable=True, session="regular"))
    return states


class _OnceBuyStrategy(Strategy):
    def __init__(self) -> None:
        self._fired = False

    @property
    def name(self) -> str:
        return "OnceBuy"

    def reset(self) -> None:
        self._fired = False

    def generate_signal(self, state: MarketState):
        if self._fired:
            return None
        self._fired = True
        return Signal(
            timestamp      = state.timestamp,
            symbol         = state.symbol,
            score          = 0.9,
            expected_return= 5.0,
            confidence     = 0.9,
            horizon_steps  = 1,
            tags           = {},
            is_valid       = True,
        )


def _make_runner(placement: str = "aggressive") -> PipelineRunner:
    config = BacktestConfig(
        symbol         = "TEST",
        start_date     = "2026-03-12",
        end_date       = "2026-03-12",
        seed           = 42,
        placement_style= placement,
    )
    return PipelineRunner(
        config   = config,
        data_dir = ".",
        strategy = _OnceBuyStrategy(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_attach_to_pipeline_smoke():
    """attach_to_pipeline → run → run_all_verifiers end-to-end."""
    runner = _make_runner("aggressive")
    runner = attach_to_pipeline(runner)
    result = runner.run(_make_states(12))

    assert len(runner.bus) > 0, "EventBus should not be empty"
    summary = runner.bus.summary()
    assert "TickStartEvent" in summary, f"Missing TickStartEvent in {summary}"
    assert summary["TickStartEvent"] == 12

    report = run_all_verifiers(runner.bus)
    assert report.fee_pass_rate == 1.0,      f"fee failures: {report.fee_failures}"
    assert report.slippage_pass_rate == 1.0, f"slippage failures: {report.slippage_failures}"
    assert report.latency_pass_rate == 1.0,  f"latency failures: {report.latency_failures}"


def test_attach_to_pipeline_verbose_captures_queue_ticks():
    """With verbose=True and passive placement, QueueTickEvents are collected."""
    runner = _make_runner("passive")
    mc     = MonitorConfig(verbose=True)
    runner = attach_to_pipeline(runner, mc)
    runner.run(_make_states(12))

    summary = runner.bus.summary()
    assert "QueueInitEvent" in summary,  f"Missing QueueInitEvent: {summary}"
    assert "QueueTickEvent" in summary,  f"Missing QueueTickEvent: {summary}"


def test_export_monitoring_run_creates_files():
    """export_monitoring_run creates all required output files."""
    runner = _make_runner("aggressive")
    runner = attach_to_pipeline(runner)
    result = runner.run(_make_states(12))
    report = run_all_verifiers(runner.bus)

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        paths = export_monitoring_run(runner.bus, report, tmp_path, result.run_id)

        run_dir = tmp_path / result.run_id
        assert (run_dir / "fills.csv").exists(),        "fills.csv missing"
        assert (run_dir / "verification.json").exists(),"verification.json missing"
        assert (run_dir / "fill_report.json").exists(), "fill_report.json missing"
        assert (run_dir / "queue_report.json").exists(),"queue_report.json missing"
        assert (run_dir / "order_submits.csv").exists(),"order_submits.csv missing"

        ver = json.loads((run_dir / "verification.json").read_text())
        assert "fee" in ver
        assert "slippage" in ver
        assert "latency" in ver
        assert ver["fee"]["pass_rate"] == 1.0
        assert ver["latency"]["pass_rate"] == 1.0


def test_attach_to_pipeline_does_not_change_backtest_result():
    """InstrumentedPipelineRunner must produce the same n_fills as plain runner."""
    states  = _make_states(12)
    config  = BacktestConfig(symbol="TEST", start_date="2026-03-12",
                             end_date="2026-03-12", seed=42,
                             placement_style="aggressive")

    plain = PipelineRunner(config=config, data_dir=".", strategy=_OnceBuyStrategy())
    plain_result = plain.run(states)

    instrumented = PipelineRunner(config=config, data_dir=".", strategy=_OnceBuyStrategy())
    instrumented = attach_to_pipeline(instrumented)
    instr_result = instrumented.run(states)

    assert instr_result.n_fills == plain_result.n_fills, (
        f"fill count changed: plain={plain_result.n_fills} instrumented={instr_result.n_fills}"
    )
