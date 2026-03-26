"""
2x2 Observation-Lag vs Cadence Decomposition Benchmark.

Runs a 2x2 matrix: {1s, 500ms} × {delay=0, delay=200}
to separate observation-lag effects from decision-cadence effects.

Usage:
    cd /home/dgu/tick/proj_rl_agent
    PYTHONPATH=src python scripts/internal/adhoc/benchmark_observation_lag_2x2.py

Output:
    outputs/benchmarks/observation_lag_2x2.json
    stdout: summary tables + effect decomposition
"""
from __future__ import annotations

import copy
import gc
import json
import resource
import signal as _signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data.layer0_data import DataIngester, MarketStateBuilder
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
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
# Run result
# -----------------------------------------------------------------------

@dataclass
class RunResult:
    run_id: str = ""
    workflow: str = ""
    symbol: str = ""
    date: str = ""
    strategy: str = ""
    resample: str = ""
    market_data_delay_ms: float = 0.0
    avg_observation_staleness_ms: float = 0.0
    # timing
    n_states: int = 0
    wall_clock_s: float = 0.0
    setup_s: float = 0.0
    loop_s: float = 0.0
    report_s: float = 0.0
    save_s: float = 0.0
    total_pipeline_s: float = 0.0
    state_build_s: float = 0.0
    peak_rss_mb: float = 0.0
    # orders & fills
    signal_count: int = 0  # = n_parent_orders
    parent_order_count: int = 0
    child_order_count: int = 0
    n_fills: int = 0
    cancel_rate: float = 0.0
    avg_holding_period_steps: float = 0.0
    avg_holding_seconds: float = 0.0
    # pnl
    net_pnl: float = 0.0
    total_realized_pnl: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_commission: float = 0.0
    total_slippage: float = 0.0
    total_impact: float = 0.0
    # execution quality
    fill_rate: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_market_impact_bps: float = 0.0
    maker_fill_ratio: float = 0.0
    # notes
    notes: str = ""


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _date_fmt(date: str) -> str:
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def _tick_duration_s(resample: str) -> float:
    if resample == "500ms":
        return 0.5
    return 1.0


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout()


# -----------------------------------------------------------------------
# Build states
# -----------------------------------------------------------------------

def _build_states(data_dir: str, symbol: str, date: str, resample: str) -> tuple[list, float]:
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
) -> RunResult | None:
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

    return RunResult(
        run_id=run_id,
        workflow=workflow,
        symbol=symbol,
        date=date,
        strategy=strategy.name,
        resample=resample,
        market_data_delay_ms=delay_ms,
        avg_observation_staleness_ms=lag_info.get("avg_observation_staleness_ms", 0.0),
        n_states=result.n_states,
        wall_clock_s=round(wall, 3),
        setup_s=timings.get("setup_s", 0.0),
        loop_s=timings.get("loop_s", 0.0),
        report_s=timings.get("report_s", 0.0),
        save_s=timings.get("save_s", 0.0),
        total_pipeline_s=timings.get("total_s", 0.0),
        state_build_s=round(state_build_s, 3),
        peak_rss_mb=round(_peak_rss_mb(), 1),
        signal_count=result.execution_report.n_parent_orders,
        parent_order_count=result.execution_report.n_parent_orders,
        child_order_count=result.execution_report.n_child_orders,
        n_fills=result.n_fills,
        cancel_rate=summary.get("cancel_rate", 0.0),
        avg_holding_period_steps=avg_hold_steps,
        avg_holding_seconds=round(avg_hold_steps * tick_s, 3),
        net_pnl=summary.get("net_pnl", 0.0),
        total_realized_pnl=summary.get("total_realized_pnl", 0.0),
        total_unrealized_pnl=summary.get("total_unrealized_pnl", 0.0),
        total_commission=summary.get("total_commission", 0.0),
        total_slippage=summary.get("total_slippage", 0.0),
        total_impact=summary.get("total_impact", 0.0),
        fill_rate=summary.get("fill_rate", 0.0),
        avg_slippage_bps=summary.get("avg_slippage_bps", 0.0),
        avg_market_impact_bps=summary.get("avg_market_impact_bps", 0.0),
        maker_fill_ratio=summary.get("maker_fill_ratio", 0.0),
    )


# -----------------------------------------------------------------------
# Normalized strategy factory: double tick-based params for 500ms
# -----------------------------------------------------------------------

def _make_normalized_spec(src_path: Path, scale: int) -> Path:
    """Create a temp spec with tick-based params scaled by *scale*."""
    with open(src_path, "r") as f:
        spec = json.load(f)

    spec["name"] = f"{spec['name']}_norm{scale}x"

    # entry cooldown_ticks
    for ep in spec.get("entry_policies", []):
        c = ep.get("constraints", {})
        if "cooldown_ticks" in c:
            c["cooldown_ticks"] = c["cooldown_ticks"] * scale

    # exit holding_ticks threshold
    for xp in spec.get("exit_policies", []):
        for rule in xp.get("rules", []):
            cond = rule.get("condition", {})
            left = cond.get("left", {})
            if left.get("name") == "holding_ticks" and "threshold" in cond:
                cond["threshold"] = cond["threshold"] * scale

    # execution cancel_after_ticks
    ex = spec.get("execution_policy", {})
    if "cancel_after_ticks" in ex:
        ex["cancel_after_ticks"] = ex["cancel_after_ticks"] * scale
    for rule in ex.get("adaptation_rules", []):
        ov = rule.get("override", {})
        if "cancel_after_ticks" in ov:
            ov["cancel_after_ticks"] = ov["cancel_after_ticks"] * scale

    tmp = NamedTemporaryFile(
        mode="w", suffix=".json", prefix="spec_norm_", delete=False,
        dir=str(PROJECT_ROOT / "outputs" / "benchmarks"),
    )
    json.dump(spec, tmp, indent=2)
    tmp.close()
    return Path(tmp.name)


# -----------------------------------------------------------------------
# Print helpers
# -----------------------------------------------------------------------

def _print_raw_table(results: list[RunResult], label: str) -> None:
    print(f"\n## Table 1: {label}")
    hdr = (
        f"{'id':>3} {'resamp':>6} {'delay':>5} {'stale':>7} {'states':>7} "
        f"{'total_s':>8} {'loop_s':>8} {'signals':>7} {'parents':>7} {'children':>8} "
        f"{'fills':>6} {'cancel%':>8} {'hold_s':>7} {'net_pnl':>14}"
    )
    print(hdr)
    for r in results:
        print(
            f"{r.run_id:>3} {r.resample:>6} {r.market_data_delay_ms:>5.0f} "
            f"{r.avg_observation_staleness_ms:>7.0f} {r.n_states:>7} "
            f"{r.total_pipeline_s:>8.1f} {r.loop_s:>8.1f} "
            f"{r.signal_count:>7} {r.parent_order_count:>7} {r.child_order_count:>8} "
            f"{r.n_fills:>6} {r.cancel_rate:>8.4f} "
            f"{r.avg_holding_seconds:>7.1f} {r.net_pnl:>14.0f}"
        )


def _effect_row(label: str, base: RunResult, comp: RunResult) -> dict:
    """Compute delta between two runs."""
    d_signals = comp.signal_count - base.signal_count
    d_fills = comp.n_fills - base.n_fills
    d_cancel = comp.cancel_rate - base.cancel_rate
    d_hold_s = comp.avg_holding_seconds - base.avg_holding_seconds
    d_pnl = comp.net_pnl - base.net_pnl
    slowdown = comp.total_pipeline_s / base.total_pipeline_s if base.total_pipeline_s else 0
    state_x = comp.n_states / base.n_states if base.n_states else 0
    return {
        "effect": label,
        "base": f"{base.run_id}({base.resample}/d={base.market_data_delay_ms:.0f})",
        "comp": f"{comp.run_id}({comp.resample}/d={comp.market_data_delay_ms:.0f})",
        "d_signals": d_signals,
        "d_fills": d_fills,
        "d_cancel": round(d_cancel, 4),
        "d_hold_s": round(d_hold_s, 1),
        "d_pnl": round(d_pnl, 0),
        "slowdown_x": round(slowdown, 2),
        "state_x": round(state_x, 2),
    }


def _print_decomposition(results: list[RunResult], label: str) -> list[dict]:
    by_id = {r.run_id: r for r in results}
    A = by_id.get("A")
    B = by_id.get("B")
    C = by_id.get("C")
    D = by_id.get("D")

    if not all([A, B, C, D]):
        print(f"\n## Table 2: {label} — incomplete runs, skipping decomposition")
        return []

    rows = [
        _effect_row("cadence_effect (A→C)", A, C),
        _effect_row("lag_effect_1s (A→B)", A, B),
        _effect_row("lag_effect_500ms (C→D)", C, D),
    ]

    # interaction: (D-C) - (B-A)
    interaction = {
        "effect": "lag_identifiability_gain [(D-C)-(B-A)]",
        "base": "—",
        "comp": "—",
        "d_signals": (D.signal_count - C.signal_count) - (B.signal_count - A.signal_count),
        "d_fills": (D.n_fills - C.n_fills) - (B.n_fills - A.n_fills),
        "d_cancel": round((D.cancel_rate - C.cancel_rate) - (B.cancel_rate - A.cancel_rate), 4),
        "d_hold_s": round(
            (D.avg_holding_seconds - C.avg_holding_seconds)
            - (B.avg_holding_seconds - A.avg_holding_seconds), 1
        ),
        "d_pnl": round((D.net_pnl - C.net_pnl) - (B.net_pnl - A.net_pnl), 0),
        "slowdown_x": 0.0,
        "state_x": 0.0,
    }
    rows.append(interaction)

    print(f"\n## Table 2: Effect Decomposition — {label}")
    hdr = (
        f"{'effect':<45} {'d_signals':>9} {'d_fills':>8} {'d_cancel':>9} "
        f"{'d_hold_s':>8} {'d_pnl':>12} {'slow_x':>7} {'state_x':>7}"
    )
    print(hdr)
    for row in rows:
        print(
            f"{row['effect']:<45} {row['d_signals']:>9} {row['d_fills']:>8} "
            f"{row['d_cancel']:>9.4f} {row['d_hold_s']:>8.1f} {row['d_pnl']:>12.0f} "
            f"{row['slowdown_x']:>7.2f} {row['state_x']:>7.2f}"
        )

    return rows


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
    print("2x2 Observation-Lag vs Cadence Decomposition Benchmark")
    print("=" * 80)
    print(f"Symbol:   {SYMBOL}")
    print(f"Date:     {DATE}")
    print(f"Strategy: {spec.name}")
    print(f"Data dir: {data_dir}")
    print()

    all_output: dict = {}

    # ==================================================================
    # Phase 1: Single-symbol 2x2
    # ==================================================================
    print("=" * 60)
    print("Phase 1: Single-Symbol 2x2")
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
    single_results: list[RunResult] = []
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
            ms_per = m.loop_s * 1000 / m.n_states if m.n_states else 0
            print(f"{m.total_pipeline_s:.1f}s  {ms_per:.3f}ms/st  "
                  f"{m.n_fills} fills  hold={m.avg_holding_seconds:.1f}s  "
                  f"staleness={m.avg_observation_staleness_ms:.0f}ms")
            single_results.append(m)
        else:
            print("TIMEOUT")

    _print_raw_table(single_results, f"Single-Symbol ({SYMBOL})")
    single_decomp = _print_decomposition(single_results, "Single-Symbol")
    all_output["single_symbol"] = {
        "raw": [asdict(r) for r in single_results],
        "decomposition": single_decomp,
    }

    # ==================================================================
    # Phase 2: Universe 2x2
    # ==================================================================
    print("\n" + "=" * 60)
    print("Phase 2: Universe 2x2")
    print("=" * 60)

    ingester = DataIngester(data_dir)
    usable = [s for s in sorted(ingester.list_symbols()) if DATE in ingester.list_dates(s)]
    if len(usable) > MAX_UNIVERSE_SYMBOLS:
        universe = usable[:MAX_UNIVERSE_SYMBOLS]
        scope_note = f"capped at {MAX_UNIVERSE_SYMBOLS} of {len(usable)} available"
    else:
        universe = usable
        scope_note = f"full ({len(usable)} symbols)"
    print(f"  Universe: {len(universe)} symbols ({scope_note})")
    print(f"  Symbols: {universe}")

    univ_results: list[RunResult] = []
    for run_id, resample, delay_ms in COMBOS:
        print(f"  [{run_id}] {resample}/delay={delay_ms:.0f}: ", end="", flush=True)
        t0 = time.monotonic()
        agg = RunResult(
            run_id=run_id, workflow="universe",
            symbol=f"{len(universe)} symbols", date=DATE,
            strategy=spec.name, resample=resample,
            market_data_delay_ms=delay_ms,
        )
        n_ok = n_timeout = 0
        staleness_accum = 0.0
        staleness_n = 0
        hold_accum = 0.0
        hold_n = 0

        for sym in universe:
            try:
                sts, bld = _build_states(data_dir, sym, DATE, resample)
            except Exception:
                continue
            if not sts:
                continue
            agg.state_build_s += bld

            m = _run_one(
                run_id=run_id, workflow="universe-per-sym",
                symbol=sym, date=DATE, resample=resample, delay_ms=delay_ms,
                states=sts, strategy_factory=strategy_factory,
                data_dir=data_dir, state_build_s=bld,
                timeout_s=PER_SYMBOL_TIMEOUT_S,
            )
            if m is None:
                n_timeout += 1
                continue

            n_ok += 1
            agg.n_states += m.n_states
            agg.loop_s += m.loop_s
            agg.signal_count += m.signal_count
            agg.parent_order_count += m.parent_order_count
            agg.child_order_count += m.child_order_count
            agg.n_fills += m.n_fills
            agg.net_pnl += m.net_pnl
            agg.total_realized_pnl += m.total_realized_pnl
            agg.total_unrealized_pnl += m.total_unrealized_pnl
            agg.total_commission += m.total_commission
            agg.total_slippage += m.total_slippage
            agg.total_impact += m.total_impact
            if m.avg_observation_staleness_ms > 0:
                staleness_accum += m.avg_observation_staleness_ms
                staleness_n += 1
            if m.avg_holding_seconds > 0:
                hold_accum += m.avg_holding_seconds
                hold_n += 1

        wall = time.monotonic() - t0
        agg.wall_clock_s = round(wall, 3)
        agg.total_pipeline_s = round(wall, 3)
        agg.state_build_s = round(agg.state_build_s, 3)
        agg.loop_s = round(agg.loop_s, 3)
        agg.avg_observation_staleness_ms = round(staleness_accum / staleness_n, 3) if staleness_n else 0.0
        agg.avg_holding_seconds = round(hold_accum / hold_n, 3) if hold_n else 0.0
        agg.cancel_rate = round(
            (agg.child_order_count - agg.n_fills) / agg.child_order_count, 4
        ) if agg.child_order_count else 0.0
        agg.peak_rss_mb = round(_peak_rss_mb(), 1)
        agg.notes = f"{scope_note}" + (f" ({n_timeout} timeouts)" if n_timeout else "")
        agg.net_pnl = round(agg.net_pnl, 2)
        agg.total_realized_pnl = round(agg.total_realized_pnl, 2)
        agg.total_unrealized_pnl = round(agg.total_unrealized_pnl, 2)

        ms_per = agg.loop_s * 1000 / agg.n_states if agg.n_states else 0
        print(f"{wall:.0f}s  {ms_per:.3f}ms/st  "
              f"{agg.n_fills} fills  {n_ok} ok" +
              (f" ({n_timeout} timeout)" if n_timeout else ""))
        univ_results.append(agg)

    _print_raw_table(univ_results, f"Universe ({DATE})")
    univ_decomp = _print_decomposition(univ_results, "Universe")
    all_output["universe"] = {
        "raw": [asdict(r) for r in univ_results],
        "decomposition": univ_decomp,
    }

    # ==================================================================
    # Phase 3: Normalized strategy control (500ms with 2x tick params)
    # ==================================================================
    print("\n" + "=" * 60)
    print("Phase 3: Wall-Clock Normalized Control")
    print("=" * 60)
    print("  Creating normalized spec (2x tick params for 500ms) ...")

    norm_spec_path = _make_normalized_spec(SPEC_PATH, scale=2)
    norm_spec = StrategySpecV2.load(str(norm_spec_path))
    print(f"  Normalized spec: {norm_spec.name}")
    print(f"    cooldown_ticks: 30 → 60")
    print(f"    holding_ticks:  25 → 50")
    print(f"    cancel_after:   10 → 20")

    def norm_factory():
        return compile_strategy(norm_spec)

    # For normalized control we only need 2 runs:
    # C_norm: 500ms/delay=0 with normalized params
    # D_norm: 500ms/delay=200 with normalized params
    # Compare against original A (1s/delay=0) and B (1s/delay=200)
    norm_combos = [
        ("C_n", "500ms", 0.0),
        ("D_n", "500ms", 200.0),
    ]

    norm_results: list[RunResult] = []
    # Reuse A and B from single_results
    for r in single_results:
        if r.run_id in ("A", "B"):
            nr = RunResult(**{k: v for k, v in asdict(r).items()})
            norm_results.append(nr)

    for run_id, resample, delay_ms in norm_combos:
        states, build_s = state_cache[resample]
        print(f"  [{run_id}] {resample}/delay={delay_ms:.0f} (norm) ... ", end="", flush=True)
        m = _run_one(
            run_id=run_id, workflow="normalized-control",
            symbol=SYMBOL, date=DATE, resample=resample, delay_ms=delay_ms,
            states=states, strategy_factory=norm_factory,
            data_dir=data_dir, state_build_s=build_s,
        )
        if m:
            ms_per = m.loop_s * 1000 / m.n_states if m.n_states else 0
            print(f"{m.total_pipeline_s:.1f}s  {ms_per:.3f}ms/st  "
                  f"{m.n_fills} fills  hold={m.avg_holding_seconds:.1f}s  "
                  f"staleness={m.avg_observation_staleness_ms:.0f}ms")
            norm_results.append(m)
        else:
            print("TIMEOUT")

    _print_raw_table(norm_results, "Normalized Control (A, B = original 1s; C_n, D_n = norm 500ms)")

    # Effect decomposition for normalized: compare A→C_n and C_n→D_n
    norm_by_id = {r.run_id: r for r in norm_results}
    A = norm_by_id.get("A")
    B = norm_by_id.get("B")
    Cn = norm_by_id.get("C_n")
    Dn = norm_by_id.get("D_n")

    norm_decomp: list[dict] = []
    if A and B and Cn and Dn:
        norm_decomp = [
            _effect_row("cadence_effect_norm (A→C_n)", A, Cn),
            _effect_row("lag_effect_1s (A→B)", A, B),
            _effect_row("lag_effect_500ms_norm (C_n→D_n)", Cn, Dn),
        ]
        interaction = {
            "effect": "lag_identifiability_norm [(D_n-C_n)-(B-A)]",
            "base": "—", "comp": "—",
            "d_signals": (Dn.signal_count - Cn.signal_count) - (B.signal_count - A.signal_count),
            "d_fills": (Dn.n_fills - Cn.n_fills) - (B.n_fills - A.n_fills),
            "d_cancel": round(
                (Dn.cancel_rate - Cn.cancel_rate) - (B.cancel_rate - A.cancel_rate), 4),
            "d_hold_s": round(
                (Dn.avg_holding_seconds - Cn.avg_holding_seconds)
                - (B.avg_holding_seconds - A.avg_holding_seconds), 1),
            "d_pnl": round(
                (Dn.net_pnl - Cn.net_pnl) - (B.net_pnl - A.net_pnl), 0),
            "slowdown_x": 0.0, "state_x": 0.0,
        }
        norm_decomp.append(interaction)

        print(f"\n## Table 3: Normalized Control Decomposition")
        hdr = (
            f"{'effect':<50} {'d_signals':>9} {'d_fills':>8} {'d_cancel':>9} "
            f"{'d_hold_s':>8} {'d_pnl':>12} {'slow_x':>7}"
        )
        print(hdr)
        for row in norm_decomp:
            print(
                f"{row['effect']:<50} {row['d_signals']:>9} {row['d_fills']:>8} "
                f"{row['d_cancel']:>9.4f} {row['d_hold_s']:>8.1f} {row['d_pnl']:>12.0f} "
                f"{row['slowdown_x']:>7.2f}"
            )

    all_output["normalized_control"] = {
        "raw": [asdict(r) for r in norm_results],
        "decomposition": norm_decomp,
        "normalized_spec": str(norm_spec_path),
    }

    # ==================================================================
    # Save
    # ==================================================================
    out_json = out_dir / "observation_lag_2x2.json"
    out_json.write_text(
        json.dumps(all_output, indent=2, default=float), encoding="utf-8",
    )
    print(f"\nRaw results saved: {out_json}")
    print()


if __name__ == "__main__":
    main()
