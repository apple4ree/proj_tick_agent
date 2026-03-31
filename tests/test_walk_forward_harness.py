from __future__ import annotations

import json
from pathlib import Path

from evaluation_orchestration.layer6_evaluator.selection_metrics import SelectionMetrics
from evaluation_orchestration.layer7_validation.walk_forward.harness import (
    WalkForwardHarness,
    WindowExecutionArtifact,
)
from evaluation_orchestration.layer7_validation.walk_forward.window_plan import (
    WalkForwardWindow,
    WalkForwardWindowPlanner,
)


class _FixedPlanner:
    def __init__(self, windows: list[WalkForwardWindow]) -> None:
        self._windows = windows

    def build(self, *, start_date: str, end_date: str, cfg: dict) -> list[WalkForwardWindow]:
        return list(self._windows)


def _window(day: str) -> WalkForwardWindow:
    return WalkForwardWindow(
        train_start=day,
        train_end=day,
        select_start=day,
        select_end=day,
        holdout_start=day,
        holdout_end=day,
        forward_start=day,
        forward_end=day,
    )


def _summary_payload() -> dict:
    return {
        "net_pnl": 1000.0,
        "parent_order_count": 10.0,
        "child_order_count": 20.0,
        "cancel_rate": 0.4,
        "maker_fill_ratio": 0.3,
        "n_fills": 5.0,
        "total_commission": 50.0,
        "total_slippage": 10.0,
        "total_impact": 5.0,
    }


def _diagnostics_payload() -> dict:
    return {
        "lifecycle": {
            "parent_order_count": 10.0,
            "child_order_count": 20.0,
            "children_per_parent": 2.0,
            "cancel_rate": 0.4,
            "n_fills": 5.0,
        },
        "queue": {
            "maker_fill_ratio": 0.3,
            "queue_blocked_count": 2.0,
            "blocked_miss_count": 1.0,
            "queue_ready_count": 4.0,
        },
        "cancel_reasons": {
            "shares": {
                "adverse_selection": 0.2,
            },
        },
    }


def test_walk_forward_harness_loads_summary_and_diagnostics_from_run_dir(tmp_path: Path) -> None:
    windows = [_window("20260311"), _window("20260312")]
    planner = _FixedPlanner(windows)

    calls: list[dict] = []

    def _executor(**kwargs):
        idx = kwargs["window_index"]
        run_dir = tmp_path / f"run_{idx}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary.json").write_text(json.dumps(_summary_payload()), encoding="utf-8")
        (run_dir / "realism_diagnostics.json").write_text(json.dumps(_diagnostics_payload()), encoding="utf-8")
        calls.append(kwargs)
        return WindowExecutionArtifact(run_dir=str(run_dir))

    harness = WalkForwardHarness(
        window_planner=planner,
        selection_metrics=SelectionMetrics(),
        run_executor=_executor,
    )

    results = harness.run_spec(
        spec_path="/tmp/spec.json",
        symbol="005930",
        universe=False,
        cfg={"start_date": "20260311", "end_date": "20260312"},
        trial_id="trial-1",
    )

    assert len(results) == 2
    assert all(result.trial_id == "trial-1" for result in results)
    assert all(result.selection_score.metadata.get("valid") is True for result in results)
    assert calls[0]["symbol"] == "005930"
    assert calls[0]["universe"] is False


def test_walk_forward_harness_routes_symbol_and_universe_flags() -> None:
    windows = [_window("20260313")]
    planner = _FixedPlanner(windows)
    calls: list[dict] = []

    def _executor(**kwargs):
        calls.append(kwargs)
        return WindowExecutionArtifact(
            run_dir="/tmp/noop",
            summary=_summary_payload(),
            diagnostics=_diagnostics_payload(),
        )

    harness = WalkForwardHarness(
        window_planner=planner,
        selection_metrics=SelectionMetrics(),
        run_executor=_executor,
    )

    harness.run_spec(
        spec_path="spec.json",
        symbol="005930",
        universe=False,
        cfg={"start_date": "20260313", "end_date": "20260313"},
    )
    harness.run_spec(
        spec_path="spec.json",
        universe=True,
        cfg={"start_date": "20260313", "end_date": "20260313"},
    )

    assert calls[0]["universe"] is False
    assert calls[0]["symbol"] == "005930"
    assert calls[1]["universe"] is True
    assert calls[1]["symbol"] is None


def test_window_planner_is_deterministic_and_monotonic() -> None:
    planner = WalkForwardWindowPlanner()
    cfg = {
        "walk_forward": {
            "train_days": 2,
            "select_days": 1,
            "holdout_days": 1,
            "forward_days": 1,
            "step_days": 1,
        }
    }

    first = planner.build(start_date="20260310", end_date="20260316", cfg=cfg)
    second = planner.build(start_date="20260310", end_date="20260316", cfg=cfg)

    assert first == second
    assert len(first) >= 1

    prev_forward = None
    for window in first:
        assert window.train_start <= window.train_end
        assert window.select_start <= window.select_end
        assert window.holdout_start <= window.holdout_end
        assert window.forward_start <= window.forward_end
        if prev_forward is not None:
            assert prev_forward <= window.forward_start
        prev_forward = window.forward_start
