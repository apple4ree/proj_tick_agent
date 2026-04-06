"""
Regression tests for code-mode threshold optimizer fallback behavior.
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


def test_optimize_code_thresholds_keeps_original_code_when_all_trials_invalid(monkeypatch):
    pytest.importorskip("optuna")
    import strategy_loop.threshold_optimizer as opt

    def _always_invalid_summary(*, candidate_code, stage_states, backtest_config, data_dir):
        return {
            "net_pnl": 0.0,
            "max_drawdown": 0.0,
            "signal_count": 0.0,
            "n_states": float(len(stage_states) or 1),
        }

    monkeypatch.setattr(opt, "_run_candidate_stage_backtest", _always_invalid_summary)

    result = opt.optimize_code_thresholds(
        code=_CODE_WITH_CONSTANT,
        states=[object(), object(), object(), object(), object()],
        backtest_config=_dummy_bt_config(),
        data_dir=".",
        n_trials=3,
        sampler_seed=7,
    )

    assert result.best_code == _CODE_WITH_CONSTANT
    assert result.best_score == opt._FREQ_PENALTY
    assert result.entry_frequency == 0.0
    assert result.n_trials_run == 3
    assert result.n_trials_pruned == 0


def test_optimize_code_thresholds_keeps_original_code_when_all_trials_pruned(monkeypatch):
    optuna = pytest.importorskip("optuna")
    import strategy_loop.threshold_optimizer as opt

    def _valid_summary(*, candidate_code, stage_states, backtest_config, data_dir):
        return {
            "net_pnl": 100_000.0,
            "max_drawdown": 0.01,
            "signal_count": 1.0,
            "n_states": float(len(stage_states)),
        }

    def _always_prune(trial, *, score, step_idx, enable_pruning):
        trial.report(score, step_idx)
        raise optuna.TrialPruned()

    monkeypatch.setattr(opt, "_run_candidate_stage_backtest", _valid_summary)
    monkeypatch.setattr(opt, "_report_stage_and_maybe_prune", _always_prune)

    result = opt.optimize_code_thresholds(
        code=_CODE_WITH_CONSTANT,
        states=[object(), object(), object(), object(), object()],
        backtest_config=_dummy_bt_config(),
        data_dir=".",
        n_trials=2,
        sampler_seed=11,
    )

    assert result.best_code == _CODE_WITH_CONSTANT
    assert result.best_score == opt._FREQ_PENALTY
    assert result.entry_frequency == 0.0
    assert result.n_trials_run == 2
    assert result.n_trials_pruned == 2
