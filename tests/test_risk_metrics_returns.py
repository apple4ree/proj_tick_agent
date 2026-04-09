from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evaluation_orchestration.layer6_evaluator.risk_metrics import RiskMetrics


def _equity_from_returns(initial_equity: float, returns: list[float]) -> pd.Series:
    equity = [initial_equity]
    cur = initial_equity
    for r in returns:
        cur = cur * (1.0 + r)
        equity.append(cur)
    return pd.Series(equity)


def test_compute_uses_fractional_returns_for_sharpe_and_sortino():
    period_returns = np.array([0.10, -0.05, -0.02, 0.08], dtype=float)
    equity = _equity_from_returns(100.0, period_returns.tolist())

    report = RiskMetrics.compute(equity, freq="daily", annualization_factor=252)

    mean_ret = float(np.mean(period_returns))
    std_ret = float(np.std(period_returns, ddof=1))
    downside = period_returns[period_returns < 0.0]
    downside_std = float(np.std(downside, ddof=1))

    expected_vol = std_ret * np.sqrt(252)
    expected_sharpe = mean_ret * 252 / expected_vol
    expected_sortino = mean_ret * 252 / (downside_std * np.sqrt(252))

    assert report.annualized_vol == pytest.approx(expected_vol)
    assert report.sharpe_ratio == pytest.approx(expected_sharpe)
    assert report.sortino_ratio == pytest.approx(expected_sortino)
