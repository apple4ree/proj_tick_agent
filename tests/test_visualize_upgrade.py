from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _load_visualize_module():
    script_path = PROJECT_ROOT / "scripts" / "internal" / "adhoc" / "visualize.py"
    spec = importlib.util.spec_from_file_location("visualize_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_base_artifacts_without_optional(run_dir: Path) -> None:
    ts = pd.date_range("2026-03-13 09:00:00", periods=8, freq="1s")

    pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": ["005930"] * len(ts),
            "score": [0.1, 0.2, -0.1, 0.3, 0.2, -0.2, 0.1, -0.1],
            "expected_return": [0.5, 0.8, -0.2, 1.1, 0.9, -0.3, 0.4, -0.1],
            "confidence": [0.6, 0.7, 0.45, 0.8, 0.7, 0.4, 0.55, 0.5],
            "horizon_steps": [5] * len(ts),
            "is_valid": [True] * len(ts),
        }
    ).to_csv(run_dir / "signals.csv", index=False)

    pd.DataFrame(
        {
            "timestamp": [ts[1], ts[3], ts[6]],
            "symbol": ["005930", "005930", "005930"],
            "side": ["BUY", "SELL", "BUY"],
            "filled_qty": [5, 7, 4],
            "fill_price": [100000, 100040, 100020],
            "fee": [2.0, 3.0, 2.0],
            "slippage_bps": [0.5, -0.4, 0.3],
            "market_impact_bps": [0.1, 0.2, 0.1],
            "latency_ms": [1.2, 1.5, 1.1],
            "parent_id": ["p1", "p2", "p3"],
            "order_id": ["c1", "c2", "c3"],
        }
    ).to_csv(run_dir / "fills.csv", index=False)

    pd.DataFrame(
        {"cumulative_net_pnl": [0.0, 20.0, 15.0, 40.0, 30.0, 45.0, 42.0, 55.0]}, index=ts
    ).to_csv(run_dir / "pnl_series.csv")

    pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": ["005930"] * len(ts),
            "realized_pnl": [0.0, 20.0, -5.0, 25.0, -10.0, 15.0, -3.0, 10.0],
            "unrealized_pnl": [0.0, 5.0, 4.0, 7.0, 5.0, 6.0, 5.0, 7.0],
            "commission_cost": [1.0] * len(ts),
            "tax_cost": [0.0] * len(ts),
            "net_pnl": [0.0, 24.0, -1.0, 31.0, -6.0, 20.0, 1.0, 16.0],
            "slippage_cost": [0.3] * len(ts),
            "impact_cost": [0.1] * len(ts),
        }
    ).to_csv(run_dir / "pnl_entries.csv", index=False)

    with (run_dir / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "sharpe_ratio": 0.9,
                "sortino_ratio": 1.1,
                "n_fills": 3,
                "net_pnl": 55.0,
                "max_drawdown": -0.03,
                "annualized_vol": 0.15,
                "total_commission": 8.0,
                "total_slippage": 2.4,
                "total_impact": 0.8,
                "is_bps": 0.5,
                "avg_slippage_bps": 0.13,
                "avg_market_impact_bps": 0.07,
                "timing_score": 0.6,
                "alpha_contribution": 20.0,
                "execution_contribution": 15.0,
                "cost_contribution": -5.0,
                "timing_contribution": 25.0,
                "total_realized_pnl": 52.0,
                "var_95": -200.0,
                "expected_shortfall_95": -250.0,
                "fill_rate": 0.8,
                "annualized_turnover": 1.2,
                "avg_latency_ms": 1.3,
                "cancel_rate": 0.25,
                "signal_count": 8,
                "parent_order_count": 3,
                "child_order_count": 10,
                "avg_child_lifetime_seconds": 1.7,
            },
            fh,
        )


def test_trade_timeline_works_without_market_quotes(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run_trade_timeline"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_base_artifacts_without_optional(run_dir)

    data = visualize.load_run(run_dir)
    out = visualize.plot_trade_timeline(data, run_dir, show=False)

    assert out.name == "trade_timeline.png"
    assert out.exists()


def test_realism_dashboard_works_without_realism_diagnostics(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run_realism_fallback"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_base_artifacts_without_optional(run_dir)

    data = visualize.load_run(run_dir)
    assert "realism_diagnostics" not in data
    out = visualize.plot_realism_dashboard(data, run_dir, show=False)

    assert out.name == "realism_dashboard.png"
    assert out.exists()


def test_generate_all_plots_handles_missing_optional_artifacts(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run_all_fallback"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_base_artifacts_without_optional(run_dir)

    paths = visualize.generate_all_plots(run_dir, show=False)

    names = {p.name for p in paths}
    assert names == {
        "overview.png",
        "signal_analysis.png",
        "execution_quality.png",
        "dashboard.png",
        "intraday_cumulative_profit.png",
        "trade_timeline.png",
        "equity_risk.png",
        "realism_dashboard.png",
    }
    for name in names:
        assert (run_dir / "plots" / name).exists()


def test_generate_report_plots_emits_default_backtest_subset(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run_report_subset"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_base_artifacts_without_optional(run_dir)

    paths = visualize.generate_report_plots(run_dir, show=False)

    names = {p.name for p in paths}
    assert names == {
        "dashboard.png",
        "intraday_cumulative_profit.png",
        "trade_timeline.png",
    }
    for name in names:
        assert (run_dir / "plots" / name).exists()


def test_generate_report_plots_handles_missing_summary_json(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run_report_no_summary"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_base_artifacts_without_optional(run_dir)

    (run_dir / "summary.json").unlink()

    paths = visualize.generate_report_plots(run_dir, show=False)

    names = {p.name for p in paths}
    assert names == {
        "dashboard.png",
        "intraday_cumulative_profit.png",
        "trade_timeline.png",
    }
    for name in names:
        assert (run_dir / "plots" / name).exists()
