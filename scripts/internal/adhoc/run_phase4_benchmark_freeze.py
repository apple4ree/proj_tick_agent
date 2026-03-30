#!/usr/bin/env python
"""Phase 4 benchmark/freeze artifact generator.

Goal:
- Revalidate a minimal canonical benchmark matrix at current HEAD.
- Freeze key contracts/behaviors into benchmark artifacts.

Outputs:
- outputs/benchmarks/phase4_benchmark_freeze.json
- outputs/benchmarks/phase4_benchmark_freeze.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
import sys

for _p in (PROJECT_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from data.layer0_data import MarketStateBuilder, validate_resample_freq
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_review.v2.backtest_feedback import load_backtest_feedback
from strategy_block.strategy_review.v2.pipeline_v2 import run_pipeline
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    RiskPolicyV2,
    StrategySpecV2,
)
from utils.config import build_backtest_environment_context, load_config


CANONICAL_SUMMARY_FIELDS = [
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
]

CANONICAL_DIAGNOSTIC_FIELDS = {
    "observation_lag": [
        "configured_market_data_delay_ms",
        "avg_observation_staleness_ms",
        "effective_delay_ms",
        "resample_interval",
        "canonical_tick_interval_ms",
    ],
    "decision_latency": [
        "configured_decision_compute_ms",
        "decision_latency_enabled",
        "avg_decision_state_age_ms",
    ],
    "tick_time": [
        "canonical_tick_interval_ms",
        "resample_interval",
        "state_history_max_len",
        "strategy_runtime_lookback_ticks",
        "history_safety_buffer_ticks",
    ],
    "lifecycle": [
        "signal_count",
        "parent_order_count",
        "child_order_count",
        "n_fills",
        "cancel_rate",
        "avg_child_lifetime_seconds",
        "max_children_per_parent",
    ],
    "queue": [
        "queue_model",
        "queue_position_assumption",
        "queue_wait_ticks",
        "queue_wait_ms",
        "blocked_miss_count",
        "ready_but_not_filled_count",
        "maker_fill_ratio",
    ],
    "latency": [
        "configured_order_submit_ms",
        "configured_order_ack_ms",
        "configured_cancel_ms",
        "latency_alias_applied",
        "order_ack_used_for_fill_gating",
        "cancel_pending_count",
        "fills_before_cancel_effective_count",
    ],
    "cancel_reasons": ["counts", "shares"],
    "timings": ["setup_s", "loop_s", "report_s", "save_s", "total_s"],
    "config_snapshot": [
        "resample",
        "market_data_delay_ms",
        "decision_compute_ms",
        "latency_ms",
        "queue_model",
        "exchange_model",
        "placement_style",
        "slicing_algo",
    ],
}

REQUIRED_PLOTS = [
    "overview.png",
    "trade_timeline.png",
    "equity_risk.png",
    "realism_dashboard.png",
]


@dataclass(frozen=True)
class MatrixCase:
    label: str
    date: str
    resample: str
    market_data_delay_ms: float


def _json_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_test_lob_csv(path: Path, symbol: str, date: str, n_steps: int = 120) -> None:
    rows: list[dict[str, object]] = []
    start_ts = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:8]} 09:00:00")
    for step in range(n_steps):
        timestamp = start_ts + pd.Timedelta(seconds=step)
        mid_shift = 3 * step
        row: dict[str, object] = {
            "BSOP_DATE": date,
            "STCK_CNTG_HOUR": timestamp.strftime("%H%M%S"),
            "MKSC_SHRN_ISCD": symbol,
            "HOUR_CLS_CODE": "0",
        }
        for level in range(1, 11):
            row[f"BIDP{level}"] = 100000 + mid_shift - 10 * (level - 1)
            row[f"ASKP{level}"] = 100010 + mid_shift + 10 * (level - 1)
            row[f"BIDP_RSQN{level}"] = 2500 + 25 * level + 3 * step
            row[f"ASKP_RSQN{level}"] = 500 + 7 * level
            row[f"BIDP_RSQN_ICDC{level}"] = 0
            row[f"ASKP_RSQN_ICDC{level}"] = 0
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


def _build_states(data_dir: Path, symbol: str, date: str, resample: str) -> list[Any]:
    validate_resample_freq(resample)
    builder = MarketStateBuilder(
        data_dir=data_dir,
        trade_lookback=50,
        resample_freq=resample,
    )
    return builder.build_states_from_symbol_date(
        symbol=symbol,
        date=date,
        resample_freq=resample,
    )


def _extract_run_metrics(run_dir: Path) -> dict[str, Any]:
    summary = _json_load(run_dir / "summary.json")
    diagnostics = _json_load(run_dir / "realism_diagnostics.json")
    lifecycle = dict(diagnostics.get("lifecycle") or {})
    queue = dict(diagnostics.get("queue") or {})
    cancel_shares = dict(dict(diagnostics.get("cancel_reasons") or {}).get("shares") or {})
    timings = dict(diagnostics.get("timings") or {})

    parent_order_count = _safe_float(lifecycle.get("parent_order_count") or summary.get("parent_order_count"))
    child_order_count = _safe_float(lifecycle.get("child_order_count") or summary.get("child_order_count"))
    children_per_parent = _safe_float(lifecycle.get("children_per_parent"))
    if children_per_parent is None and parent_order_count not in (None, 0.0) and child_order_count is not None:
        children_per_parent = child_order_count / parent_order_count

    plots_dir = run_dir / "plots"
    plot_status = {name: (plots_dir / name).exists() for name in REQUIRED_PLOTS}

    return {
        "run_id": run_dir.name,
        "run_dir": str(run_dir.resolve()),
        "symbol": "005930",
        "date": summary.get("date") or None,
        "resample": summary.get("resample_interval"),
        "market_data_delay_ms": summary.get("configured_market_data_delay_ms"),
        "decision_compute_ms": summary.get("configured_decision_compute_ms"),
        "execution_policy_explicit": bool(summary.get("execution_policy_explicit", False)),
        "signal_count": lifecycle.get("signal_count", summary.get("signal_count")),
        "parent_order_count": parent_order_count,
        "child_order_count": child_order_count,
        "children_per_parent": children_per_parent,
        "n_fills": lifecycle.get("n_fills", summary.get("n_fills")),
        "cancel_rate": lifecycle.get("cancel_rate", summary.get("cancel_rate")),
        "avg_child_lifetime_seconds": lifecycle.get(
            "avg_child_lifetime_seconds",
            summary.get("avg_child_lifetime_seconds"),
        ),
        "max_children_per_parent": lifecycle.get("max_children_per_parent"),
        "queue_blocked_count": queue.get("queue_blocked_count"),
        "blocked_miss_count": queue.get("blocked_miss_count"),
        "queue_ready_count": queue.get("queue_ready_count"),
        "maker_fill_ratio": queue.get("maker_fill_ratio", summary.get("maker_fill_ratio")),
        "adverse_selection_share": cancel_shares.get("adverse_selection"),
        "timeout_share": cancel_shares.get("timeout"),
        "net_pnl": summary.get("net_pnl"),
        "total_commission": summary.get("total_commission"),
        "total_slippage": summary.get("total_slippage"),
        "total_impact": summary.get("total_impact"),
        "loop_s": timings.get("loop_s"),
        "total_s": timings.get("total_s"),
        "plots": {
            "all_required_present": all(plot_status.values()),
            "required": plot_status,
        },
    }


def _run_backtest(
    *,
    strategy: Any,
    states: list[Any],
    data_dir: Path,
    output_dir: Path,
    symbol: str,
    date: str,
    market_data_delay_ms: float,
) -> dict[str, Any]:
    config = BacktestConfig(
        symbol=symbol,
        start_date=f"{date[:4]}-{date[4:6]}-{date[6:8]}",
        end_date=f"{date[:4]}-{date[4:6]}-{date[6:8]}",
        initial_cash=1e8,
        seed=42,
        slicing_algo="TWAP",
        placement_style="spread_adaptive",
        latency_ms=100.0,
        fee_model="krx",
        impact_model="linear",
        exchange_model="partial_fill",
        queue_model="risk_adverse",
        queue_position_assumption=0.5,
        market_data_delay_ms=market_data_delay_ms,
        decision_compute_ms=0.0,
        compute_attribution=False,
    )
    result = PipelineRunner(
        config=config,
        data_dir=data_dir,
        output_dir=output_dir,
        strategy=strategy,
    ).run(states)
    run_dir = output_dir / result.run_id
    metrics = _extract_run_metrics(run_dir)
    metrics["date"] = date
    return metrics


def _missing_execution_policy_spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="phase4_missing_execution_policy",
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.2),
                strength=ConstExpr(value=0.4),
            )
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="stop_only",
                        priority=1,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("unrealized_pnl_bps"),
                            op="<=",
                            threshold=-25.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    )
                ],
            )
        ],
        execution_policy=None,
        risk_policy=RiskPolicyV2(max_position=100, inventory_cap=200),
    )


def _collect_single_symbol_matrix(output_dir: Path) -> list[dict[str, Any]]:
    cases = [
        MatrixCase(label="A", date="20260313", resample="1s", market_data_delay_ms=0.0),
        MatrixCase(label="B", date="20260313", resample="1s", market_data_delay_ms=200.0),
        MatrixCase(label="C", date="20260313", resample="500ms", market_data_delay_ms=0.0),
        MatrixCase(label="D", date="20260313", resample="500ms", market_data_delay_ms=200.0),
    ]
    spec = StrategySpecV2.load(PROJECT_ROOT / "strategies/examples/stateful_cooldown_momentum_v2.0.json")
    strategy = compile_strategy(spec)
    rows: list[dict[str, Any]] = []

    with TemporaryDirectory() as temp_data_dir:
        data_dir = Path(temp_data_dir)
        symbol = "005930"
        date = "20260313"
        day_dir = data_dir / symbol / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _build_test_lob_csv(day_dir / "lob.csv", symbol=symbol, date=date, n_steps=120)

        state_cache: dict[str, list[Any]] = {}
        for case in cases:
            if case.resample not in state_cache:
                state_cache[case.resample] = _build_states(
                    data_dir=data_dir,
                    symbol=symbol,
                    date=case.date,
                    resample=case.resample,
                )
            metrics = _run_backtest(
                strategy=strategy,
                states=state_cache[case.resample],
                data_dir=data_dir,
                output_dir=output_dir,
                symbol=symbol,
                date=case.date,
                market_data_delay_ms=case.market_data_delay_ms,
            )
            metrics["matrix_label"] = case.label
            rows.append(metrics)
    return rows


def _collect_review_variants(output_dir: Path) -> list[dict[str, Any]]:
    cfg = load_config(profile="smoke")
    env = build_backtest_environment_context(cfg)
    baseline_feedback = load_backtest_feedback(
        PROJECT_ROOT / "outputs/backtests/83b123e2-2755-499d-9091-52e96f69a51b"
    )
    base_spec = _missing_execution_policy_spec()

    rows: list[dict[str, Any]] = []
    with TemporaryDirectory() as temp_data_dir:
        data_dir = Path(temp_data_dir)
        symbol = "005930"
        date = "20260313"
        day_dir = data_dir / symbol / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _build_test_lob_csv(day_dir / "lob.csv", symbol=symbol, date=date, n_steps=90)
        states = _build_states(data_dir=data_dir, symbol=symbol, date=date, resample="1s")

        variants = [
            ("static", None),
            ("llm-review", None),
            ("auto-repair", None),
            ("auto-repair", baseline_feedback),
        ]
        labels = [
            "static_only",
            "llm_review",
            "auto_repair",
            "feedback_aware_auto_repair",
        ]

        for label, (mode, feedback) in zip(labels, variants):
            result = run_pipeline(
                mode=mode,
                spec=base_spec,
                backtest_environment=env,
                backtest_feedback=feedback,
                client_mode="mock",
                backend="openai",
            )

            final_spec = (
                StrategySpecV2.from_dict(result.repaired_spec)
                if result.repaired_spec is not None
                else base_spec
            )
            strategy = compile_strategy(final_spec)
            bt_metrics = _run_backtest(
                strategy=strategy,
                states=states,
                data_dir=data_dir,
                output_dir=output_dir,
                symbol=symbol,
                date=date,
                market_data_delay_ms=0.0,
            )
            bt_metrics.update(
                {
                    "variant": label,
                    "review_mode": mode,
                    "repair_applied": bool(result.repair_applied),
                    "feedback_aware_repair": bool(result.feedback_aware_repair),
                    "final_static_passed": bool(result.final_passed),
                    "llm_review_run": result.llm_review is not None,
                    "execution_policy_explicit": final_spec.execution_policy is not None,
                }
            )
            rows.append(bt_metrics)
    return rows


def _collect_historical_feedback_cases() -> dict[str, Any]:
    churn_run_id = "83b123e2-2755-499d-9091-52e96f69a51b"
    improved_run_id = "74322b9d-2096-4e1b-a1f0-ee263dc36666"
    churn_metrics = _extract_run_metrics(PROJECT_ROOT / "outputs/backtests" / churn_run_id)
    improved_metrics = _extract_run_metrics(PROJECT_ROOT / "outputs/backtests" / improved_run_id)
    delta_children = None
    if churn_metrics.get("child_order_count") is not None and improved_metrics.get("child_order_count") is not None:
        delta_children = improved_metrics["child_order_count"] - churn_metrics["child_order_count"]
    delta_cancel = None
    if churn_metrics.get("cancel_rate") is not None and improved_metrics.get("cancel_rate") is not None:
        delta_cancel = improved_metrics["cancel_rate"] - churn_metrics["cancel_rate"]

    return {
        "churn_heavy_run": churn_metrics,
        "improved_run": improved_metrics,
        "delta": {
            "child_order_count_delta": delta_children,
            "cancel_rate_delta": delta_cancel,
        },
    }


def _build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Phase 4 Benchmark Freeze")
    lines.append("")
    lines.append(f"- generated_at_utc: `{payload['generated_at_utc']}`")
    lines.append(f"- protocol: `{payload['protocol_version']}`")
    lines.append(f"- objective: `{payload['objective']}`")
    lines.append("")

    lines.append("## Canonical Matrix (Single-Symbol)")
    lines.append("")
    lines.append("| label | resample | delay_ms | signals | parents | children | fills | cancel_rate | children/parent | net_pnl | loop_s | total_s |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in payload["canonical_matrix"]["single_symbol_runs"]:
        lines.append(
            f"| {row.get('matrix_label','?')} | {row.get('resample')} | {row.get('market_data_delay_ms')} | "
            f"{_fmt(row.get('signal_count'))} | {_fmt(row.get('parent_order_count'))} | {_fmt(row.get('child_order_count'))} | "
            f"{_fmt(row.get('n_fills'))} | {_fmt(row.get('cancel_rate'))} | {_fmt(row.get('children_per_parent'))} | "
            f"{_fmt(row.get('net_pnl'))} | {_fmt(row.get('loop_s'))} | {_fmt(row.get('total_s'))} |"
        )
    lines.append("")

    lines.append("## Review/Repair Variants")
    lines.append("")
    lines.append("| variant | review_mode | llm_review | repair_applied | feedback_aware_repair | final_static_passed | execution_policy_explicit | backtest_run_id |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for row in payload["canonical_matrix"]["review_variants"]:
        lines.append(
            f"| {row.get('variant')} | {row.get('review_mode')} | {row.get('llm_review_run')} | "
            f"{row.get('repair_applied')} | {row.get('feedback_aware_repair')} | {row.get('final_static_passed')} | "
            f"{row.get('execution_policy_explicit')} | {row.get('run_id')} |"
        )
    lines.append("")

    hist = payload["feedback_loop_cases"]
    churn = hist["churn_heavy_run"]
    improved = hist["improved_run"]
    lines.append("## Historical Feedback Loop Case")
    lines.append("")
    lines.append("| case | run_id | children | cancel_rate | adverse_selection_share | timeout_share | loop_s | total_s |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| churn_heavy | {churn.get('run_id')} | {_fmt(churn.get('child_order_count'))} | {_fmt(churn.get('cancel_rate'))} | "
        f"{_fmt(churn.get('adverse_selection_share'))} | {_fmt(churn.get('timeout_share'))} | {_fmt(churn.get('loop_s'))} | {_fmt(churn.get('total_s'))} |"
    )
    lines.append(
        f"| improved | {improved.get('run_id')} | {_fmt(improved.get('child_order_count'))} | {_fmt(improved.get('cancel_rate'))} | "
        f"{_fmt(improved.get('adverse_selection_share'))} | {_fmt(improved.get('timeout_share'))} | {_fmt(improved.get('loop_s'))} | {_fmt(improved.get('total_s'))} |"
    )
    lines.append("")
    lines.append(
        f"- child_order_count_delta(improved - churn_heavy): `{_fmt(hist['delta'].get('child_order_count_delta'))}`"
    )
    lines.append(
        f"- cancel_rate_delta(improved - churn_heavy): `{_fmt(hist['delta'].get('cancel_rate_delta'))}`"
    )
    lines.append("")

    lines.append("## Contract Freeze")
    lines.append("")
    lines.append(f"- summary core fields: `{len(payload['freeze_contracts']['summary_core_fields'])}`")
    lines.append(f"- diagnostics sections: `{len(payload['freeze_contracts']['realism_diagnostics_core_fields'])}`")
    lines.append(
        "- review artifact fields: `static_review, llm_review, repair_plan, repair_applied, final_static_review, final_passed, repaired_spec, backtest_feedback, feedback_aware_repair`"
    )
    lines.append("")
    lines.append("## Known Limitations (Frozen)")
    lines.append("")
    for item in payload["known_limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    output_backtests = PROJECT_ROOT / "outputs/backtests"
    output_backtests.mkdir(parents=True, exist_ok=True)

    single_symbol_runs = _collect_single_symbol_matrix(output_backtests)
    review_variants = _collect_review_variants(output_backtests)
    historical_feedback = _collect_historical_feedback_cases()

    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_version": "phase4_benchmark_freeze_v1",
        "objective": "baseline revalidation + contract freeze for generation->review/repair->backtest loop",
        "canonical_matrix": {
            "single_symbol_runs": single_symbol_runs,
            "review_variants": review_variants,
        },
        "feedback_loop_cases": historical_feedback,
        "freeze_contracts": {
            "generation_prompt_contract": "canonical backtest constraint summary block injected",
            "review_prompt_contract": "canonical constraint summary + optional backtest feedback summary/json",
            "repair_prompt_contract": "failure-pattern-aware guidance with constrained ops only",
            "summary_core_fields": CANONICAL_SUMMARY_FIELDS,
            "realism_diagnostics_core_fields": CANONICAL_DIAGNOSTIC_FIELDS,
            "review_pipeline_result_fields": [
                "static_review",
                "llm_review",
                "repair_plan",
                "repair_applied",
                "final_static_review",
                "final_passed",
                "repaired_spec",
                "backtest_feedback",
                "feedback_aware_repair",
            ],
        },
        "behavioral_freeze": [
            "short-horizon spec without explicit execution_policy is treated as risky by static reviewer",
            "env-aware reviewer uses cadence and latency/tick ratio when scoring execution-policy churn risk",
            "feedback-aware repair changes operation priority by failure pattern",
            "backtest constraint prompt contract includes queue/latency/replace/cost friction",
        ],
        "regression_tolerance": {
            "exact_match_fields": [
                "prompt contract wording anchors",
                "summary/diagnostics/review artifact field presence",
                "feedback-aware repair priority ordering for same flags",
            ],
            "tolerated_numeric_drift_fields": [
                "net_pnl",
                "cost metrics",
                "runtime timings",
            ],
            "smoke_only_fields": [
                "plot image bytes",
                "run_id values",
            ],
        },
        "known_limitations": [
            "full staged replace state machine is deferred; replace remains minimal immediate model",
            "deep queue instrumentation beyond aggregate diagnostics is deferred",
            "post-backtest feedback loop remains aggregate-only (no raw CSV trace injection)",
            "full universe operational guarantee is not frozen in this phase",
            "live/replay LLM behavior can vary by provider/runtime availability; mock mode remains deterministic baseline",
        ],
    }

    json_out = PROJECT_ROOT / "outputs/benchmarks/phase4_benchmark_freeze.json"
    md_out = PROJECT_ROOT / "outputs/benchmarks/phase4_benchmark_freeze.md"
    _write_json(json_out, payload)
    _write_text(md_out, _build_markdown(payload))
    print(f"Wrote: {json_out}")
    print(f"Wrote: {md_out}")


if __name__ == "__main__":
    main()
