from __future__ import annotations

import pandas as pd
import pytest

from evaluation_orchestration.layer6_evaluator.risk_metrics import RiskMetrics


def test_compute_drawdown_uses_true_peak_to_trough_fraction_for_positive_equity_curve():
    series = pd.Series([100.0, 120.0, 90.0, 130.0])

    mdd, duration = RiskMetrics._compute_drawdown(series)

    assert mdd == pytest.approx(0.25)
    assert duration == 1
