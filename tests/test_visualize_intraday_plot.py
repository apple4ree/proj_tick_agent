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


def _write_minimal_run_artifacts(run_dir: Path) -> None:
    ts = pd.date_range("2026-03-13 09:00:00", periods=6, freq="1s")

    pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": ["005930"] * len(ts),
            "score": [0.2, 0.4, -0.1, 0.3, 0.1, -0.2],
            "expected_return": [1.0, 1.2, -0.3, 0.8, 0.4, -0.5],
            "confidence": [0.6, 0.7, 0.5, 0.65, 0.55, 0.45],
            "horizon_steps": [5] * len(ts),
            "is_valid": [True] * len(ts),
        }
    ).to_csv(run_dir / "signals.csv", index=False)

    pd.DataFrame(
        {
            "timestamp": [ts[1], ts[3], ts[5]],
            "symbol": ["005930", "005930", "005930"],
            "side": ["BUY", "SELL", "BUY"],
            "filled_qty": [10, 8, 12],
            "fill_price": [100000, 100050, 100030],
            "fee": [5.0, 4.0, 6.0],
            "slippage_bps": [1.1, -0.8, 0.6],
            "market_impact_bps": [0.3, 0.2, 0.4],
            "latency_ms": [1.5, 1.8, 1.2],
            "parent_id": ["o1", "o2", "o3"],
            "order_id": ["c1", "c2", "c3"],
        }
    ).to_csv(run_dir / "fills.csv", index=False)

    pd.DataFrame(
        {
            "order_id": ["o1", "o2", "o3"],
            "arrival_mid": [100002, 100048, 100028],
        }
    ).to_csv(run_dir / "orders.csv", index=False)

    pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": ["005930"] * len(ts),
            "best_bid": [99990, 99995, 100000, 100010, 100015, 100020],
            "best_ask": [100010, 100015, 100020, 100030, 100035, 100040],
            "mid_price": [100000, 100005, 100010, 100020, 100025, 100030],
        }
    ).to_csv(run_dir / "market_quotes.csv", index=False)

    pnl_entries = pd.DataFrame(
        {
            "timestamp": [ts[1], ts[2], ts[3], ts[4], ts[5]],
            "symbol": ["005930"] * 5,
            "realized_pnl": [0.0, 300.0, -120.0, 180.0, 0.0],
            "unrealized_pnl": [50.0, 70.0, 10.0, 20.0, 0.0],
            "commission_cost": [5.0, 5.0, 4.0, 4.0, 3.0],
            "tax_cost": [0.0, 0.0, 0.0, 0.0, 0.0],
            "net_pnl": [45.0, 365.0, -114.0, 196.0, -3.0],
            "slippage_cost": [1.0, 2.0, 1.0, 1.0, 1.0],
            "impact_cost": [0.5, 0.5, 0.4, 0.4, 0.3],
        }
    )
    pnl_entries = pnl_entries.set_index("timestamp")
    pnl_entries.to_csv(run_dir / "pnl_entries.csv")

    with (run_dir / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "sharpe_ratio": 1.2,
                "sortino_ratio": 1.4,
                "n_fills": 3,
                "net_pnl": 489.0,
                "total_commission": 21.0,
                "total_slippage": 6.0,
                "total_impact": 2.1,
                "is_bps": 0.9,
                "avg_slippage_bps": 0.3,
                "avg_market_impact_bps": 0.2,
                "timing_score": 0.7,
                "alpha_contribution": 300.0,
                "execution_contribution": 100.0,
                "cost_contribution": -30.0,
                "timing_contribution": 119.0,
                "total_realized_pnl": 360.0,
                "max_drawdown": -0.01,
                "var_95": -1000.0,
                "expected_shortfall_95": -1200.0,
                "fill_rate": 0.95,
                "annualized_vol": 0.12,
                "annualized_turnover": 1.8,
                "avg_latency_ms": 1.5,
                "cancel_rate": 0.2,
                "signal_count": 6,
                "parent_order_count": 3,
                "child_order_count": 6,
                "avg_child_lifetime_seconds": 1.4,
            },
            fh,
        )

    with (run_dir / "realism_diagnostics.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "observation_lag": {
                    "configured_market_data_delay_ms": 0.0,
                    "avg_observation_staleness_ms": 0.0,
                    "effective_delay_ms": 0.0,
                    "resample_interval": "1s",
                    "canonical_tick_interval_ms": 1000.0,
                },
                "decision_latency": {
                    "configured_decision_compute_ms": 0.0,
                    "decision_latency_enabled": False,
                    "avg_decision_state_age_ms": 0.0,
                },
                "tick_time": {
                    "canonical_tick_interval_ms": 1000.0,
                    "resample_interval": "1s",
                    "state_history_max_len": 64,
                    "strategy_runtime_lookback_ticks": 12,
                    "history_safety_buffer_ticks": 10,
                },
                "queue": {
                    "queue_model": "prob_queue",
                    "queue_position_assumption": 0.5,
                    "queue_wait_ticks": 1.1,
                    "queue_wait_ms": 1100.0,
                    "blocked_miss_count": 2,
                    "ready_but_not_filled_count": 1,
                },
                "cancel_reasons": {
                    "counts": {
                        "timeout": 2,
                        "adverse_selection": 1,
                        "stale_price": 0,
                        "max_reprices_reached": 0,
                        "micro_event_block": 0,
                        "unknown": 0,
                    },
                    "shares": {
                        "timeout": 0.6667,
                        "adverse_selection": 0.3333,
                        "stale_price": 0.0,
                        "max_reprices_reached": 0.0,
                        "micro_event_block": 0.0,
                        "unknown": 0.0,
                    },
                },
                "latency": {
                    "sampled_avg_submit_latency_ms": 1.0,
                    "sampled_avg_cancel_latency_ms": 0.5,
                    "sampled_avg_fill_latency_ms": 1.4,
                    "cancel_pending_count": 0,
                    "fills_before_cancel_effective_count": 0,
                    "avg_cancel_effective_lag_ms": 0.5,
                    "configured_order_submit_ms": 1.0,
                    "configured_cancel_ms": 0.5,
                },
                "lifecycle": {
                    "signal_count": 6,
                    "parent_order_count": 3,
                    "child_order_count": 6,
                    "n_fills": 3,
                    "cancel_rate": 0.2,
                    "avg_child_lifetime_seconds": 1.4,
                    "max_children_per_parent": 4,
                    "max_cancelled_children_per_parent": 2,
                    "top_parent_by_children": "o1",
                },
            },
            fh,
        )


def test_generate_all_plots_includes_intraday_plot(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_minimal_run_artifacts(run_dir)

    paths = visualize.generate_all_plots(run_dir, show=False)

    assert len(paths) == 8
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


def test_intraday_plot_handles_empty_data(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run_empty"
    run_dir.mkdir(parents=True, exist_ok=True)

    out = visualize.plot_intraday_cumulative_profit({}, run_dir, show=False)

    assert out.name == "intraday_cumulative_profit.png"
    assert out.exists()


def test_intraday_plot_uses_pnl_series_when_entries_missing(tmp_path: Path):
    visualize = _load_visualize_module()
    run_dir = tmp_path / "run_series"
    run_dir.mkdir(parents=True, exist_ok=True)

    idx = pd.date_range("2026-03-13 09:00:00", periods=5, freq="1s")
    pd.DataFrame({"cumulative_net_pnl": [0.0, 10.0, 5.0, 20.0, 25.0]}, index=idx).to_csv(
        run_dir / "pnl_series.csv"
    )

    data = visualize.load_run(run_dir)
    out = visualize.plot_intraday_cumulative_profit(data, run_dir, show=False)

    assert out.exists()
