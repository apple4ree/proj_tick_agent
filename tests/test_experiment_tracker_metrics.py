"""BacktestResult.summary() key integrity with v2 strategy specs."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for p in (PROJECT_ROOT, SRC_ROOT, SCRIPTS_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load_module(name: str):
    script_path = SCRIPTS_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_raw_csv(path: Path, symbol: str, date: str, n_steps: int = 12) -> None:
    rows: list[dict] = []
    start_ts = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:8]} 09:00:00")
    for step in range(n_steps):
        timestamp = start_ts + pd.Timedelta(seconds=step)
        mid_shift = 5 * step
        row: dict = {
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


EXPECTED_BACKTEST_METRIC_KEYS = {
    "sharpe_ratio",
    "net_pnl",
    "n_fills",
    "max_drawdown",
    "fill_rate",
    "is_bps",
}


def _v2_spec_json() -> dict:
    return {
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


def test_summary_keys_exist_in_result():
    backtest = _load_module("backtest")

    with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
        data_dir = Path(data_tmp)
        symbol, date = "005930", "20260312"
        day_dir = data_dir / symbol / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _make_raw_csv(day_dir / "lob.csv", symbol=symbol, date=date)

        spec_path = Path(output_tmp) / "test_spec_v2.json"
        spec_path.write_text(json.dumps(_v2_spec_json()), encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            config=None, symbol=symbol, start_date=date, end_date=None,
            data_dir=str(data_dir), resample=None, trade_lookback=100,
            initial_cash=1e8, seed=123, slicing_algo="TWAP",
            placement_style="aggressive", latency_ms=1.0,
            fee_model="krx", impact_model="linear",
            no_attribution=True, output_dir=str(output_tmp),
            print_summary_only=True,
            spec=str(spec_path),
        )
        result = backtest.run_backtest(args)
        summary = result.summary()

        missing = EXPECTED_BACKTEST_METRIC_KEYS - set(summary.keys())
        assert not missing


def test_tracker_receives_nonzero_metrics():
    backtest = _load_module("backtest")

    with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
        data_dir = Path(data_tmp)
        symbol, date = "005930", "20260312"
        day_dir = data_dir / symbol / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _make_raw_csv(day_dir / "lob.csv", symbol=symbol, date=date)

        spec_path = Path(output_tmp) / "test_spec_v2.json"
        spec_path.write_text(json.dumps(_v2_spec_json()), encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            config=None, symbol=symbol, start_date=date, end_date=None,
            data_dir=str(data_dir), resample=None, trade_lookback=100,
            initial_cash=1e8, seed=123, slicing_algo="TWAP",
            placement_style="aggressive", latency_ms=1.0,
            fee_model="krx", impact_model="linear",
            no_attribution=True, output_dir=str(output_tmp),
            print_summary_only=True,
            spec=str(spec_path),
        )

        result = backtest.run_backtest(args)
        summary = result.summary()

        assert summary["n_fills"] > 0
        assert summary["fill_rate"] > 0
