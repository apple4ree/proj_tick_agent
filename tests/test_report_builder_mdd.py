from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from evaluation_orchestration.layer7_validation.backtest_config import BacktestConfig
from evaluation_orchestration.layer7_validation.report_builder import ReportBuilder


class _DummyLedger:
    def __init__(self) -> None:
        idx = pd.to_datetime([
            "2026-03-13 10:23:53",
            "2026-03-13 10:25:43",
            "2026-03-13 10:29:32",
        ])
        self._cum_pnl = pd.Series([-6365.0, -21933.0, 4703.65], index=idx, name="cumulative_net_pnl")
        self._report = SimpleNamespace(
            total_realized=0.0,
            total_unrealized=0.0,
            total_commission=0.0,
            total_tax=0.0,
            total_slippage=0.0,
            total_impact=0.0,
            net_pnl=float(self._cum_pnl.iloc[-1]),
            pnl_series=self._cum_pnl,
        )

    def generate_report(self):
        return self._report

    def cumulative_pnl_series(self) -> pd.Series:
        return self._cum_pnl


def _exec_report() -> SimpleNamespace:
    return SimpleNamespace(
        fill_rate=0.0,
        cancel_rate=0.0,
        implementation_shortfall_bps=0.0,
        vwap_diff_bps=0.0,
        avg_slippage_bps=0.0,
        avg_market_impact_bps=0.0,
        timing_score=0.0,
        partial_fill_rate=0.0,
        maker_fill_ratio=0.0,
        avg_latency_ms=0.0,
    )


def _turnover_report() -> SimpleNamespace:
    return SimpleNamespace(
        annualized_turnover=0.0,
        avg_holding_period=0.0,
        iqm_return=0.0,
    )


def test_report_builder_uses_portfolio_value_for_risk_metrics(monkeypatch):
    seen = {}

    def _fake_risk_compute(*, pnl_series, freq, annualization_factor, period="full"):
        seen["series"] = pnl_series.copy()
        return SimpleNamespace(
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            max_drawdown=0.0,
            max_drawdown_duration=0,
            annualized_vol=0.0,
            var_95=0.0,
            expected_shortfall_95=0.0,
        )

    monkeypatch.setattr(
        "evaluation_orchestration.layer6_evaluator.risk_metrics.RiskMetrics.compute",
        _fake_risk_compute,
    )
    monkeypatch.setattr(
        "evaluation_orchestration.layer6_evaluator.execution_metrics.ExecutionMetrics.compute",
        lambda fills, parent_orders, states: _exec_report(),
    )
    monkeypatch.setattr(
        "evaluation_orchestration.layer6_evaluator.turnover_metrics.TurnoverMetrics.compute",
        lambda fills, portfolio_values, positions_history, annualization_factor: _turnover_report(),
    )

    config = BacktestConfig(symbol="005930", start_date="2026-03-13", end_date="2026-03-13", initial_cash=100_000_000.0)
    builder = ReportBuilder(config=config, pnl_ledger=_DummyLedger())

    portfolio_values = [
        (pd.Timestamp("2026-03-13 10:23:53"), 99_993_635.0),
        (pd.Timestamp("2026-03-13 10:25:43"), 99_978_067.0),
        (pd.Timestamp("2026-03-13 10:29:32"), 100_004_703.65),
    ]

    builder.generate_reports(
        fills=[],
        parent_orders=[],
        states=[],
        signals=[],
        portfolio_values=portfolio_values,
        positions_history=[],
        arrival_prices={},
        twap_prices={},
        run_id="test-run",
    )

    expected = pd.Series(
        [99_993_635.0, 99_978_067.0, 100_004_703.65],
        index=pd.to_datetime([
            "2026-03-13 10:23:53",
            "2026-03-13 10:25:43",
            "2026-03-13 10:29:32",
        ]),
        dtype=float,
    )

    pd.testing.assert_series_equal(seen["series"], expected, check_names=False)
