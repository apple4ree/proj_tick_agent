from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _load_backtest_module():
    script_path = PROJECT_ROOT / "scripts" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_test_spec(output_dir: Path) -> str:
    spec_json = {
        "spec_format": "v2",
        "name": "test_spec_v2",
        "version": "2.0",
        "entry_policies": [
            {
                "name": "long_entry",
                "side": "long",
                "trigger": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.1},
                "strength": {"type": "const", "value": 0.5},
            }
        ],
        "exit_policies": [
            {
                "name": "default_exit",
                "rules": [
                    {
                        "name": "time_exit",
                        "priority": 1,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "holding_ticks"},
                            "op": ">=",
                            "threshold": 2,
                        },
                        "action": {"type": "close_all"},
                    }
                ],
            }
        ],
        "risk_policy": {
            "max_position": 100,
            "inventory_cap": 100,
            "position_sizing": {"mode": "fixed", "base_size": 10, "max_size": 20},
        },
    }
    spec_path = output_dir / "test_spec_v2.json"
    spec_path.write_text(json.dumps(spec_json), encoding="utf-8")
    return str(spec_path)


def _make_raw_csv(path: Path, symbol: str, date: str, n_steps: int = 12) -> None:
    rows: list[dict[str, object]] = []
    start_ts = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:8]} 09:00:00")

    for step in range(n_steps):
        timestamp = start_ts + pd.Timedelta(seconds=step)
        mid_shift = 5 * step
        row: dict[str, object] = {
            "BSOP_DATE": date,
            "STCK_CNTG_HOUR": timestamp.strftime("%H%M%S"),
            "MKSC_SHRN_ISCD": symbol,
            "HOUR_CLS_CODE": "0",
        }
        for level in range(1, 11):
            row[f"BIDP{level}"] = 100000 + mid_shift - 10 * (level - 1)
            row[f"ASKP{level}"] = 100010 + mid_shift + 10 * (level - 1)
            row[f"BIDP_RSQN{level}"] = 3000 + 30 * level + 5 * step
            row[f"ASKP_RSQN{level}"] = 200 + 5 * level
            row[f"BIDP_RSQN_ICDC{level}"] = 0
            row[f"ASKP_RSQN_ICDC{level}"] = 0
        rows.append(row)

    pd.DataFrame(rows).to_csv(path, index=False)


def test_backtest_script_builds_states_and_runs_pipeline():
    backtest = _load_backtest_module()

    with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
        data_dir = Path(data_tmp)
        symbol = "005930"
        date = "20260312"
        day_dir = data_dir / symbol / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _make_raw_csv(day_dir / "lob.csv", symbol=symbol, date=date)

        states = backtest.build_states_for_range(
            data_dir=data_dir,
            symbol=symbol,
            start_date=date,
        )
        assert len(states) == 12

        output_dir = Path(output_tmp)
        spec_path = _write_test_spec(output_dir)

        from evaluation_orchestration.layer7_validation import BacktestConfig
        from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
        from strategy_block.strategy_compiler import compile_strategy

        config = BacktestConfig(
            symbol=symbol,
            start_date="2026-03-12",
            end_date="2026-03-12",
            initial_cash=1e8,
            seed=123,
            slicing_algo="TWAP",
            placement_style="aggressive",
            latency_ms=1.0,
            fee_model="krx",
            impact_model="linear",
            compute_attribution=False,
        )
        strategy = compile_strategy(StrategySpecV2.load(spec_path))

        result = backtest.run_backtest_with_states(
            config=config,
            states=states,
            data_dir=str(data_dir),
            output_dir=str(output_tmp),
            strategy=strategy,
        )
        summary = result.summary()

        assert result.n_states == 12
        assert result.n_fills >= 1
        assert summary["fill_rate"] > 0.0

        run_dir = Path(output_tmp) / result.run_id
        assert (run_dir / "summary.json").exists()
        assert (run_dir / "realism_diagnostics.json").exists()

        saved_summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        for key in (
            "resample_interval",
            "canonical_tick_interval_ms",
            "configured_market_data_delay_ms",
            "avg_observation_staleness_ms",
            "configured_decision_compute_ms",
            "decision_latency_enabled",
            "effective_delay_ms",
            "queue_model",
            "queue_position_assumption",
            "state_history_max_len",
            "strategy_runtime_lookback_ticks",
            "avg_child_lifetime_seconds",
            "cancel_rate",
        ):
            assert key in saved_summary

        diagnostics = json.loads((run_dir / "realism_diagnostics.json").read_text(encoding="utf-8"))
        for section in (
            "observation_lag",
            "decision_latency",
            "tick_time",
            "lifecycle",
            "queue",
            "latency",
            "cancel_reasons",
            "timings",
            "config_snapshot",
        ):
            assert section in diagnostics

        assert "canonical_tick_interval_ms" in diagnostics["tick_time"]
        assert "configured_decision_compute_ms" in diagnostics["decision_latency"]
        assert "avg_decision_state_age_ms" in diagnostics["decision_latency"]
        assert "decision_state_samples_count" in diagnostics["decision_latency"]
        for key in ("queue_wait_ticks", "queue_wait_ms", "blocked_miss_count", "ready_but_not_filled_count"):
            assert key in diagnostics["queue"]
        assert "configured_order_submit_ms" in diagnostics["latency"]
        assert "configured_order_ack_ms" in diagnostics["latency"]
        assert "configured_cancel_ms" in diagnostics["latency"]
        assert "avg_cancel_effective_lag_ms" in diagnostics["latency"]
        assert "cancel_pending_count" in diagnostics["latency"]
        assert "fills_before_cancel_effective_count" in diagnostics["latency"]
        assert diagnostics["latency"]["order_ack_used_for_fill_gating"] is False
        assert diagnostics["latency"]["latency_alias_applied"] is True
        for key in ("max_children_per_parent", "max_cancelled_children_per_parent", "top_parent_by_children", "top_parent_by_cancelled_children"):
            assert key in diagnostics["lifecycle"]
        assert "configured_order_submit_ms" in saved_summary
        assert saved_summary["latency_alias_applied"] is True
        assert "configured_cancel_ms" in saved_summary
        assert "queue_wait_ms" not in saved_summary
        assert "cancel_pending_count" not in saved_summary
        assert "max_children_per_parent" not in saved_summary
        assert "counts" in diagnostics["cancel_reasons"]
        assert "shares" in diagnostics["cancel_reasons"]
        for bucket in ("timeout", "adverse_selection", "stale_price", "max_reprices_reached", "micro_event_block", "unknown"):
            assert bucket in diagnostics["cancel_reasons"]["counts"]
            assert bucket in diagnostics["cancel_reasons"]["shares"]


def test_backtest_config_from_cfg():
    backtest = _load_backtest_module()

    cfg = {
        "backtest": {
            "initial_cash": 5e7,
            "seed": 99,
            "fee_model": "zero",
            "exchange_model": "partial_fill",
            "queue_model": "none",
            "queue_position_assumption": 0.25,
        },
    }
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
    )
    assert bc.symbol == "005930"
    assert bc.initial_cash == 5e7
    assert bc.seed == 99
    assert bc.fee_model == "zero"
    assert bc.exchange_model == "partial_fill"
    assert bc.queue_model == "none"
    assert bc.queue_position_assumption == 0.25


# ---------------------------------------------------------------------------
# market_data_delay_ms propagation tests
# ---------------------------------------------------------------------------


def test_build_config_propagates_market_data_delay_ms():
    """build_config() must forward market_data_delay_ms to BacktestConfig."""
    backtest = _load_backtest_module()
    import argparse

    args = argparse.Namespace(
        symbol="005930",
        start_date="20260313",
        end_date=None,
    )
    bt_cfg = {"market_data_delay_ms": 250.0}
    config = backtest.build_config(args, bt_cfg)
    assert config.market_data_delay_ms == 250.0


def test_build_config_defaults_delay_to_zero():
    """build_config() defaults market_data_delay_ms to 0.0 when absent."""
    backtest = _load_backtest_module()
    import argparse

    args = argparse.Namespace(
        symbol="005930",
        start_date="20260313",
        end_date=None,
    )
    config = backtest.build_config(args, {})
    assert config.market_data_delay_ms == 0.0


def test_backtest_config_from_cfg_propagates_market_data_delay_ms():
    """backtest_config_from_cfg() must forward market_data_delay_ms."""
    backtest = _load_backtest_module()
    cfg = {
        "backtest": {
            "market_data_delay_ms": 500.0,
        },
    }
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
    )
    assert bc.market_data_delay_ms == 500.0


def test_backtest_config_from_cfg_defaults_delay_to_zero():
    """backtest_config_from_cfg() defaults market_data_delay_ms to 0.0."""
    backtest = _load_backtest_module()
    cfg = {"backtest": {}}
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
    )
    assert bc.market_data_delay_ms == 0.0


def test_backtest_config_from_cfg_override_takes_precedence():
    """Keyword override for market_data_delay_ms wins over config."""
    backtest = _load_backtest_module()
    cfg = {
        "backtest": {
            "market_data_delay_ms": 100.0,
        },
    }
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
        market_data_delay_ms=999.0,
    )
    assert bc.market_data_delay_ms == 999.0


def test_build_config_propagates_decision_compute_ms():
    """build_config() must forward decision_compute_ms to BacktestConfig."""
    backtest = _load_backtest_module()
    import argparse

    args = argparse.Namespace(
        symbol="005930",
        start_date="20260313",
        end_date=None,
    )
    bt_cfg = {"decision_compute_ms": 123.0}
    config = backtest.build_config(args, bt_cfg)
    assert config.decision_compute_ms == 123.0


def test_backtest_config_from_cfg_propagates_decision_compute_ms():
    """backtest_config_from_cfg() must forward decision_compute_ms."""
    backtest = _load_backtest_module()
    cfg = {
        "backtest": {
            "decision_compute_ms": 321.0,
        },
    }
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
    )
    assert bc.decision_compute_ms == 321.0


def test_backtest_config_from_cfg_decision_override_takes_precedence():
    """Keyword override for decision_compute_ms wins over config."""
    backtest = _load_backtest_module()
    cfg = {
        "backtest": {
            "decision_compute_ms": 100.0,
        },
    }
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
        decision_compute_ms=999.0,
    )
    assert bc.decision_compute_ms == 999.0
