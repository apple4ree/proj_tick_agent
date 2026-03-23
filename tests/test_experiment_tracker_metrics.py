"""BacktestResult.summary()가 필수 키를 포함하는지 검증한다."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for p in (PROJECT_ROOT, SRC_ROOT, SCRIPTS_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from evaluation_orchestration.layer7_validation.backtest_config import BacktestResult


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


# ── Keys that BacktestResult.summary() actually returns ──────────────

# Subset of keys that any consumer MUST be able to read
EXPECTED_BACKTEST_METRIC_KEYS = {
    "sharpe_ratio",
    "net_pnl",
    "n_fills",
    "max_drawdown",
    "fill_rate",
    "is_bps",
}


class TestBacktestMetricKeys:
    """backtest.py가 올바른 summary 키를 반환하는지 확인한다."""

    def test_summary_keys_exist_in_result(self):
        """BacktestResult.summary()에 필수 키가 존재하는지 확인한다."""
        backtest = _load_module("backtest")

        # Create a minimal spec JSON for the compiled strategy
        spec_json = {
            "name": "test_spec",
            "version": "1.0",
            "signal_rules": [
                {"feature": "order_imbalance", "operator": ">",
                 "threshold": 0.1, "score_contribution": 0.5}
            ],
            "exit_rules": [{"exit_type": "time_exit", "timeout_ticks": 100}],
        }

        with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
            data_dir = Path(data_tmp)
            symbol, date = "005930", "20260312"
            day_dir = data_dir / symbol / date
            day_dir.mkdir(parents=True, exist_ok=True)
            _make_raw_csv(day_dir / "lob.csv", symbol=symbol, date=date)

            # Write spec file
            spec_path = Path(output_tmp) / "test_spec.json"
            import json
            spec_path.write_text(json.dumps(spec_json), encoding="utf-8")

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
            assert not missing, f"Keys missing from BacktestResult.summary(): {missing}"

    def test_tracker_receives_nonzero_metrics(self):
        """summary에서 n_fills > 0, fill_rate > 0인지 확인한다."""
        backtest = _load_module("backtest")

        spec_json = {
            "name": "test_spec",
            "version": "1.0",
            "signal_rules": [
                {"feature": "order_imbalance", "operator": ">",
                 "threshold": 0.1, "score_contribution": 0.5}
            ],
            "exit_rules": [{"exit_type": "time_exit", "timeout_ticks": 100}],
        }

        with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
            data_dir = Path(data_tmp)
            symbol, date = "005930", "20260312"
            day_dir = data_dir / symbol / date
            day_dir.mkdir(parents=True, exist_ok=True)
            _make_raw_csv(day_dir / "lob.csv", symbol=symbol, date=date)

            spec_path = Path(output_tmp) / "test_spec.json"
            import json
            spec_path.write_text(json.dumps(spec_json), encoding="utf-8")

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

            assert summary["n_fills"] > 0, (
                f"n_fills should be > 0 but got {summary['n_fills']}"
            )
            assert summary["fill_rate"] > 0, (
                f"fill_rate should be > 0 but got {summary['fill_rate']}"
            )
