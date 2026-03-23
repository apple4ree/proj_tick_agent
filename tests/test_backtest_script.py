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
    """Write a minimal strategy spec JSON and return its path."""
    spec_json = {
        "name": "test_spec",
        "version": "1.0",
        "signal_rules": [
            {"feature": "order_imbalance", "operator": ">",
             "threshold": 0.1, "score_contribution": 0.5}
        ],
        "exit_rules": [{"exit_type": "time_exit", "timeout_ticks": 100}],
    }
    spec_path = output_dir / "test_spec.json"
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


def _make_h0stasp0_csv(path: Path, symbol: str, date: str, n_steps: int = 12) -> None:
    rows: list[dict[str, object]] = []
    start_ts = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:8]} 09:00:00", tz="Asia/Seoul")

    for step in range(n_steps):
        timestamp = start_ts + pd.Timedelta(seconds=step)
        mid_shift = 5 * step
        row: dict[str, object] = {
            "recv_ts_utc": timestamp.tz_convert("UTC").isoformat(),
            "recv_ts_kst": timestamp.isoformat(),
            "tr_id": "H0STASP0",
            "MKSC_SHRN_ISCD": symbol,
            "BSOP_HOUR": timestamp.strftime("%H%M%S"),
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
        from strategy_block.strategy_specs.schema import StrategySpec
        from strategy_block.strategy_compiler.compiler import StrategyCompiler

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
        strategy = StrategyCompiler.compile(StrategySpec.load(spec_path))

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
        with open(run_dir / "summary.json", encoding="utf-8") as handle:
            saved_summary = json.load(handle)
        assert saved_summary["n_states"] == 12.0


def test_backtest_config_from_cfg():
    """backtest_config_from_cfg bridges config system to BacktestConfig."""
    backtest = _load_backtest_module()

    cfg = {
        "backtest": {
            "initial_cash": 5e7,
            "seed": 99,
            "fee_model": "zero",
        },
    }
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
    )
    assert bc.symbol == "005930"
    assert bc.initial_cash == 5e7
    assert bc.seed == 99
    assert bc.fee_model == "zero"
    # defaults still filled
    assert bc.slicing_algo == "TWAP"


def test_backtest_config_from_cfg_with_overrides():
    """Keyword overrides take priority over config values."""
    backtest = _load_backtest_module()

    cfg = {"backtest": {"initial_cash": 5e7}}
    bc = backtest.backtest_config_from_cfg(
        cfg, symbol="005930", start_date="20260313",
        initial_cash=2e7,
    )
    assert bc.initial_cash == 2e7


def test_run_backtest_with_states_yaml_cfg():
    """yaml_cfg injects paths into run_backtest_with_states."""
    backtest = _load_backtest_module()

    with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
        data_dir = Path(data_tmp)
        symbol = "005930"
        date = "20260312"
        day_dir = data_dir / symbol / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _make_raw_csv(day_dir / "lob.csv", symbol=symbol, date=date)

        states = backtest.build_states_for_range(
            data_dir=data_dir, symbol=symbol, start_date=date,
        )

        output_dir = Path(output_tmp)
        spec_path = _write_test_spec(output_dir)

        from evaluation_orchestration.layer7_validation import BacktestConfig
        from strategy_block.strategy_specs.schema import StrategySpec
        from strategy_block.strategy_compiler.compiler import StrategyCompiler

        config = BacktestConfig(
            symbol=symbol, start_date="2026-03-12", end_date="2026-03-12",
            initial_cash=1e8, seed=123, compute_attribution=False,
            placement_style="aggressive",
        )
        strategy = StrategyCompiler.compile(StrategySpec.load(spec_path))

        yaml_cfg = {
            "paths": {
                "data_dir": str(data_dir),
                "outputs_dir": str(output_tmp),
            },
        }
        result = backtest.run_backtest_with_states(
            config=config, states=states,
            data_dir=str(data_dir), output_dir=str(output_tmp),
            strategy=strategy, yaml_cfg=yaml_cfg,
        )
        assert result.n_states == 12


def test_backtest_script_supports_h0stasp0_date_first_layout():
    backtest = _load_backtest_module()

    with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
        data_dir = Path(data_tmp)
        symbol = "005930"
        date = "20260312"
        day_dir = data_dir / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _make_h0stasp0_csv(day_dir / f"{symbol}.csv", symbol=symbol, date=date)

        states = backtest.build_states_for_range(
            data_dir=data_dir,
            symbol=symbol,
            start_date=date,
        )
        assert len(states) == 12
        assert states[0].timestamp == pd.Timestamp("2026-03-12 09:00:00")

        output_dir = Path(output_tmp)
        spec_path = _write_test_spec(output_dir)

        from evaluation_orchestration.layer7_validation import BacktestConfig
        from strategy_block.strategy_specs.schema import StrategySpec
        from strategy_block.strategy_compiler.compiler import StrategyCompiler

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
        strategy = StrategyCompiler.compile(StrategySpec.load(spec_path))

        result = backtest.run_backtest_with_states(
            config=config,
            states=states,
            data_dir=str(data_dir),
            output_dir=str(output_tmp),
            strategy=strategy,
        )
        assert result.n_states == 12
