"""
tests/test_loop_routing.py
---------------------------
run_spec_centric() routing logic tests — v2.3.

Verifies:
  1. spec_invalid plan → next plan (no code attempts)
  2. precode_eval rejected plan → next plan
  3. IS pass + no OOS → result.verdict="pass"
  4. plan-level memory saved after each plan
  5. max_plan_iterations respected
  6. structural feedback → exits inner loop, requests new plan
  7. parametric feedback → stays in inner loop (same spec, retry code)
  8. normalized_spec (not raw spec) is the implementer input
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from strategy_loop.date_ranges import DateRanges
from strategy_loop.loop_runner import LoopRunner
from tests.fakes.fake_llm_client import FakeLLMClient, FAKE_CODE_RESPONSE


# ── summary helpers ───────────────────────────────────────────────────────────

def _good_summary(**overrides) -> dict[str, Any]:
    base = {
        "signal_count": 20, "n_states": 1000, "n_fills": 20,
        "avg_holding_period": 25, "net_pnl": 500.0,
        "total_realized_pnl": 700.0, "total_unrealized_pnl": 0.0,
        "total_commission": 100.0, "total_slippage": 50.0, "total_impact": 50.0,
    }
    base.update(overrides)
    return base


def _fail_summary(**overrides) -> dict[str, Any]:
    base = {
        "signal_count": 0, "n_states": 1000, "n_fills": 0,
        "avg_holding_period": 0, "net_pnl": 0.0,
        "total_realized_pnl": 0.0, "total_unrealized_pnl": 0.0,
        "total_commission": 0.0, "total_slippage": 0.0, "total_impact": 0.0,
    }
    base.update(overrides)
    return base


def _make_runner(tmp_path: Path, client: Any | None = None) -> LoopRunner:
    if client is None:
        client = FakeLLMClient()
    return LoopRunner(
        client=client,
        memory_dir=tmp_path / "memory",
        output_dir=tmp_path / "outputs",
        optimize_n_trials=0,
    )


# ── test: spec_invalid → skip to next plan ────────────────────────────────────

class TestSpecInvalidRouting:
    def test_invalid_spec_skipped_no_code_attempts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        bad_planner_resp = {
            "strategy_text": "bad spec",
            "strategy_spec": {
                "version": "2.2",
                "archetype": 1,
                "archetype_name": "test",
                "derived_features": [],
                "entry_conditions": [
                    {"source_type": "feature", "source": "COMPLETELY_UNKNOWN_FEATURE_XYZ",
                     "op": ">", "threshold": 0.3}
                ],
                "exit_time_ticks": 20,
                "exit_signal_conditions": [
                    {"source_type": "feature", "source": "order_imbalance",
                     "op": "<", "threshold": -0.05}
                ],
                "tunable_params": [], "features_used": [], "rationale": "bad",
            },
        }
        client = FakeLLMClient(planner_response=bad_planner_resp)
        runner = _make_runner(tmp_path, client=client)
        result = runner.run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=1, max_code_attempts=3,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        assert result.iterations == []


# ── test: precode_eval rejection → skip to next plan ─────────────────────────

class TestPrecodeEvalRejection:
    def test_low_precode_eval_score_skips_code_attempts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        weak_spec_planner = {
            "strategy_text": "empty",
            "strategy_spec": {
                "version": "2.2", "archetype": None, "archetype_name": "",
                "derived_features": [],
                "entry_conditions": [
                    {"source_type": "feature", "source": "order_imbalance",
                     "op": ">", "threshold": 0.3}
                ],
                "exit_time_ticks": 20, "exit_signal_conditions": [],
                "tunable_params": [], "features_used": ["order_imbalance"],
                "rationale": "",
            },
        }
        client = FakeLLMClient(planner_response=weak_spec_planner)
        runner = _make_runner(tmp_path, client=client)
        result = runner.run_spec_centric(
            research_goal="imbalance",
            max_plan_iterations=1, max_code_attempts=3,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
            precode_eval_threshold=0.60,
        )
        assert result.iterations == []


# ── test: good path — IS pass → verdict="pass" ───────────────────────────────

class TestGoodPath:
    def test_is_pass_no_oos_stops_loop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_good_summary(), [])
        )
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=3, max_code_attempts=3,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        assert result.verdict == "pass"
        assert result.best_run_id is not None
        assert len(result.iterations) == 1


# ── test: structural feedback → exits inner loop ──────────────────────────────

class TestStructuralRouting:
    """Structural feedback must break out of the inner code loop immediately."""

    def _make_structural_feedback_summary(self) -> dict[str, Any]:
        return {
            "signal_count": 15, "n_states": 1000, "n_fills": 15,
            "avg_holding_period": 25.0,
            "total_realized_pnl": -100.0,
            "total_unrealized_pnl": -20.0,
            "total_commission": 30.0,
            "total_slippage": 15.0,
            "total_impact": 5.0,
            "net_pnl": -170.0,
        }

    def test_structural_feedback_breaks_inner_loop_before_max_attempts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        call_count = {"n": 0}
        structural_summary = self._make_structural_feedback_summary()

        def _counting_backtest(*args, **kwargs):
            call_count["n"] += 1
            return structural_summary, []

        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", _counting_backtest)
        runner = _make_runner(tmp_path)
        runner.run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=1,
            max_code_attempts=5,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        assert call_count["n"] < 5, (
            f"Expected structural routing to stop before max_code_attempts, "
            f"but got {call_count['n']} backtest calls"
        )

    def test_structural_failure_stored_in_plan_record(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            LoopRunner, "_run_backtest_multi_code",
            lambda *a, **kw: (self._make_structural_feedback_summary(), []),
        )
        runner = _make_runner(tmp_path)
        runner.run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=1, max_code_attempts=5,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        plans_dir = tmp_path / "memory" / "plans"
        record = json.loads(list(plans_dir.glob("*.json"))[0].read_text())
        assert record["outcome"] == "structural_fail"

    def test_parametric_feedback_retries_same_spec(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Parametric verdict (no_trades / repair) stays in inner code loop."""
        call_count = {"n": 0}

        def _no_trade_backtest(*args, **kwargs):
            call_count["n"] += 1
            return _fail_summary(), []

        monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", _no_trade_backtest)
        runner = _make_runner(tmp_path)
        runner.run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=1,
            max_code_attempts=3,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        assert call_count["n"] == 3, (
            f"Expected 3 code attempts for parametric failure, got {call_count['n']}"
        )


# ── test: normalized_spec is the implementer input ────────────────────────────

class TestNormalizedSpecUsed:
    def test_implementer_receives_normalized_spec_from_review(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """normalized_spec from review must be passed to build_implementer_messages."""
        captured: list[Any] = []

        import strategy_loop.loop_runner as lr_mod

        original_build = lr_mod.build_implementer_messages

        def _capturing_build(spec, **kwargs):
            captured.append(spec)
            return original_build(spec, **kwargs)

        monkeypatch.setattr(lr_mod, "build_implementer_messages", _capturing_build)
        monkeypatch.setattr(
            LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_fail_summary(), [])
        )

        runner = _make_runner(tmp_path)
        runner.run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )

        assert len(captured) >= 1
        from strategy_loop.spec_review import review_spec
        from strategy_loop.spec_schema import StrategySpec
        used_spec = captured[0]
        assert isinstance(used_spec, StrategySpec)
        review = review_spec(used_spec)
        assert review.valid is True


# ── test: plan-level memory saved ─────────────────────────────────────────────

class TestPlanMemoryPersistence:
    def test_plan_record_saved_to_disk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_fail_summary(), [])
        )
        _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        plans_dir = tmp_path / "memory" / "plans"
        assert len(list(plans_dir.glob("*.json"))) == 1

    def test_plan_record_contains_expected_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_fail_summary(), [])
        )
        _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=1, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        plans_dir = tmp_path / "memory" / "plans"
        record = json.loads(list(plans_dir.glob("*.json"))[0].read_text())
        for field in ("plan_id", "archetype", "archetype_name", "spec", "spec_review", "precode_eval"):
            assert field in record


# ── test: max_plan_iterations respected ──────────────────────────────────────

class TestMaxPlanIterations:
    def test_stops_after_max_plan_iterations(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            LoopRunner, "_run_backtest_multi_code", lambda *a, **kw: (_fail_summary(), [])
        )
        result = _make_runner(tmp_path).run_spec_centric(
            research_goal="imbalance momentum",
            max_plan_iterations=2, max_code_attempts=1,
            data_dir=tmp_path, symbols=["005930"],
            date_ranges=DateRanges.from_single_day("20260102"), cfg={},
        )
        assert result.verdict == "no_pass"
        plans_dir = tmp_path / "memory" / "plans"
        assert len(list(plans_dir.glob("*.json"))) == 2
