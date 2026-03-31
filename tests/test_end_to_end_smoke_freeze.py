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

from evaluation_orchestration.layer7_validation import BacktestConfig
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_review.v2.pipeline_v2 import run_pipeline
from strategy_block.strategy_generation.generator import StrategyGenerator
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import build_backtest_environment_context, load_config


def _load_backtest_module():
    script_path = PROJECT_ROOT / "scripts" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_raw_csv(path: Path, symbol: str, date: str, n_steps: int = 16) -> None:
    rows = []
    start_ts = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:8]} 09:00:00")
    for step in range(n_steps):
        timestamp = start_ts + pd.Timedelta(seconds=step)
        mid_shift = 5 * step
        row = {
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


def test_generation_review_backtest_smoke_freeze_path():
    cfg = load_config(profile="smoke")
    env_context = build_backtest_environment_context(cfg)

    generator = StrategyGenerator(
        backend="template",
        mode="mock",
        latency_ms=100.0,
        backtest_environment=env_context,
    )
    spec, trace = generator.generate(research_goal="order imbalance momentum", n_ideas=1, idea_index=0)
    assert trace.get("static_review_passed") is True

    pipeline_result = run_pipeline(
        mode="auto-repair",
        spec=spec,
        backtest_environment=env_context,
        client_mode="mock",
        backend="openai",
    )
    assert pipeline_result.final_passed is True

    final_spec = (
        StrategySpecV2.from_dict(pipeline_result.repaired_spec)
        if pipeline_result.repaired_spec is not None
        else spec
    )

    backtest = _load_backtest_module()
    with TemporaryDirectory() as data_tmp, TemporaryDirectory() as output_tmp:
        data_dir = Path(data_tmp)
        symbol = "005930"
        date = "20260313"
        day_dir = data_dir / symbol / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _make_raw_csv(day_dir / "lob.csv", symbol=symbol, date=date)

        states = backtest.build_states_for_range(
            data_dir=data_dir,
            symbol=symbol,
            start_date=date,
            resample_freq="1s",
        )

        strategy = compile_strategy(final_spec)
        cfg = BacktestConfig(
            symbol=symbol,
            start_date="2026-03-13",
            end_date="2026-03-13",
            initial_cash=1e8,
            seed=42,
            slicing_algo="TWAP",
            placement_style="spread_adaptive",
            latency_ms=100.0,
            fee_model="krx",
            exchange_model="partial_fill",
            queue_model="prob_queue",
            queue_position_assumption=0.5,
            market_data_delay_ms=0.0,
            decision_compute_ms=0.0,
            compute_attribution=False,
        )
        result = backtest.run_backtest_with_states(
            config=cfg,
            states=states,
            data_dir=str(data_dir),
            output_dir=str(output_tmp),
            strategy=strategy,
        )
        run_dir = Path(output_tmp) / result.run_id

        assert (run_dir / "summary.json").exists()
        assert (run_dir / "realism_diagnostics.json").exists()

        plots_dir = run_dir / "plots"
        required_plots = {
            "dashboard.png",
            "intraday_cumulative_profit.png",
            "trade_timeline.png",
        }
        for name in required_plots:
            assert (plots_dir / name).exists()

        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        assert "child_order_count" in summary
