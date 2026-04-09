"""
tests/test_loop_e2e_fake.py
----------------------------
End-to-end tests for run_spec_centric() with an injected FakeLLMClient — v2.3.

These tests verify LoopRunner behavior using a deterministic stub client.
No OpenAI API calls are made.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from strategy_loop.date_ranges import DateRanges
from strategy_loop.loop_runner import IterationRecord, LoopResult, LoopRunner
from tests.fakes.fake_llm_client import FakeLLMClient


def _passing_summary() -> dict[str, Any]:
    return {
        "signal_count": 15, "n_states": 1000, "n_fills": 15,
        "avg_holding_period": 22.0, "net_pnl": 400.0,
        "total_realized_pnl": 600.0, "total_unrealized_pnl": 0.0,
        "total_commission": 100.0, "total_slippage": 60.0, "total_impact": 40.0,
    }


def _no_trade_summary() -> dict[str, Any]:
    return {
        "signal_count": 0, "n_states": 1000, "n_fills": 0,
        "avg_holding_period": 0.0, "net_pnl": 0.0,
        "total_realized_pnl": 0.0, "total_unrealized_pnl": 0.0,
        "total_commission": 0.0, "total_slippage": 0.0, "total_impact": 0.0,
    }


def _make_runner(tmp_path: Path, *, optimize_n_trials: int = 0) -> LoopRunner:
    return LoopRunner(
        client=FakeLLMClient(),
        memory_dir=tmp_path / "memory",
        output_dir=tmp_path / "outputs",
        optimize_n_trials=optimize_n_trials,
    )


# ── E2E: single plan, single code attempt → pass ─────────────────────────────

class TestE2ESinglePlanPass:
    def test_returns_loop_result(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=2, max_code_attempts=2,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        assert isinstance(result, LoopResult)

    def test_verdict_pass(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=2, max_code_attempts=2,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        assert result.verdict == "pass"

    def test_best_run_id_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        assert result.best_run_id is not None

    def test_iterations_contain_iteration_record(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        assert len(result.iterations) >= 1
        assert isinstance(result.iterations[0], IterationRecord)

    def test_iteration_record_has_feedback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        rec = result.iterations[0]
        assert rec.feedback is not None
        assert "verdict" in rec.feedback


# ── E2E: artifact persistence ─────────────────────────────────────────────────

class TestE2EArtifactPersistence:
    def test_strategy_file_saved(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        assert len(list((tmp_path / "memory" / "strategies").glob("*.json"))) >= 1

    def test_plan_file_saved(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        assert len(list((tmp_path / "memory" / "plans").glob("*.json"))) >= 1

    def test_plan_outcome_updated_to_pass(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        record = json.loads(
            list((tmp_path / "memory" / "plans").glob("*.json"))[0].read_text()
        )
        assert record["outcome"] == "pass"


# ── E2E: no pass scenario ─────────────────────────────────────────────────────

class TestE2ENoPass:
    def test_all_fail_verdict_no_pass(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_no_trade_summary(), []))
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        assert result.verdict == "no_pass"

    def test_plan_outcome_no_code_pass(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_no_trade_summary(), []))
        _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum", max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"),
        )
        record = json.loads(
            list((tmp_path / "memory" / "plans").glob("*.json"))[0].read_text()
        )
        assert record["outcome"] == "no_code_pass"


# ── E2E: existing run() regression ───────────────────────────────────────────

class TestExistingRunUnchanged:
    def test_run_still_works(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_passing_summary(), []))
        result = _make_runner(tmp_path).run(
            research_goal="imbalance momentum", n_iterations=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        assert result.verdict == "pass"
