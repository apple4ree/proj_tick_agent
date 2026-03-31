from __future__ import annotations

from evaluation_orchestration.layer6_evaluator.selection_metrics import SelectionScore
from evaluation_orchestration.layer7_validation.walk_forward.harness import WalkForwardRunResult
from evaluation_orchestration.layer7_validation.walk_forward.selector import WalkForwardSelector
from evaluation_orchestration.layer7_validation.walk_forward.window_plan import WalkForwardWindow


def _window(idx: int) -> WalkForwardWindow:
    day = f"202603{10 + idx:02d}"
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


def _result(idx: int, score: float, *, valid: bool = True, flags: dict | None = None) -> WalkForwardRunResult:
    metadata = {"valid": valid, "flags": dict(flags or {})}
    return WalkForwardRunResult(
        trial_id="trial-x",
        window=_window(idx),
        run_dir=f"/tmp/run-{idx}",
        summary={},
        diagnostics={},
        selection_score=SelectionScore(
            total_score=score,
            components={"edge_net_pnl": score},
            penalties={},
            metadata=metadata,
        ),
    )


def test_selector_passes_when_thresholds_satisfied() -> None:
    selector = WalkForwardSelector()
    results = [_result(0, 0.3), _result(1, 0.1), _result(2, 0.4)]
    cfg = {
        "selector": {
            "min_windows": 2,
            "min_pass_windows": 2,
            "min_window_score": 0.0,
            "min_average_score": 0.05,
            "max_churn_heavy_share": 1.0,
            "max_cost_dominated_share": 1.0,
            "max_adverse_selection_share": 1.0,
            "max_score_std": 5.0,
        }
    }

    decision = selector.select(results, cfg)
    assert decision.passed is True
    assert decision.reasons == []


def test_selector_fails_on_too_few_valid_runs() -> None:
    selector = WalkForwardSelector()
    results = [_result(0, 0.2, valid=True), _result(1, 0.1, valid=False)]
    decision = selector.select(results, {"selector": {"min_windows": 2, "min_pass_windows": 1}})

    assert decision.passed is False
    assert any("too_few_valid_runs" in reason for reason in decision.reasons)


def test_selector_fails_when_churn_heavy_share_too_high() -> None:
    selector = WalkForwardSelector()
    results = [
        _result(0, 0.1, flags={"churn_heavy": True}),
        _result(1, 0.2, flags={"churn_heavy": True}),
        _result(2, 0.3, flags={"churn_heavy": False}),
    ]
    cfg = {
        "selector": {
            "min_windows": 3,
            "min_pass_windows": 1,
            "min_window_score": -1.0,
            "min_average_score": -1.0,
            "max_churn_heavy_share": 0.5,
            "max_cost_dominated_share": 1.0,
            "max_adverse_selection_share": 1.0,
            "max_score_std": 5.0,
        }
    }

    decision = selector.select(results, cfg)
    assert decision.passed is False
    assert any("churn_heavy_share_too_high" in reason for reason in decision.reasons)


def test_selector_fails_when_average_score_below_threshold() -> None:
    selector = WalkForwardSelector()
    results = [_result(0, -1.0), _result(1, -0.8), _result(2, -0.9)]
    cfg = {
        "selector": {
            "min_windows": 2,
            "min_pass_windows": 1,
            "min_window_score": -2.0,
            "min_average_score": -0.5,
            "max_churn_heavy_share": 1.0,
            "max_cost_dominated_share": 1.0,
            "max_adverse_selection_share": 1.0,
            "max_score_std": 5.0,
        }
    }

    decision = selector.select(results, cfg)
    assert decision.passed is False
    assert any("average_score_below_threshold" in reason for reason in decision.reasons)


def test_selector_aggregation_is_deterministic() -> None:
    selector = WalkForwardSelector()
    results = [_result(0, 0.2), _result(1, -0.1), _result(2, 0.5)]
    cfg = {
        "selector": {
            "min_windows": 2,
            "min_pass_windows": 1,
            "min_window_score": -0.2,
            "min_average_score": -0.2,
            "max_churn_heavy_share": 1.0,
            "max_cost_dominated_share": 1.0,
            "max_adverse_selection_share": 1.0,
            "max_score_std": 5.0,
        }
    }

    first = selector.select(results, cfg)
    second = selector.select(results, cfg)

    assert first.aggregate_score == second.aggregate_score
    assert first.reasons == second.reasons
    assert first.metadata == second.metadata
