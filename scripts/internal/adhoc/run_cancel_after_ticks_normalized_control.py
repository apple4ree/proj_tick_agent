"""
Normalized cancel_after_ticks Control Experiment.

Compares baseline A (1s/d=0), C (500ms/d=0) against C_ctrl (500ms/d=0, cancel×2)
to measure cancel-timer sensitivity of the child-order explosion.

Only cancel_after_ticks is doubled (10→20 execution_policy, 4→8 adaptation).
cooldown_ticks, holding_ticks, max_reprices are unchanged.

Usage:
    cd /home/dgu/tick/proj_rl_agent
    PYTHONPATH=src python scripts/internal/adhoc/run_cancel_after_ticks_normalized_control.py

Output:
    outputs/benchmarks/cancel_after_ticks_normalized_control.json
    outputs/benchmarks/cancel_after_ticks_normalized_control_hotspots.json
    stdout: comparison tables
"""
from __future__ import annotations

import copy
import gc
import json
import resource
import signal as _signal
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import mean, median
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data.layer0_data import DataIngester, MarketStateBuilder
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from evaluation_orchestration.layer7_validation.report_builder import ReportBuilder
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import load_config, get_paths

# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------

SYMBOL = "005930"
DATE = "20260313"
SPEC_PATH = PROJECT_ROOT / "strategies" / "examples" / "stateful_cooldown_momentum_v2.0.json"

# -----------------------------------------------------------------------
# Parent order capture via monkey-patch
# -----------------------------------------------------------------------

_captured_parent_orders: list = []
_original_generate_reports = ReportBuilder.generate_reports


def _capturing_generate_reports(self, fills, parent_orders, **kwargs):
    """Intercept parent_orders from report generation without modifying engine."""
    _captured_parent_orders.clear()
    _captured_parent_orders.extend(parent_orders)
    return _original_generate_reports(self, fills=fills, parent_orders=parent_orders, **kwargs)


ReportBuilder.generate_reports = _capturing_generate_reports

# -----------------------------------------------------------------------
# Cancel-only normalized spec
# -----------------------------------------------------------------------


def _make_cancel_only_normalized_spec(src_path: Path, scale: int) -> Path:
    """Create a temp spec with ONLY cancel_after_ticks scaled by *scale*.

    Unchanged: cooldown_ticks, holding_ticks threshold, max_reprices, placement_mode.
    """
    with open(src_path, "r") as f:
        spec = json.load(f)

    spec["name"] = f"{spec['name']}_cancel_norm{scale}x"

    # execution_policy cancel_after_ticks
    ex = spec.get("execution_policy", {})
    if "cancel_after_ticks" in ex:
        old = ex["cancel_after_ticks"]
        ex["cancel_after_ticks"] = old * scale

    # adaptation rules cancel_after_ticks
    for rule in ex.get("adaptation_rules", []):
        ov = rule.get("override", {})
        if "cancel_after_ticks" in ov:
            old = ov["cancel_after_ticks"]
            ov["cancel_after_ticks"] = old * scale

    out_dir = PROJECT_ROOT / "outputs" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = NamedTemporaryFile(
        mode="w", suffix=".json", prefix="spec_cancel_norm_", delete=False,
        dir=str(out_dir),
    )
    json.dump(spec, tmp, indent=2)
    tmp.close()
    return Path(tmp.name)


# -----------------------------------------------------------------------
# Child-level analysis (reused from explosion analysis)
# -----------------------------------------------------------------------


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


def _analyze_parent_orders(parent_orders: list, resample: str) -> dict:
    from execution_planning.layer3_order.order_types import OrderStatus

    total_children = 0
    total_cancels = 0
    total_fills_child = 0
    total_replacements = 0
    cancel_reasons: Counter = Counter()
    reprice_counts: Counter = Counter()
    child_lifetimes: list[float] = []
    per_parent: list[dict] = []

    for p in parent_orders:
        p_children = len(p.child_orders)
        p_cancels = 0
        p_fills = 0
        p_replacements = 0
        p_cancel_reasons: Counter = Counter()
        p_lifetimes: list[float] = []

        for c in p.child_orders:
            total_children += 1
            rc = int(c.meta.get("reprice_count", 0))

            if c.status == OrderStatus.CANCELLED:
                total_cancels += 1
                p_cancels += 1
                reason = c.meta.get("cancel_reason", "unknown")
                cat = _categorize_reason(reason)
                cancel_reasons[cat] += 1
                p_cancel_reasons[cat] += 1
                reprice_counts[rc] += 1
                if "replace:" in reason:
                    total_replacements += 1
                    p_replacements += 1

            elif c.status == OrderStatus.FILLED:
                total_fills_child += 1
                p_fills += 1

            if c.submitted_time is not None:
                end_time = (
                    c.cancel_time if c.cancel_time is not None
                    else c.fill_time if hasattr(c, "fill_time") and c.fill_time is not None
                    else None
                )
                if end_time is not None:
                    lt = (end_time - c.submitted_time).total_seconds()
                    child_lifetimes.append(lt)
                    p_lifetimes.append(lt)

        per_parent.append({
            "parent_id": p.order_id,
            "symbol": p.symbol,
            "side": p.side.name if hasattr(p.side, "name") else str(p.side),
            "total_qty": p.total_qty,
            "filled_qty": p.filled_qty,
            "n_children": p_children,
            "n_cancels": p_cancels,
            "n_fills": p_fills,
            "n_replacements": p_replacements,
            "cancel_reasons": dict(p_cancel_reasons),
            "avg_lifetime_s": round(mean(p_lifetimes), 3) if p_lifetimes else 0,
        })

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
            k: round(v / total_cancels, 4) if total_cancels else 0
            for k, v in cancel_reasons.items()
        },
        "reprice_count_histogram": dict(reprice_counts),
        "children_per_parent": {
            "mean": round(mean(children_per_parent), 1) if children_per_parent else 0,
            "median": round(median(children_per_parent), 1) if children_per_parent else 0,
            "max": max(children_per_parent) if children_per_parent else 0,
            "min": min(children_per_parent) if children_per_parent else 0,
        },
        "cancels_per_parent": {
            "mean": round(mean(cancels_per_parent), 1) if cancels_per_parent else 0,
            "median": round(median(cancels_per_parent), 1) if cancels_per_parent else 0,
            "max": max(cancels_per_parent) if cancels_per_parent else 0,
        },
        "fills_per_parent": round(total_fills_child / n_parents, 2) if n_parents else 0,
        "replacements_per_parent": round(total_replacements / n_parents, 2) if n_parents else 0,
        "avg_child_lifetime_seconds": round(mean(child_lifetimes), 3) if child_lifetimes else 0,
        "median_child_lifetime_seconds": round(median(child_lifetimes), 3) if child_lifetimes else 0,
        "per_parent": per_parent,
    }


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _date_fmt(date: str) -> str:
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def _tick_duration_s(resample: str) -> float:
    return 0.5 if resample == "500ms" else 1.0


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout()


def _build_states(data_dir: str, symbol: str, date: str, resample: str):
    builder = MarketStateBuilder(data_dir=data_dir, resample_freq=resample)
    t0 = time.monotonic()
    states = builder.build_states_from_symbol_date(symbol=symbol, date=date, resample_freq=resample)
    return states, time.monotonic() - t0


# -----------------------------------------------------------------------
# Single run
# -----------------------------------------------------------------------

def _run_one(
    *,
    run_id: str,
    workflow: str,
    symbol: str,
    date: str,
    resample: str,
    delay_ms: float,
    states: list,
    strategy_factory,
    data_dir: str,
    state_build_s: float,
    timeout_s: int | None = None,
) -> dict | None:
    config = BacktestConfig(
        symbol=symbol,
        start_date=_date_fmt(date),
        end_date=_date_fmt(date),
        seed=42,
        market_data_delay_ms=delay_ms,
        placement_style="aggressive",
        compute_attribution=False,
    )
    strategy = strategy_factory()
    gc.collect()
    t0 = time.monotonic()

    old_handler = None
    if timeout_s is not None:
        old_handler = _signal.signal(_signal.SIGALRM, _timeout_handler)
        _signal.alarm(timeout_s)

    try:
        runner = PipelineRunner(config=config, data_dir=data_dir, output_dir=None, strategy=strategy)
        result = runner.run(states)
    except _Timeout:
        if old_handler is not None:
            _signal.alarm(0)
            _signal.signal(_signal.SIGALRM, old_handler)
        return None
    finally:
        if timeout_s is not None:
            _signal.alarm(0)
            if old_handler is not None:
                _signal.signal(_signal.SIGALRM, old_handler)

    wall = time.monotonic() - t0
    summary = result.summary()
    timings = result.metadata.get("timings", {})
    lag_info = result.metadata.get("observation_lag", {})

    tick_s = _tick_duration_s(resample)
    avg_hold_steps = summary.get("avg_holding_period", 0.0)

    child_analysis = _analyze_parent_orders(_captured_parent_orders[:], resample)

    return {
        "run_id": run_id,
        "workflow": workflow,
        "symbol": symbol,
        "date": date,
        "strategy": strategy.name,
        "resample": resample,
        "market_data_delay_ms": delay_ms,
        "canonical_tick_interval_ms": lag_info.get("canonical_tick_interval_ms", 0.0),
        "n_states": result.n_states,
        "wall_clock_s": round(wall, 3),
        "loop_s": timings.get("loop_s", 0.0),
        "total_pipeline_s": timings.get("total_s", 0.0),
        "state_build_s": round(state_build_s, 3),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
        "signal_count": result.execution_report.n_parent_orders,
        "parent_order_count": result.execution_report.n_parent_orders,
        "child_order_count": result.execution_report.n_child_orders,
        "n_fills": result.n_fills,
        "cancel_rate": summary.get("cancel_rate", 0.0),
        "avg_holding_period_steps": avg_hold_steps,
        "avg_holding_seconds": round(avg_hold_steps * tick_s, 3),
        "net_pnl": summary.get("net_pnl", 0.0),
        "fill_rate": summary.get("fill_rate", 0.0),
        "child_analysis": child_analysis,
    }


# -----------------------------------------------------------------------
# Print helpers
# -----------------------------------------------------------------------

def _print_basic_comparison(runs: dict[str, dict]) -> None:
    print("\n## Table 1: Baseline vs Control Basic Comparison")
    hdr = (
        f"{'id':>6} {'resamp':>6} {'delay':>5} {'tick_ms':>7} "
        f"{'loop_s':>8} {'signals':>7} {'parents':>7} {'children':>8} "
        f"{'fills':>6} {'cancel%':>8} {'ch/parent':>10} {'avg_life_s':>11} {'net_pnl':>12}"
    )
    print(hdr)
    for rid in ("A", "C", "C_ctrl", "B", "D", "D_ctrl"):
        r = runs.get(rid)
        if not r:
            continue
        ca = r["child_analysis"]
        print(
            f"{r['run_id']:>6} {r['resample']:>6} {r['market_data_delay_ms']:>5.0f} "
            f"{r['canonical_tick_interval_ms']:>7.0f} "
            f"{r['loop_s']:>8.1f} {r['signal_count']:>7} {r['parent_order_count']:>7} "
            f"{r['child_order_count']:>8} "
            f"{r['n_fills']:>6} {r['cancel_rate']:>8.4f} "
            f"{ca['children_per_parent']['mean']:>10.1f} "
            f"{ca['avg_child_lifetime_seconds']:>11.3f} "
            f"{r['net_pnl']:>12.0f}"
        )


def _print_cancel_reason_comparison(runs: dict[str, dict]) -> None:
    print("\n## Table 2: Cancel Reason Decomposition")
    all_reasons = set()
    for r in runs.values():
        all_reasons.update(r["child_analysis"]["cancel_reason_counts"].keys())
    all_reasons = sorted(all_reasons)

    run_order = [rid for rid in ("A", "C", "C_ctrl", "B", "D", "D_ctrl") if rid in runs]

    hdr = f"{'reason':<25}"
    for rid in run_order:
        hdr += f" {'cnt_' + rid:>10} {'%_' + rid:>8}"
    print(hdr)

    for reason in all_reasons:
        row = f"{reason:<25}"
        for rid in run_order:
            r = runs[rid]
            cnt = r["child_analysis"]["cancel_reason_counts"].get(reason, 0)
            share = r["child_analysis"]["cancel_reason_share"].get(reason, 0)
            row += f" {cnt:>10} {share:>8.1%}"
        print(row)

    row = f"{'TOTAL':<25}"
    for rid in run_order:
        tc = runs[rid]["child_analysis"]["total_cancels"]
        row += f" {tc:>10} {'100.0%':>8}"
    print(row)


def _print_parent_hotspots(runs: dict[str, dict]) -> None:
    print("\n## Table 3: Dominant Hotspot Parent Comparison")
    print(f"  {'run':>6} {'side':<5} {'children':>8} {'%total':>7} {'cancels':>8} "
          f"{'fills':>6} {'avg_life_s':>11} {'dominant_reason':<25}")

    for rid in ("A", "C", "C_ctrl", "B", "D", "D_ctrl"):
        r = runs.get(rid)
        if not r:
            continue
        parents = r["child_analysis"]["per_parent"]
        if not parents:
            continue
        total_ch = r["child_analysis"]["total_children"]
        top = max(parents, key=lambda x: x["n_children"])
        pct = top["n_children"] / total_ch * 100 if total_ch else 0
        dom_reason = max(top["cancel_reasons"], key=top["cancel_reasons"].get) if top["cancel_reasons"] else "—"
        print(
            f"  {rid:>6} {top['side']:<5} {top['n_children']:>8} {pct:>6.1f}% "
            f"{top['n_cancels']:>8} {top['n_fills']:>6} "
            f"{top['avg_lifetime_s']:>11.3f} {dom_reason:<25}"
        )


def _print_top10_hotspots(runs: dict[str, dict]) -> None:
    print("\n## Table 4: Top 10 Parents by Child Count")
    for rid in ("A", "C", "C_ctrl"):
        r = runs.get(rid)
        if not r:
            continue
        parents = r["child_analysis"]["per_parent"]
        by_children = sorted(parents, key=lambda x: x["n_children"], reverse=True)[:10]
        total_ch = r["child_analysis"]["total_children"]

        print(f"\n  [{rid}] {r['resample']}/d={r['market_data_delay_ms']:.0f} "
              f"(total={total_ch} children across {len(parents)} parents):")
        print(f"    {'rank':>4} {'side':<5} {'children':>8} {'%total':>7} {'cancels':>8} "
              f"{'fills':>6} {'avg_life_s':>11}")
        cum_pct = 0.0
        for i, pp in enumerate(by_children, 1):
            pct = pp["n_children"] / total_ch * 100 if total_ch else 0
            cum_pct += pct
            print(f"    {i:>4} {pp['side']:<5} {pp['n_children']:>8} {pct:>6.1f}% "
                  f"{pp['n_cancels']:>8} {pp['n_fills']:>6} "
                  f"{pp['avg_lifetime_s']:>11.3f}")
        print(f"    Top 10 cumulative: {cum_pct:.1f}%")


def _print_c_vs_ctrl_delta(runs: dict[str, dict]) -> None:
    c = runs.get("C")
    ctrl = runs.get("C_ctrl")
    if not c or not ctrl:
        return

    print("\n## Table 5: C vs C_ctrl Delta")
    ca_c = c["child_analysis"]
    ca_ctrl = ctrl["child_analysis"]

    rows = [
        ("signals", c["signal_count"], ctrl["signal_count"]),
        ("parents", c["parent_order_count"], ctrl["parent_order_count"]),
        ("children", c["child_order_count"], ctrl["child_order_count"]),
        ("children/parent", ca_c["children_per_parent"]["mean"], ca_ctrl["children_per_parent"]["mean"]),
        ("fills", c["n_fills"], ctrl["n_fills"]),
        ("cancel_rate", c["cancel_rate"], ctrl["cancel_rate"]),
        ("avg_child_lifetime_s", ca_c["avg_child_lifetime_seconds"], ca_ctrl["avg_child_lifetime_seconds"]),
        ("loop_s", c["loop_s"], ctrl["loop_s"]),
        ("net_pnl", c["net_pnl"], ctrl["net_pnl"]),
    ]

    print(f"  {'metric':<22} {'C':>14} {'C_ctrl':>14} {'delta':>14} {'ratio':>8}")
    for label, v_c, v_ctrl in rows:
        delta = v_ctrl - v_c
        ratio = v_ctrl / v_c if v_c != 0 else float("inf")
        print(f"  {label:<22} {v_c:>14.2f} {v_ctrl:>14.2f} {delta:>+14.2f} {ratio:>8.2f}×")


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main() -> None:
    import logging
    logging.basicConfig(level=logging.WARNING)

    cfg = load_config()
    paths = get_paths(cfg)
    data_dir = paths["data_dir"]

    out_dir = PROJECT_ROOT / "outputs" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load baseline spec
    spec_baseline = StrategySpecV2.load(str(SPEC_PATH))

    def baseline_factory():
        return compile_strategy(spec_baseline)

    # Create cancel-only normalized spec (×2)
    ctrl_spec_path = _make_cancel_only_normalized_spec(SPEC_PATH, 2)
    spec_ctrl = StrategySpecV2.load(str(ctrl_spec_path))

    def ctrl_factory():
        return compile_strategy(spec_ctrl)

    print("=" * 80)
    print("Normalized cancel_after_ticks Control Experiment")
    print("=" * 80)
    print(f"Symbol:    {SYMBOL}")
    print(f"Date:      {DATE}")
    print(f"Baseline:  {spec_baseline.name}")
    print(f"Control:   {spec_ctrl.name}")
    print(f"Data dir:  {data_dir}")
    print(f"Override:  cancel_after_ticks 10→20, adaptation 4→8")
    print()

    # Warmup
    print("  Warmup ... ", end="", flush=True)
    ws, _ = _build_states(data_dir, SYMBOL, DATE, "1s")
    wc = BacktestConfig(
        symbol=SYMBOL, start_date=_date_fmt(DATE), end_date=_date_fmt(DATE),
        seed=42, market_data_delay_ms=200.0,
        placement_style="aggressive", compute_attribution=False,
    )
    PipelineRunner(config=wc, data_dir=data_dir, output_dir=None,
                   strategy=baseline_factory()).run(ws[:50])
    del ws
    gc.collect()
    print("done")

    # Build states
    state_cache: dict[str, tuple[list, float]] = {}
    for resample in ("1s", "500ms"):
        states, build_s = _build_states(data_dir, SYMBOL, DATE, resample)
        state_cache[resample] = (states, build_s)
        print(f"  Built {len(states):>6} states @ {resample:>5}  ({build_s:.1f}s)")

    # Define runs: (run_id, resample, delay_ms, factory)
    run_configs = [
        ("A", "1s", 0.0, baseline_factory),
        ("C", "500ms", 0.0, baseline_factory),
        ("C_ctrl", "500ms", 0.0, ctrl_factory),
        ("B", "1s", 200.0, baseline_factory),
        ("D", "500ms", 200.0, baseline_factory),
        ("D_ctrl", "500ms", 200.0, ctrl_factory),
    ]

    all_runs: dict[str, dict] = {}

    for run_id, resample, delay_ms, factory in run_configs:
        states, build_s = state_cache[resample]
        label = f"cancel×2" if "ctrl" in run_id else "baseline"
        print(f"  [{run_id}] {resample}/delay={delay_ms:.0f} ({label}) ... ", end="", flush=True)
        m = _run_one(
            run_id=run_id, workflow="cancel_ctrl",
            symbol=SYMBOL, date=DATE, resample=resample, delay_ms=delay_ms,
            states=states, strategy_factory=factory,
            data_dir=data_dir, state_build_s=build_s,
        )
        if m:
            ca = m["child_analysis"]
            print(f"{m['total_pipeline_s']:.1f}s  "
                  f"children={m['child_order_count']}  "
                  f"ch/parent={ca['children_per_parent']['mean']:.1f}  "
                  f"avg_life={ca['avg_child_lifetime_seconds']:.3f}s")
            all_runs[run_id] = m
        else:
            print("TIMEOUT")

    # Print tables
    _print_basic_comparison(all_runs)
    _print_cancel_reason_comparison(all_runs)
    _print_parent_hotspots(all_runs)
    _print_top10_hotspots(all_runs)
    _print_c_vs_ctrl_delta(all_runs)

    # Save hotspot data
    hotspot_data = {}
    for rid in ("A", "C", "C_ctrl"):
        if rid in all_runs:
            parents = all_runs[rid]["child_analysis"]["per_parent"]
            hotspot_data[rid] = sorted(parents, key=lambda x: x["n_children"], reverse=True)[:20]

    hotspot_path = out_dir / "cancel_after_ticks_normalized_control_hotspots.json"
    hotspot_path.write_text(json.dumps(hotspot_data, indent=2, default=str), encoding="utf-8")
    print(f"\nHotspot data saved: {hotspot_path}")

    # Strip per_parent for main JSON (too large)
    for rid, r in all_runs.items():
        ca = r["child_analysis"]
        ca["per_parent_summary"] = sorted(
            ca["per_parent"], key=lambda x: x["n_children"], reverse=True
        )[:20]
        del ca["per_parent"]

    out_json = out_dir / "cancel_after_ticks_normalized_control.json"
    out_json.write_text(json.dumps(all_runs, indent=2, default=str), encoding="utf-8")
    print(f"Raw results saved: {out_json}")

    # Clean up temp spec
    try:
        ctrl_spec_path.unlink()
    except OSError:
        pass

    print("\nDone.")


if __name__ == "__main__":
    main()
