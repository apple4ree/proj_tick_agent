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
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    RiskPolicyV2,
    StrategySpecV2,
)


def _load_backtest_module():
    script_path = PROJECT_ROOT / "scripts" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_raw_csv(path: Path, symbol: str, date: str, n_steps: int = 12) -> None:
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


def _write_test_spec(output_dir: Path) -> Path:
    spec_json = {
        "spec_format": "v2",
        "name": "freeze_contract_spec",
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
    path = output_dir / "freeze_contract_spec.json"
    path.write_text(json.dumps(spec_json), encoding="utf-8")
    return path


def _invalid_spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="freeze_review_invalid",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                strength=ConstExpr(value=0.4),
            ),
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="partial_only",
                        priority=1,
                        condition=ComparisonExpr(feature="spread_bps", op=">", threshold=25.0),
                        action=ExitActionV2(type="reduce_position", reduce_fraction=0.5),
                    ),
                ],
            ),
        ],
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def test_summary_and_diagnostics_contract_freeze_keys_exist():
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
            resample_freq="1s",
        )

        output_dir = Path(output_tmp)
        spec_path = _write_test_spec(output_dir)
        strategy = compile_strategy(StrategySpecV2.load(spec_path))

        cfg = BacktestConfig(
            symbol=symbol,
            start_date="2026-03-12",
            end_date="2026-03-12",
            initial_cash=1e8,
            seed=123,
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
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        diagnostics = json.loads((run_dir / "realism_diagnostics.json").read_text(encoding="utf-8"))

        summary_required = {
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
            "configured_order_submit_ms",
            "configured_order_ack_ms",
            "configured_cancel_ms",
            "latency_alias_applied",
            "signal_count",
            "parent_order_count",
            "child_order_count",
            "net_pnl",
        }
        assert summary_required.issubset(summary.keys())

        diagnostics_sections = {
            "observation_lag",
            "decision_latency",
            "tick_time",
            "lifecycle",
            "queue",
            "latency",
            "cancel_reasons",
            "timings",
            "config_snapshot",
        }
        assert diagnostics_sections.issubset(diagnostics.keys())

        assert "canonical_tick_interval_ms" in diagnostics["tick_time"]
        assert "avg_decision_state_age_ms" in diagnostics["decision_latency"]
        assert "blocked_miss_count" in diagnostics["queue"]
        assert "ready_but_not_filled_count" in diagnostics["queue"]
        assert diagnostics["latency"]["order_ack_used_for_fill_gating"] is False
        assert "counts" in diagnostics["cancel_reasons"]
        assert "shares" in diagnostics["cancel_reasons"]


def test_review_pipeline_result_contract_freeze_keys_exist():
    result = run_pipeline(
        mode="auto-repair",
        spec=_invalid_spec(),
        backtest_environment={},
        client_mode="mock",
        backend="openai",
    )
    payload = result.model_dump()

    required = {
        "static_review",
        "llm_review",
        "repair_plan",
        "repair_applied",
        "final_static_review",
        "final_passed",
        "repaired_spec",
        "backtest_feedback",
        "feedback_aware_repair",
    }
    assert required.issubset(payload.keys())
