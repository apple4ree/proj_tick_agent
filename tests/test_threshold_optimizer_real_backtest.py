"""
Tests for real-backtest-based code threshold optimizer helpers and flow.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


_CODE_WITH_CONSTANT = """\
ORDER_IMBALANCE_THRESHOLD = 0.10

def generate_signal(features, position):
    if features.get("order_imbalance", 0.0) > ORDER_IMBALANCE_THRESHOLD:
        return 1
    return None
"""


def _dummy_bt_config() -> SimpleNamespace:
    return SimpleNamespace(initial_cash=100_000_000.0)


def test_score_formula_uses_net_return_minus_lambda_mdd_times_mdd_bps():
    import strategy_loop.threshold_optimizer as opt

    summary = {
        "net_pnl": 1_500_000.0,
        "max_drawdown": 0.03,
    }

    score, net_return_bps, mdd_bps = opt._score_from_backtest_summary(
        summary,
        initial_cash=100_000_000.0,
        lambda_mdd=1.0,
    )

    assert net_return_bps == pytest.approx(150.0)
    assert mdd_bps == pytest.approx(300.0)
    assert score == pytest.approx(-150.0)


def test_stage_state_slices_build_20_50_100_prefixes_and_avoid_empty():
    import strategy_loop.threshold_optimizer as opt

    states = list(range(10))
    slices = opt._build_stage_state_slices(states, [0.2, 0.5, 1.0])
    assert [len(s) for s in slices] == [2, 5, 10]

    small_states = [1]
    small_slices = opt._build_stage_state_slices(small_states, [0.2, 0.5, 1.0])
    assert [len(s) for s in small_slices] == [1, 1, 1]


def test_pruning_raises_trial_pruned_after_intermediate_report():
    optuna = pytest.importorskip("optuna")
    import strategy_loop.threshold_optimizer as opt

    class _FakeTrial:
        def __init__(self) -> None:
            self.reports = []

        def report(self, score: float, step_idx: int) -> None:
            self.reports.append((score, step_idx))

        def should_prune(self) -> bool:
            return True

    trial = _FakeTrial()
    with pytest.raises(optuna.TrialPruned):
        opt._report_stage_and_maybe_prune(
            trial,
            score=123.4,
            step_idx=1,
            enable_pruning=True,
        )

    assert trial.reports == [(123.4, 1)]


def test_optimize_code_thresholds_uses_real_backtest_stage_path(monkeypatch):
    pytest.importorskip("optuna")
    import strategy_loop.threshold_optimizer as opt

    calls = []

    def _fake_backtest(*, candidate_code, stage_states, backtest_config, data_dir):
        calls.append(len(stage_states))
        return {
            "net_pnl": 100_000.0,
            "max_drawdown": 0.01,
            "signal_count": 1.0,
            "n_states": float(len(stage_states)),
        }

    monkeypatch.setattr(opt, "_run_candidate_stage_backtest", _fake_backtest)

    result = opt.optimize_code_thresholds(
        code=_CODE_WITH_CONSTANT,
        states=[object() for _ in range(10)],
        backtest_config=_dummy_bt_config(),
        data_dir=".",
        n_trials=1,
        sampler_seed=5,
        stage_prefixes=(0.2, 0.5, 1.0),
        enable_pruning=False,
    )

    assert calls == [2, 5, 10]
    assert result.best_score > opt._FREQ_PENALTY
    assert result.n_trials_run == 1
