"""
Adverse-selection threshold sensitivity experiment (500ms, delay=0).

Measures how child-order churn changes as adverse_selection_threshold_bps varies.

Usage:
    cd /home/dgu/tick/proj_rl_agent
    PYTHONPATH=src python scripts/internal/adhoc/run_adverse_selection_threshold_sensitivity.py

Output:
    outputs/benchmarks/adverse_selection_threshold_sensitivity.json
"""
from __future__ import annotations

import gc
import json
import resource
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data.layer0_data import MarketStateBuilder
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from evaluation_orchestration.layer7_validation.report_builder import ReportBuilder
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import get_paths, load_config

SYMBOL = "005930"
DATE = "20260313"
SPEC_PATH = PROJECT_ROOT / "strategies" / "examples" / "stateful_cooldown_momentum_v2.0.json"
RESAMPLE = "500ms"
MARKET_DATA_DELAY_MS = 0.0
THRESHOLDS_BPS = [10.0, 15.0, 20.0, 30.0]

_captured_parent_orders: list = []
_original_generate_reports = ReportBuilder.generate_reports


def _capturing_generate_reports(self, fills, parent_orders, **kwargs):
    _captured_parent_orders.clear()
    _captured_parent_orders.extend(parent_orders)
    return _original_generate_reports(self, fills=fills, parent_orders=parent_orders, **kwargs)


ReportBuilder.generate_reports = _capturing_generate_reports


class ThresholdPipelineRunner(PipelineRunner):
    """Script-local runner to inject threshold without changing engine semantics."""

    def __init__(self, *args, adverse_selection_threshold_bps: float, **kwargs) -> None:
        self._adverse_selection_threshold_bps = float(adverse_selection_threshold_bps)
        super().__init__(*args, **kwargs)

    def _setup_components(self, config: BacktestConfig) -> None:
        super()._setup_components(config)
        if self._cancel_replace is not None:
            self._cancel_replace.adverse_selection_threshold_bps = float(self._adverse_selection_threshold_bps)


def _date_fmt(date: str) -> str:
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _categorize_reason(reason: str) -> str:
    if reason.startswith("timeout"):
        return "timeout"
    if reason.startswith("replace:stale_price") or reason == "replace:stale_price":
        return "replace:stale_price"
    if reason.startswith("replace:"):
        return f"replace:{reason.split(':', 1)[1]}"
    if reason == "adverse_selection":
        return "adverse_selection"
    if reason.startswith("price_very_stale"):
        return "price_very_stale"
    if reason == "max_reprices_reached":
        return "max_reprices_reached"
    if reason == "micro_event_block":
        return "micro_event_block"
    return reason


def _analyze_parent_orders(parent_orders: list) -> dict:
    from execution_planning.layer3_order.order_types import OrderStatus

    total_children = 0
    total_cancels = 0
    total_fills_child = 0
    total_replacements = 0
    cancel_reasons: Counter = Counter()
    reprice_counts: Counter = Counter()
    child_lifetimes: list[float] = []
    per_parent: list[dict] = []

    for parent in parent_orders:
        p_children = len(parent.child_orders)
        p_cancels = 0
        p_fills = 0
        p_replacements = 0
        p_cancel_reasons: Counter = Counter()

        for child in parent.child_orders:
            total_children += 1
            rc = int(child.meta.get("reprice_count", 0))

            if child.status == OrderStatus.CANCELLED:
                total_cancels += 1
                p_cancels += 1
                reason = child.meta.get("cancel_reason", "unknown")
                category = _categorize_reason(reason)
                cancel_reasons[category] += 1
                p_cancel_reasons[category] += 1
                reprice_counts[rc] += 1
                if "replace:" in reason:
                    total_replacements += 1
                    p_replacements += 1
            elif child.status == OrderStatus.FILLED:
                total_fills_child += 1
                p_fills += 1

            if child.submitted_time is not None:
                end_time = (
                    child.cancel_time
                    if child.cancel_time is not None
                    else child.fill_time
                    if hasattr(child, "fill_time") and child.fill_time is not None
                    else None
                )
                if end_time is not None:
                    child_lifetimes.append((end_time - child.submitted_time).total_seconds())

        per_parent.append(
            {
                "parent_id": parent.order_id,
                "symbol": parent.symbol,
                "side": parent.side.name if hasattr(parent.side, "name") else str(parent.side),
                "total_qty": parent.total_qty,
                "filled_qty": parent.filled_qty,
                "n_children": p_children,
                "n_cancels": p_cancels,
                "n_fills": p_fills,
                "n_replacements": p_replacements,
                "cancel_reasons": dict(p_cancel_reasons),
            }
        )

    n_parents = len(parent_orders)
    children_per_parent = [pp["n_children"] for pp in per_parent]
    cancels_per_parent = [pp["n_cancels"] for pp in per_parent]

    return {
        "total_parents": n_parents,
        "total_children": total_children,
        "total_cancels": total_cancels,
        "total_fills_child": total_fills_child,
        "total_replacements": total_replacements,
        "cancel_reason_counts": dict(cancel_reasons),
        "cancel_reason_share": {
            k: round(v / total_cancels, 4) if total_cancels else 0.0
            for k, v in cancel_reasons.items()
        },
        "reprice_count_histogram": dict(reprice_counts),
        "children_per_parent": {
            "mean": round(mean(children_per_parent), 3) if children_per_parent else 0.0,
            "median": round(median(children_per_parent), 3) if children_per_parent else 0.0,
            "max": max(children_per_parent) if children_per_parent else 0,
            "min": min(children_per_parent) if children_per_parent else 0,
        },
        "cancels_per_parent": {
            "mean": round(mean(cancels_per_parent), 3) if cancels_per_parent else 0.0,
            "median": round(median(cancels_per_parent), 3) if cancels_per_parent else 0.0,
            "max": max(cancels_per_parent) if cancels_per_parent else 0,
        },
        "fills_per_parent": round(total_fills_child / n_parents, 6) if n_parents else 0.0,
        "replacements_per_parent": round(total_replacements / n_parents, 6) if n_parents else 0.0,
        "avg_child_lifetime_seconds": round(mean(child_lifetimes), 6) if child_lifetimes else 0.0,
        "median_child_lifetime_seconds": round(median(child_lifetimes), 6) if child_lifetimes else 0.0,
        "per_parent": per_parent,
    }


def _dominant_hotspot(per_parent: list[dict], total_children: int) -> dict:
    if not per_parent:
        return {
            "parent_id": None,
            "n_children": 0,
            "n_fills": 0,
            "n_cancels": 0,
            "share_of_children": 0.0,
        }
    top = max(per_parent, key=lambda x: x["n_children"])
    share = (float(top["n_children"]) / float(total_children)) if total_children > 0 else 0.0
    return {
        "parent_id": str(top["parent_id"]),
        "n_children": int(top["n_children"]),
        "n_fills": int(top["n_fills"]),
        "n_cancels": int(top["n_cancels"]),
        "share_of_children": round(share, 6),
    }


def _build_states(data_dir: str, symbol: str, date: str, resample: str):
    builder = MarketStateBuilder(data_dir=data_dir, resample_freq=resample)
    t0 = time.monotonic()
    states = builder.build_states_from_symbol_date(symbol=symbol, date=date, resample_freq=resample)
    return states, time.monotonic() - t0


def _run_one(*, threshold_bps: float, states: list, strategy_factory, data_dir: str, state_build_s: float) -> dict:
    config = BacktestConfig(
        symbol=SYMBOL,
        start_date=_date_fmt(DATE),
        end_date=_date_fmt(DATE),
        seed=42,
        market_data_delay_ms=MARKET_DATA_DELAY_MS,
        placement_style="aggressive",
        compute_attribution=False,
    )
    strategy = strategy_factory()
    gc.collect()
    t0 = time.monotonic()

    runner = ThresholdPipelineRunner(
        config=config,
        data_dir=data_dir,
        output_dir=None,
        strategy=strategy,
        adverse_selection_threshold_bps=threshold_bps,
    )
    result = runner.run(states)

    wall_s = time.monotonic() - t0
    summary = result.summary()
    timings = result.metadata.get("timings", {})
    lag_info = result.metadata.get("observation_lag", {})
    cancel_reasons_diag = result.metadata.get("cancel_reasons", {})
    child_analysis = _analyze_parent_orders(_captured_parent_orders[:])
    hotspot = _dominant_hotspot(child_analysis.get("per_parent", []), int(child_analysis["total_children"]))

    return {
        "adverse_selection_threshold_bps": float(threshold_bps),
        "symbol": SYMBOL,
        "date": DATE,
        "strategy_name": strategy.name,
        "resample": RESAMPLE,
        "market_data_delay_ms": float(MARKET_DATA_DELAY_MS),
        "canonical_tick_interval_ms": float(lag_info.get("canonical_tick_interval_ms", 0.0)),
        "state_count": int(result.n_states),
        "state_build_s": round(state_build_s, 6),
        "wall_clock_s": round(wall_s, 6),
        "loop_s": float(timings.get("loop_s", 0.0)),
        "total_s": float(timings.get("total_s", 0.0)),
        "peak_rss_mb": round(_peak_rss_mb(), 3),
        "signal_count": int(result.execution_report.n_parent_orders),
        "parent_order_count": int(result.execution_report.n_parent_orders),
        "child_order_count": int(result.execution_report.n_child_orders),
        "children_per_parent": float(child_analysis["children_per_parent"]["mean"]),
        "n_fills": int(result.n_fills),
        "cancel_rate": float(summary.get("cancel_rate", 0.0)),
        "avg_child_lifetime_seconds": float(child_analysis["avg_child_lifetime_seconds"]),
        "cancel_reason_counts": dict(cancel_reasons_diag.get("counts", {})),
        "cancel_reason_shares": dict(cancel_reasons_diag.get("shares", {})),
        "dominant_hotspot_parent": hotspot,
        "net_pnl": float(summary.get("net_pnl", 0.0)),
        "child_analysis": child_analysis,
    }


def _print_table(rows: list[dict]) -> None:
    print("\nThreshold sensitivity (500ms, delay=0)")
    print(
        f"{'thr_bps':>8} {'signals':>8} {'parents':>8} {'children':>10} "
        f"{'ch/parent':>10} {'fills':>8} {'cancel%':>9} {'avg_life_s':>11} "
        f"{'loop_s':>8} {'total_s':>8} {'net_pnl':>12}"
    )
    for row in rows:
        print(
            f"{row['adverse_selection_threshold_bps']:>8.1f} "
            f"{row['signal_count']:>8d} "
            f"{row['parent_order_count']:>8d} "
            f"{row['child_order_count']:>10d} "
            f"{row['children_per_parent']:>10.3f} "
            f"{row['n_fills']:>8d} "
            f"{row['cancel_rate']:>9.4f} "
            f"{row['avg_child_lifetime_seconds']:>11.4f} "
            f"{row['loop_s']:>8.2f} "
            f"{row['total_s']:>8.2f} "
            f"{row['net_pnl']:>12.1f}"
        )


def main() -> None:
    import logging

    logging.basicConfig(level=logging.WARNING)
    cfg = load_config()
    paths = get_paths(cfg)
    data_dir = paths["data_dir"]
    out_dir = PROJECT_ROOT / "outputs" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "adverse_selection_threshold_sensitivity.json"

    spec = StrategySpecV2.load(str(SPEC_PATH))

    def strategy_factory():
        return compile_strategy(spec)

    print("=" * 90)
    print("Adverse-selection threshold sensitivity")
    print("=" * 90)
    print(f"Symbol:   {SYMBOL}")
    print(f"Date:     {DATE}")
    print(f"Strategy: {spec.name}")
    print(f"Spec:     {SPEC_PATH}")
    print(f"Resample: {RESAMPLE}")
    print(f"Delay:    {MARKET_DATA_DELAY_MS:.1f}ms")
    print(f"Thresholds(bps): {THRESHOLDS_BPS}")
    print()

    print("  Warmup ... ", end="", flush=True)
    warm_states, _ = _build_states(data_dir, SYMBOL, DATE, "1s")
    warm_config = BacktestConfig(
        symbol=SYMBOL,
        start_date=_date_fmt(DATE),
        end_date=_date_fmt(DATE),
        seed=42,
        market_data_delay_ms=200.0,
        placement_style="aggressive",
        compute_attribution=False,
    )
    warm_runner = ThresholdPipelineRunner(
        config=warm_config,
        data_dir=data_dir,
        output_dir=None,
        strategy=strategy_factory(),
        adverse_selection_threshold_bps=10.0,
    )
    warm_runner.run(warm_states[:50])
    del warm_states
    gc.collect()
    print("done")

    states, build_s = _build_states(data_dir, SYMBOL, DATE, RESAMPLE)
    print(f"  Built {len(states)} states @ {RESAMPLE} ({build_s:.2f}s)")

    rows: list[dict] = []
    for threshold in THRESHOLDS_BPS:
        print(f"  threshold={threshold:.1f}bps ... ", end="", flush=True)
        row = _run_one(
            threshold_bps=threshold,
            states=states,
            strategy_factory=strategy_factory,
            data_dir=data_dir,
            state_build_s=build_s,
        )
        print(
            f"{row['total_s']:.2f}s  children={row['child_order_count']}  "
            f"ch/parent={row['children_per_parent']:.3f}  fills={row['n_fills']}"
        )
        rows.append(row)

    rows = sorted(rows, key=lambda x: x["adverse_selection_threshold_bps"])
    _print_table(rows)

    for row in rows:
        child_analysis = row.get("child_analysis", {})
        per_parent = child_analysis.get("per_parent", [])
        child_analysis["per_parent_summary"] = sorted(
            per_parent, key=lambda x: x.get("n_children", 0), reverse=True
        )[:20]
        if "per_parent" in child_analysis:
            del child_analysis["per_parent"]

    payload = {
        "experiment": {
            "name": "adverse_selection_threshold_sensitivity",
            "symbol": SYMBOL,
            "date": DATE,
            "strategy_spec_path": str(SPEC_PATH),
            "resample": RESAMPLE,
            "market_data_delay_ms": MARKET_DATA_DELAY_MS,
            "thresholds_bps": THRESHOLDS_BPS,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "runs": rows,
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nRaw results saved: {out_json}")


if __name__ == "__main__":
    main()
