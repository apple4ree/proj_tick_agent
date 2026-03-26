"""
Observation-lag 500ms resample benchmark.

Measures wall-clock cost and result impact of 500ms vs 1s resample,
with and without observation lag, for both single-symbol and universe
backtest paths.

Usage:
    cd /home/dgu/tick/proj_rl_agent
    PYTHONPATH=src python scripts/internal/adhoc/benchmark_observation_lag_500ms.py

Output:
    outputs/benchmarks/observation_lag_500ms.json
    stdout: summary tables
"""
from __future__ import annotations

import gc
import json
import resource
import signal as _signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data.layer0_data import DataIngester, MarketStateBuilder
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import load_config, get_paths, get_backtest

# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------

SYMBOL = "005930"
DATE = "20260313"
SPEC_PATH = PROJECT_ROOT / "strategies" / "examples" / "stateful_cooldown_momentum_v2.0.json"
MAX_UNIVERSE_SYMBOLS = 10
PER_SYMBOL_TIMEOUT_S = 120  # skip symbols that take > 2 min


@dataclass
class RunMetrics:
    workflow: str = ""
    symbol: str = ""
    date: str = ""
    strategy: str = ""
    resample: str = ""
    market_data_delay_ms: float = 0.0
    avg_observation_staleness_ms: float = 0.0
    n_states: int = 0
    n_fills: int = 0
    cancel_rate: float = 0.0
    net_pnl: float = 0.0
    wall_clock_s: float = 0.0
    setup_s: float = 0.0
    loop_s: float = 0.0
    report_s: float = 0.0
    total_pipeline_s: float = 0.0
    state_build_s: float = 0.0
    peak_rss_mb: float = 0.0
    notes: str = ""


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _build_states(data_dir: str, symbol: str, date: str, resample: str) -> tuple[list, float]:
    builder = MarketStateBuilder(data_dir=data_dir, resample_freq=resample)
    t0 = time.monotonic()
    states = builder.build_states_from_symbol_date(symbol=symbol, date=date, resample_freq=resample)
    return states, time.monotonic() - t0


def _date_fmt(date: str) -> str:
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout()


def _run_single(
    *,
    symbol: str,
    date: str,
    resample: str,
    delay_ms: float,
    states: list,
    strategy_factory,
    data_dir: str,
    state_build_s: float,
    timeout_s: int | None = None,
) -> RunMetrics | None:
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

    return RunMetrics(
        workflow="single-symbol",
        symbol=symbol,
        date=date,
        strategy=strategy.name,
        resample=resample,
        market_data_delay_ms=delay_ms,
        avg_observation_staleness_ms=lag_info.get("avg_observation_staleness_ms", 0.0),
        n_states=result.n_states,
        n_fills=result.n_fills,
        cancel_rate=summary.get("cancel_rate", 0.0),
        net_pnl=summary.get("net_pnl", 0.0),
        wall_clock_s=round(wall, 3),
        setup_s=timings.get("setup_s", 0.0),
        loop_s=timings.get("loop_s", 0.0),
        report_s=timings.get("report_s", 0.0),
        total_pipeline_s=timings.get("total_s", 0.0),
        state_build_s=round(state_build_s, 3),
        peak_rss_mb=round(_peak_rss_mb(), 1),
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

    spec = StrategySpecV2.load(str(SPEC_PATH))
    def strategy_factory():
        return compile_strategy(spec)

    print("=" * 72)
    print("Observation-Lag 500ms Resample Benchmark")
    print("=" * 72)
    print(f"Symbol:   {SYMBOL}")
    print(f"Date:     {DATE}")
    print(f"Strategy: {spec.name}")
    print(f"Data dir: {data_dir}")
    print()

    all_results: list[RunMetrics] = []

    # ------------------------------------------------------------------
    # Warmup
    # ------------------------------------------------------------------
    print("  Warmup (import overhead) ... ", end="", flush=True)
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
    print("done\n")

    # ------------------------------------------------------------------
    # 1. Single-symbol benchmark (all 4 combos)
    # ------------------------------------------------------------------
    print("--- Single-Symbol Benchmark ---")

    state_cache: dict[str, tuple[list, float]] = {}
    for resample in ("1s", "500ms"):
        states, build_s = _build_states(data_dir, SYMBOL, DATE, resample)
        state_cache[resample] = (states, build_s)
        print(f"  Built {len(states):>6} states @ {resample:>5}  ({build_s:.1f}s)")

    combos = [("1s", 0.0), ("1s", 200.0), ("500ms", 0.0), ("500ms", 200.0)]
    for resample, delay_ms in combos:
        states, build_s = state_cache[resample]
        print(f"  {resample}/delay={delay_ms:.0f} ... ", end="", flush=True)
        m = _run_single(
            symbol=SYMBOL, date=DATE, resample=resample, delay_ms=delay_ms,
            states=states, strategy_factory=strategy_factory,
            data_dir=data_dir, state_build_s=build_s,
        )
        if m:
            ms_per = m.loop_s * 1000 / m.n_states if m.n_states else 0
            print(f"{m.wall_clock_s:.1f}s  loop={m.loop_s:.1f}s  "
                  f"{ms_per:.3f}ms/state  {m.n_fills} fills  staleness={m.avg_observation_staleness_ms:.0f}ms")
            all_results.append(m)

    # ------------------------------------------------------------------
    # 2. Universe benchmark (all 4 combos, with per-symbol timeout)
    # ------------------------------------------------------------------
    print("\n--- Universe Benchmark ---")

    ingester = DataIngester(data_dir)
    usable = [s for s in sorted(ingester.list_symbols()) if DATE in ingester.list_dates(s)]
    if len(usable) > MAX_UNIVERSE_SYMBOLS:
        universe = usable[:MAX_UNIVERSE_SYMBOLS]
        note = f"capped at {MAX_UNIVERSE_SYMBOLS} of {len(usable)} available"
    else:
        universe = usable
        note = f"full ({len(usable)} symbols)"
    print(f"  Universe: {len(universe)} symbols ({note})")

    for resample, delay_ms in combos:
        print(f"  {resample}/delay={delay_ms:.0f}: ", end="", flush=True)
        t0 = time.monotonic()
        agg_states = agg_fills = 0
        agg_cancel = agg_pnl = agg_loop = agg_staleness = agg_build = 0.0
        n_ok = n_timeout = 0
        staleness_n = 0

        for sym in universe:
            try:
                sts, bld = _build_states(data_dir, sym, DATE, resample)
            except Exception:
                continue
            if not sts:
                continue
            agg_build += bld

            m = _run_single(
                symbol=sym, date=DATE, resample=resample, delay_ms=delay_ms,
                states=sts, strategy_factory=strategy_factory,
                data_dir=data_dir, state_build_s=bld,
                timeout_s=PER_SYMBOL_TIMEOUT_S,
            )
            if m is None:
                n_timeout += 1
                continue
            agg_states += m.n_states
            agg_fills += m.n_fills
            agg_cancel += m.cancel_rate
            agg_pnl += m.net_pnl
            agg_loop += m.loop_s
            if m.avg_observation_staleness_ms > 0:
                agg_staleness += m.avg_observation_staleness_ms
                staleness_n += 1
            n_ok += 1

        wall = time.monotonic() - t0
        avg_staleness = agg_staleness / staleness_n if staleness_n > 0 else 0.0
        avg_cancel = agg_cancel / n_ok if n_ok > 0 else 0.0

        timeout_note = f" ({n_timeout} timeouts)" if n_timeout else ""
        um = RunMetrics(
            workflow="universe",
            symbol=f"{n_ok}/{len(universe)} symbols",
            date=DATE,
            strategy=spec.name,
            resample=resample,
            market_data_delay_ms=delay_ms,
            avg_observation_staleness_ms=round(avg_staleness, 3),
            n_states=agg_states,
            n_fills=agg_fills,
            cancel_rate=round(avg_cancel, 4),
            net_pnl=round(agg_pnl, 2),
            wall_clock_s=round(wall, 3),
            loop_s=round(agg_loop, 3),
            total_pipeline_s=round(wall, 3),
            state_build_s=round(agg_build, 3),
            peak_rss_mb=round(_peak_rss_mb(), 1),
            notes=f"{note}{timeout_note}",
        )
        ms_per = agg_loop * 1000 / agg_states if agg_states else 0
        print(f"{wall:.0f}s  loop={agg_loop:.0f}s  {ms_per:.3f}ms/state  "
              f"{agg_fills} fills  {n_ok} ok{timeout_note}")
        all_results.append(um)

    # ------------------------------------------------------------------
    # 3. Save results
    # ------------------------------------------------------------------
    out_dir = PROJECT_ROOT / "outputs" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "observation_lag_500ms.json"
    out_json.write_text(
        json.dumps([asdict(r) for r in all_results], indent=2, default=float),
        encoding="utf-8",
    )
    print(f"\nRaw results: {out_json}")

    # ------------------------------------------------------------------
    # 4. Print summary tables
    # ------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("RESULTS")
    print("=" * 72)

    single = [r for r in all_results if r.workflow == "single-symbol"]
    univ = [r for r in all_results if r.workflow == "universe"]

    print("\n## Table 1: Single-Symbol (005930)")
    hdr = f"{'resample':>8} {'delay':>6} {'staleness':>10} {'states':>7} " \
          f"{'wall_s':>7} {'loop_s':>7} {'ms/st':>7} {'fills':>6} {'cancel%':>8} {'pnl':>12}"
    print(hdr)
    for r in single:
        ms_per = r.loop_s * 1000 / r.n_states if r.n_states else 0
        print(f"{r.resample:>8} {r.market_data_delay_ms:>6.0f} "
              f"{r.avg_observation_staleness_ms:>10.1f} {r.n_states:>7} "
              f"{r.wall_clock_s:>7.1f} {r.loop_s:>7.1f} {ms_per:>7.3f} {r.n_fills:>6} "
              f"{r.cancel_rate:>8.4f} {r.net_pnl:>12.0f}")

    print(f"\n## Table 2: Universe ({DATE})")
    hdr2 = f"{'resample':>8} {'delay':>6} {'staleness':>10} {'states':>8} " \
           f"{'wall_s':>7} {'loop_s':>7} {'ms/st':>7} {'fills':>6} {'scope'}"
    print(hdr2)
    for r in univ:
        ms_per = r.loop_s * 1000 / r.n_states if r.n_states else 0
        print(f"{r.resample:>8} {r.market_data_delay_ms:>6.0f} "
              f"{r.avg_observation_staleness_ms:>10.1f} {r.n_states:>8} "
              f"{r.wall_clock_s:>7.0f} {r.loop_s:>7.0f} {ms_per:>7.3f} {r.n_fills:>6} "
              f"{r.symbol} {r.notes}")

    print("\n## Table 3: Relative Slowdown (500ms vs 1s)")
    bs = next((r for r in single if r.resample == "1s" and r.market_data_delay_ms == 0), None)
    bs200 = next((r for r in single if r.resample == "1s" and r.market_data_delay_ms == 200), None)
    bu = next((r for r in univ if r.resample == "1s" and r.market_data_delay_ms == 0), None)
    bu200 = next((r for r in univ if r.resample == "1s" and r.market_data_delay_ms == 200), None)

    print(f"{'workflow':>15} {'baseline':>20} {'compared':>20} {'wall_x':>7} {'loop_x':>7} {'states_x':>8}")
    for r in single:
        if r.resample == "1s" and r.market_data_delay_ms == 0:
            continue
        base = bs200 if r.market_data_delay_ms == 200 else bs
        if not base:
            continue
        tag = f"{r.resample}/d={r.market_data_delay_ms:.0f}"
        btag = f"1s/d={base.market_data_delay_ms:.0f}"
        wx = r.wall_clock_s / base.wall_clock_s if base.wall_clock_s else 0
        lx = r.loop_s / base.loop_s if base.loop_s else 0
        sx = r.n_states / base.n_states if base.n_states else 0
        print(f"{'single':>15} {btag:>20} {tag:>20} {wx:>6.2f}x {lx:>6.2f}x {sx:>7.2f}x")
    for r in univ:
        if r.resample == "1s" and r.market_data_delay_ms == 0:
            continue
        base = bu200 if r.market_data_delay_ms == 200 else bu
        if not base or not base.wall_clock_s:
            continue
        tag = f"{r.resample}/d={r.market_data_delay_ms:.0f}"
        btag = f"1s/d={base.market_data_delay_ms:.0f}"
        wx = r.wall_clock_s / base.wall_clock_s if base.wall_clock_s else 0
        lx = r.loop_s / base.loop_s if base.loop_s else 0
        sx = r.n_states / base.n_states if base.n_states else 0
        print(f"{'universe':>15} {btag:>20} {tag:>20} {wx:>6.2f}x {lx:>6.2f}x {sx:>7.2f}x")

    print()


if __name__ == "__main__":
    main()
