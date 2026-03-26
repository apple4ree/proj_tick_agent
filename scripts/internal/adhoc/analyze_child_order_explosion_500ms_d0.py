"""
Child Order Explosion Analysis: 500ms / delay=0.

Instruments PipelineRunner to capture per-child cancel reasons, reprice counts,
and per-parent hotspot data. Compares A (1s/d=0) vs C (500ms/d=0) to identify
the root cause of the ~7.6× child order explosion.

Usage:
    cd /home/dgu/tick/proj_rl_agent
    PYTHONPATH=src python scripts/internal/adhoc/analyze_child_order_explosion_500ms_d0.py

Output:
    outputs/benchmarks/child_order_explosion_500ms_d0.json
    outputs/benchmarks/child_order_explosion_500ms_d0_hotspots.json
    stdout: analysis tables
"""
from __future__ import annotations

import gc
import json
import resource
import signal as _signal
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean, median

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
MAX_UNIVERSE_SYMBOLS = 5
PER_SYMBOL_TIMEOUT_S = 180

COMBOS = [
    ("A", "1s", 0.0),
    ("B", "1s", 200.0),
    ("C", "500ms", 0.0),
    ("D", "500ms", 200.0),
]

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


# Install monkey-patch
ReportBuilder.generate_reports = _capturing_generate_reports


# -----------------------------------------------------------------------
# Child-level analysis
# -----------------------------------------------------------------------

def _analyze_parent_orders(parent_orders: list, resample: str) -> dict:
    """Extract child-level metrics from captured parent orders."""
    from execution_planning.layer3_order.order_types import OrderStatus

    tick_s = 0.5 if resample == "500ms" else 1.0

    total_children = 0
    total_cancels = 0
    total_fills_child = 0
    total_replacements = 0
    cancel_reasons: Counter = Counter()
    reprice_counts: Counter = Counter()  # histogram of reprice_count at cancel
    child_lifetimes: list[float] = []

    per_parent: list[dict] = []

    for p in parent_orders:
        p_children = len(p.child_orders)
        p_cancels = 0
        p_fills = 0
        p_replacements = 0
        p_cancel_reasons: Counter = Counter()

        for c in p.child_orders:
            total_children += 1
            rc = int(c.meta.get("reprice_count", 0))

            if c.status == OrderStatus.CANCELLED:
                total_cancels += 1
                p_cancels += 1
                reason = c.meta.get("cancel_reason", "unknown")
                # Normalize reason categories
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

            # Compute child lifetime
            if c.submitted_time is not None:
                end_time = c.cancel_time if c.cancel_time is not None else c.fill_time if hasattr(c, "fill_time") and c.fill_time is not None else None
                if end_time is not None:
                    lifetime_s = (end_time - c.submitted_time).total_seconds()
                    child_lifetimes.append(lifetime_s)

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
        })

    # Aggregate
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


def _categorize_reason(reason: str) -> str:
    """Map raw cancel_reason string to a normalized category."""
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
# Single run with child analysis
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

    # Analyze captured parent orders
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
        "avg_observation_staleness_ms": lag_info.get("avg_observation_staleness_ms", 0.0),
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
        # Child-level analysis
        "child_analysis": child_analysis,
    }


# -----------------------------------------------------------------------
# Print helpers
# -----------------------------------------------------------------------

def _print_basic_comparison(runs: dict[str, dict]) -> None:
    print("\n## Table 1: A vs C Basic Comparison")
    hdr = (
        f"{'id':>3} {'resamp':>6} {'delay':>5} {'tick_ms':>7} {'states':>7} "
        f"{'loop_s':>8} {'signals':>7} {'parents':>7} {'children':>8} "
        f"{'fills':>6} {'cancel%':>8} {'ch/parent':>10} {'avg_life_s':>11}"
    )
    print(hdr)
    for rid in ("A", "C", "B", "D"):
        r = runs.get(rid)
        if not r:
            continue
        ca = r["child_analysis"]
        print(
            f"{r['run_id']:>3} {r['resample']:>6} {r['market_data_delay_ms']:>5.0f} "
            f"{r['canonical_tick_interval_ms']:>7.0f} {r['n_states']:>7} "
            f"{r['loop_s']:>8.1f} {r['signal_count']:>7} {r['parent_order_count']:>7} "
            f"{r['child_order_count']:>8} "
            f"{r['n_fills']:>6} {r['cancel_rate']:>8.4f} "
            f"{ca['children_per_parent']['mean']:>10.1f} "
            f"{ca['avg_child_lifetime_seconds']:>11.3f}"
        )


def _print_cancel_reason_comparison(runs: dict[str, dict]) -> None:
    print("\n## Table 2: Cancel Reason Decomposition")

    # Collect all reasons across runs
    all_reasons = set()
    for r in runs.values():
        all_reasons.update(r["child_analysis"]["cancel_reason_counts"].keys())
    all_reasons = sorted(all_reasons)

    hdr = f"{'reason':<30}"
    for rid in ("A", "C", "B", "D"):
        if rid in runs:
            hdr += f" {'cnt_' + rid:>8} {'%_' + rid:>7}"
    if "A" in runs and "C" in runs:
        hdr += f" {'C/A':>6}"
    print(hdr)

    for reason in all_reasons:
        row = f"{reason:<30}"
        counts = {}
        for rid in ("A", "C", "B", "D"):
            r = runs.get(rid)
            if not r:
                continue
            cnt = r["child_analysis"]["cancel_reason_counts"].get(reason, 0)
            share = r["child_analysis"]["cancel_reason_share"].get(reason, 0)
            counts[rid] = cnt
            row += f" {cnt:>8} {share:>7.1%}"
        if "A" in counts and "C" in counts and counts["A"] > 0:
            row += f" {counts['C'] / counts['A']:>6.1f}×"
        elif "A" in counts and "C" in counts:
            row += f" {'∞':>6}"
        print(row)

    # Totals
    row = f"{'TOTAL':<30}"
    for rid in ("A", "C", "B", "D"):
        r = runs.get(rid)
        if not r:
            continue
        tc = r["child_analysis"]["total_cancels"]
        row += f" {tc:>8} {'100.0%':>7}"
    if "A" in runs and "C" in runs:
        a_tc = runs["A"]["child_analysis"]["total_cancels"]
        c_tc = runs["C"]["child_analysis"]["total_cancels"]
        ratio = c_tc / a_tc if a_tc else float("inf")
        row += f" {ratio:>6.1f}×"
    print(row)


def _print_reprice_histogram(runs: dict[str, dict]) -> None:
    print("\n## Table 3: Reprice Count at Cancel Time")
    for rid in ("A", "C"):
        r = runs.get(rid)
        if not r:
            continue
        hist = r["child_analysis"]["reprice_count_histogram"]
        total = sum(hist.values())
        print(f"  [{rid}] {r['resample']}/d={r['market_data_delay_ms']:.0f}:")
        for k in sorted(hist.keys(), key=int):
            pct = hist[k] / total * 100 if total else 0
            print(f"    reprice_count={k}: {hist[k]:>6} ({pct:.1f}%)")


def _print_parent_hotspots(runs: dict[str, dict]) -> None:
    print("\n## Table 4: Parent Hotspot Analysis (Top 10 by child count)")
    for rid in ("A", "C"):
        r = runs.get(rid)
        if not r:
            continue
        parents = r["child_analysis"]["per_parent"]
        by_children = sorted(parents, key=lambda x: x["n_children"], reverse=True)[:10]
        total_children = r["child_analysis"]["total_children"]

        print(f"\n  [{rid}] {r['resample']}/d={r['market_data_delay_ms']:.0f} "
              f"(total={total_children} children across {len(parents)} parents):")
        print(f"    {'rank':>4} {'side':<5} {'children':>8} {'%total':>7} {'cancels':>8} "
              f"{'fills':>6} {'replacements':>12}")
        cum_pct = 0.0
        for i, pp in enumerate(by_children, 1):
            pct = pp["n_children"] / total_children * 100 if total_children else 0
            cum_pct += pct
            print(f"    {i:>4} {pp['side']:<5} {pp['n_children']:>8} {pct:>6.1f}% "
                  f"{pp['n_cancels']:>8} {pp['n_fills']:>6} {pp['n_replacements']:>12}")
        print(f"    Top 10 cumulative: {cum_pct:.1f}%")


def _print_lifecycle_ratios(runs: dict[str, dict]) -> None:
    print("\n## Table 5: Lifecycle Ratios")
    hdr = (
        f"{'id':>3} {'ch/parent':>10} {'cancel/parent':>14} {'fill/parent':>11} "
        f"{'repl/parent':>12} {'cancel/child':>13} {'repl/child':>11}"
    )
    print(hdr)
    for rid in ("A", "C", "B", "D"):
        r = runs.get(rid)
        if not r:
            continue
        ca = r["child_analysis"]
        n_parents = ca["total_parents"]
        n_children = ca["total_children"]
        n_cancels = ca["total_cancels"]
        n_repls = ca["total_replacements"]
        n_fills = ca["total_fills_child"]

        cancel_per_child = n_cancels / n_children if n_children else 0
        repl_per_child = n_repls / n_children if n_children else 0

        print(
            f"{rid:>3} {ca['children_per_parent']['mean']:>10.1f} "
            f"{ca['cancels_per_parent']['mean']:>14.1f} "
            f"{ca['fills_per_parent']:>11.2f} "
            f"{ca['replacements_per_parent']:>12.2f} "
            f"{cancel_per_child:>13.4f} "
            f"{repl_per_child:>11.4f}"
        )


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

    spec = StrategySpecV2.load(str(SPEC_PATH))
    def strategy_factory():
        return compile_strategy(spec)

    print("=" * 80)
    print("Child Order Explosion Analysis: 500ms / delay=0")
    print("=" * 80)
    print(f"Symbol:   {SYMBOL}")
    print(f"Date:     {DATE}")
    print(f"Strategy: {spec.name}")
    print(f"Data dir: {data_dir}")
    print()

    all_output: dict = {}

    # ==================================================================
    # Phase 1: Single-Symbol A/B/C/D
    # ==================================================================
    print("=" * 60)
    print("Phase 1: Single-Symbol Analysis (005930)")
    print("=" * 60)

    # Warmup
    print("  Warmup ... ", end="", flush=True)
    ws, _ = _build_states(data_dir, SYMBOL, DATE, "1s")
    wc = BacktestConfig(
        symbol=SYMBOL, start_date=_date_fmt(DATE), end_date=_date_fmt(DATE),
        seed=42, market_data_delay_ms=200.0,
        placement_style="aggressive", compute_attribution=False,
    )
    PipelineRunner(config=wc, data_dir=data_dir, output_dir=None,
                   strategy=strategy_factory()).run(ws[:50])
    del ws
    gc.collect()
    print("done")

    # Build states
    state_cache: dict[str, tuple[list, float]] = {}
    for resample in ("1s", "500ms"):
        states, build_s = _build_states(data_dir, SYMBOL, DATE, resample)
        state_cache[resample] = (states, build_s)
        print(f"  Built {len(states):>6} states @ {resample:>5}  ({build_s:.1f}s)")

    # Run 4 combos
    single_runs: dict[str, dict] = {}
    for run_id, resample, delay_ms in COMBOS:
        states, build_s = state_cache[resample]
        print(f"  [{run_id}] {resample}/delay={delay_ms:.0f} ... ", end="", flush=True)
        m = _run_one(
            run_id=run_id, workflow="single-symbol",
            symbol=SYMBOL, date=DATE, resample=resample, delay_ms=delay_ms,
            states=states, strategy_factory=strategy_factory,
            data_dir=data_dir, state_build_s=build_s,
        )
        if m:
            ca = m["child_analysis"]
            print(f"{m['total_pipeline_s']:.1f}s  "
                  f"children={m['child_order_count']}  "
                  f"ch/parent={ca['children_per_parent']['mean']:.1f}  "
                  f"avg_life={ca['avg_child_lifetime_seconds']:.3f}s")
            single_runs[run_id] = m
        else:
            print("TIMEOUT")

    # Print analysis tables
    _print_basic_comparison(single_runs)
    _print_cancel_reason_comparison(single_runs)
    _print_reprice_histogram(single_runs)
    _print_parent_hotspots(single_runs)
    _print_lifecycle_ratios(single_runs)

    # Strip per_parent detail for JSON output (too large)
    for rid, r in single_runs.items():
        r["child_analysis"]["per_parent_summary"] = r["child_analysis"]["per_parent"][:20]
        del r["child_analysis"]["per_parent"]

    all_output["single_symbol"] = single_runs

    # ==================================================================
    # Phase 2: Universe Corroboration
    # ==================================================================
    print("\n" + "=" * 60)
    print("Phase 2: Universe Corroboration")
    print("=" * 60)

    ingester = DataIngester(data_dir)
    usable = [s for s in sorted(ingester.list_symbols()) if DATE in ingester.list_dates(s)]
    if len(usable) > MAX_UNIVERSE_SYMBOLS:
        universe = usable[:MAX_UNIVERSE_SYMBOLS]
    else:
        universe = usable
    print(f"  Universe: {len(universe)} symbols")
    print(f"  Symbols: {universe}")

    # Only run A and C for universe (the explosion comparison)
    univ_combos = [("A", "1s", 0.0), ("C", "500ms", 0.0)]
    univ_per_symbol: dict[str, dict] = {}  # symbol → {run_id → metrics}

    for run_id, resample, delay_ms in univ_combos:
        print(f"\n  [{run_id}] {resample}/delay={delay_ms:.0f}:")
        for sym in universe:
            print(f"    {sym} ... ", end="", flush=True)
            try:
                sts, bld = _build_states(data_dir, sym, DATE, resample)
            except Exception as e:
                print(f"BUILD_FAIL: {e}")
                continue
            if not sts:
                print("NO_DATA")
                continue

            m = _run_one(
                run_id=run_id, workflow="universe-per-sym",
                symbol=sym, date=DATE, resample=resample, delay_ms=delay_ms,
                states=sts, strategy_factory=strategy_factory,
                data_dir=data_dir, state_build_s=bld,
                timeout_s=PER_SYMBOL_TIMEOUT_S,
            )
            if m is None:
                print("TIMEOUT")
                if sym not in univ_per_symbol:
                    univ_per_symbol[sym] = {}
                univ_per_symbol[sym][run_id] = {"status": "timeout"}
                continue

            ca = m["child_analysis"]
            print(f"{m['loop_s']:.1f}s  "
                  f"children={m['child_order_count']}  "
                  f"ch/parent={ca['children_per_parent']['mean']:.1f}")

            if sym not in univ_per_symbol:
                univ_per_symbol[sym] = {}
            # Store slim summary for universe
            univ_per_symbol[sym][run_id] = {
                "status": "ok",
                "resample": resample,
                "signal_count": m["signal_count"],
                "child_order_count": m["child_order_count"],
                "n_fills": m["n_fills"],
                "cancel_rate": m["cancel_rate"],
                "loop_s": m["loop_s"],
                "children_per_parent_mean": ca["children_per_parent"]["mean"],
                "cancel_reason_counts": ca["cancel_reason_counts"],
                "avg_child_lifetime_seconds": ca["avg_child_lifetime_seconds"],
            }

    # Print universe summary
    print("\n## Universe Corroboration: Per-Symbol Child Count")
    print(f"  {'symbol':>8} {'A_children':>11} {'C_children':>11} {'ratio':>7} {'A_status':>9} {'C_status':>9}")
    for sym in universe:
        sd = univ_per_symbol.get(sym, {})
        a = sd.get("A", {})
        c = sd.get("C", {})
        a_ch = a.get("child_order_count", 0) if a.get("status") == "ok" else 0
        c_ch = c.get("child_order_count", 0) if c.get("status") == "ok" else 0
        ratio = c_ch / a_ch if a_ch > 0 else float("inf") if c_ch > 0 else 0
        a_st = a.get("status", "—")
        c_st = c.get("status", "—")
        ratio_s = f"{ratio:.1f}×" if ratio != float("inf") else "∞"
        print(f"  {sym:>8} {a_ch:>11} {c_ch:>11} {ratio_s:>7} {a_st:>9} {c_st:>9}")

    all_output["universe"] = univ_per_symbol

    # ==================================================================
    # Save hotspots
    # ==================================================================
    hotspot_data = {}
    for rid in ("A", "C"):
        if rid in single_runs:
            hotspot_data[rid] = single_runs[rid].get("child_analysis", {}).get("per_parent_summary", [])

    hotspot_path = out_dir / "child_order_explosion_500ms_d0_hotspots.json"
    hotspot_path.write_text(json.dumps(hotspot_data, indent=2, default=str), encoding="utf-8")
    print(f"\nHotspot data saved: {hotspot_path}")

    # ==================================================================
    # Save main JSON
    # ==================================================================
    out_json = out_dir / "child_order_explosion_500ms_d0.json"
    out_json.write_text(json.dumps(all_output, indent=2, default=str), encoding="utf-8")
    print(f"Raw results saved: {out_json}")
    print()


if __name__ == "__main__":
    main()
