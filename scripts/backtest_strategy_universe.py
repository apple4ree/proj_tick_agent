"""
Universe 백테스트 실행기.

하나의 전략 사양을 모든 적용 가능한 종목에 대해 백테스트하고,
latency를 실험 변인으로 포함합니다.

기본 config stack (자동 로드):
    app → paths → generation → backtest_base → backtest_worker → workers
    (latency sweep 등 worker 전용 설정은 backtest_worker.yaml에서 로드)

사용법:
    cd /home/dgu/tick/proj_rl_agent

    # 기본 실행 (data-dir, latency sweep 등은 config stack에서 로드)
    PYTHONPATH=src python scripts/backtest_strategy_universe.py \
        --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
        --start-date 20260313

    # data-dir CLI override (config stack의 paths.data_dir 대신 사용)
    PYTHONPATH=src python scripts/backtest_strategy_universe.py \
        --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
        --data-dir /path/to/H0STASP0 \
        --start-date 20260313

    # Profile override (config stack 위에 profile YAML을 merge)
    PYTHONPATH=src python scripts/backtest_strategy_universe.py \
        --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
        --start-date 20260313 --profile smoke
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import pickle
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from data.layer0_data import DataIngester, MarketStateBuilder, validate_resample_freq
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from evaluation_orchestration.layer7_validation.backtest_config import LatencyConfig
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import load_config, get_paths, get_backtest, get_backtest_worker

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run universe backtest for a strategy spec")
    parser.add_argument("--spec", required=True, help="Path to strategy spec JSON")
    parser.add_argument("--data-dir", default=None, help="H0STASP0 data root (default: from config)")
    parser.add_argument("--start-date", required=True, help="Start date YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="End date YYYYMMDD (default: same as start)")
    parser.add_argument("--config", default=None,
                        help="Optional YAML override merged on top of the default config stack "
                             "(app+paths+generation+backtest_base+backtest_worker+workers+profile)")
    parser.add_argument("--profile", default=None,
                        help="Config profile (dev, smoke, prod) — merged after base files, before --config")
    return parser.parse_args()


def discover_symbols(data_dir: str, start_date: str, end_date: str | None = None) -> list[str]:
    """Discover all available symbols in the data directory."""
    ingester = DataIngester(data_dir)
    all_symbols = ingester.list_symbols()

    valid_symbols = []
    start = start_date.replace("-", "")
    end = (end_date or start_date).replace("-", "")

    for symbol in all_symbols:
        dates = ingester.list_dates(symbol)
        if any(start <= d <= end for d in dates):
            valid_symbols.append(symbol)

    return sorted(valid_symbols)


# ---------------------------------------------------------------------------
# State caching
# ---------------------------------------------------------------------------

def _cache_key(data_dir: str, symbol: str, start_date: str,
               end_date: str | None, resample: str) -> str:
    """Deterministic cache key from build parameters."""
    raw = f"{data_dir}|{symbol}|{start_date}|{end_date}|{resample}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def build_states(data_dir: str, symbol: str, start_date: str,
                 end_date: str | None, resample: str,
                 cache_dir: Path | None = None) -> list:
    """Build market states for a symbol, with optional disk caching."""
    # Check cache
    if cache_dir is not None:
        key = _cache_key(data_dir, symbol, start_date, end_date, resample)
        cache_path = cache_dir / f"states_{symbol}_{key}.pkl"
        if cache_path.exists():
            logger.info("Loading cached states for %s from %s", symbol, cache_path.name)
            with open(cache_path, "rb") as f:
                return pickle.load(f)

    start = start_date.replace("-", "")
    end = (end_date or start_date).replace("-", "")
    builder = MarketStateBuilder(data_dir=data_dir, resample_freq=resample)

    ingester = DataIngester(data_dir)
    available_dates = ingester.list_dates(symbol)
    selected = [d for d in available_dates if start <= d <= end]

    states = []
    for date in selected:
        states.extend(
            builder.build_states_from_symbol_date(
                symbol=symbol, date=date, resample_freq=resample,
            )
        )

    # Save to cache
    if cache_dir is not None and states:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(data_dir, symbol, start_date, end_date, resample)
        cache_path = cache_dir / f"states_{symbol}_{key}.pkl"
        with open(cache_path, "wb") as f:
            pickle.dump(states, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info("Cached %d states for %s (%s)", len(states), symbol, cache_path.name)

    return states


# ---------------------------------------------------------------------------
# Per-run output directory
# ---------------------------------------------------------------------------

def _run_output_dir(base_output_dir: Path, symbol: str, latency_ms: float) -> Path:
    """Deterministic per-run directory: <base>/runs/<symbol>/lat_<ms>/"""
    return base_output_dir / "runs" / symbol / f"lat_{int(latency_ms)}ms"


# ---------------------------------------------------------------------------
# Single backtest
# ---------------------------------------------------------------------------

@dataclass
class BacktestRunResult:
    """Result of a single backtest run — either success or failure."""
    symbol: str
    latency_ms: float
    summary: dict | None = None
    error: str | None = None
    elapsed_s: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.summary is not None


def run_single_backtest(
    strategy_cls,
    symbol: str,
    states: list,
    data_dir: str,
    latency_ms: float,
    initial_cash: float,
    seed: int,
    compute_attribution: bool,
    start_date: str,
    end_date: str,
    summary_only: bool = False,
    run_output_dir: Path | None = None,
    state_build_s: float = 0.0,
) -> BacktestRunResult:
    """Run a single backtest and return a result object (never None).

    Parameters
    ----------
    summary_only : bool
        When True, ``run_output_dir`` is ignored and no per-run artifacts are saved.
        When False and ``run_output_dir`` is provided, full artifacts
        (signals/fills/orders/plots) are persisted to that directory.
    run_output_dir : Path | None
        Per-run artifact directory.  Only used when ``summary_only=False``.
    state_build_s : float
        Elapsed seconds for building states (included in timings).
    """
    if not states:
        return BacktestRunResult(symbol=symbol, latency_ms=latency_ms,
                                 error="no market states")

    t0 = time.monotonic()

    # Build latency config from ms value
    derived_market_data_delay_ms = latency_ms * 0.1
    latency_config = LatencyConfig(
        profile="default",
        order_submit_ms=latency_ms * 0.3,
        order_ack_ms=latency_ms * 0.7,
        cancel_ms=latency_ms * 0.2,
        market_data_delay_ms=derived_market_data_delay_ms,
        add_jitter=latency_ms > 0,
        jitter_std_ms=max(0.01, latency_ms * 0.05),
    )

    config = BacktestConfig(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        seed=seed,
        latency=latency_config,
        market_data_delay_ms=derived_market_data_delay_ms,
        compute_attribution=compute_attribution,
    )

    strategy = strategy_cls()

    # Determine output_dir for PipelineRunner:
    #   summary_only=True  → None (no per-run artifacts)
    #   summary_only=False → run_output_dir (if provided)
    pipeline_output_dir = None if summary_only else run_output_dir

    runner = PipelineRunner(
        config=config,
        data_dir=data_dir,
        output_dir=pipeline_output_dir,
        strategy=strategy,
    )

    try:
        result = runner.run(states)
        summary = result.summary()
        summary["symbol"] = symbol
        summary["latency_ms"] = latency_ms
        elapsed = time.monotonic() - t0

        # Collect structured timings
        pipeline_timings = result.metadata.get("timings", {})
        run_timings = {"state_build_s": round(state_build_s, 3)}
        run_timings.update(pipeline_timings)
        run_timings["total_s"] = round(state_build_s + pipeline_timings.get("total_s", elapsed), 3)

        return BacktestRunResult(
            symbol=symbol, latency_ms=latency_ms,
            summary=summary, elapsed_s=elapsed, timings=run_timings,
        )
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error("Backtest failed for %s (latency=%sms): %s", symbol, latency_ms, e)
        return BacktestRunResult(symbol=symbol, latency_ms=latency_ms,
                                 error=str(e), elapsed_s=elapsed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    cfg = load_config(config_path=args.config, profile=args.profile)

    app = cfg.get("app", {})
    logging.basicConfig(
        level=getattr(logging, app.get("log_level", "INFO")),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    paths = get_paths(cfg)
    bt = get_backtest(cfg)
    bt_worker = get_backtest_worker(cfg)
    data_dir = args.data_dir or paths["data_dir"]
    latencies = bt_worker.get("latencies_ms", [0.0, 50.0, 100.0, 500.0, 1000.0])
    initial_cash = bt.get("initial_cash", 1e8)
    seed = bt.get("seed", 42)
    resample = bt.get("resample", "1s")
    validate_resample_freq(resample)
    base_output_dir = paths.get("outputs_dir", "outputs") + "/universe_backtest"

    # Load and compile strategy (v2-only)
    spec = StrategySpecV2.load(Path(args.spec))
    compiled_spec = spec

    def strategy_factory():
        return compile_strategy(compiled_spec)

    print(f"Strategy: {spec.name} (v{spec.version})")
    print(f"Description: {spec.description}")

    # Discover all symbols
    symbols = discover_symbols(data_dir, args.start_date, args.end_date)

    start_date_fmt = f"{args.start_date[:4]}-{args.start_date[4:6]}-{args.start_date[6:8]}" \
        if len(args.start_date) == 8 else args.start_date
    end_date_raw = args.end_date or args.start_date
    end_date_fmt = f"{end_date_raw[:4]}-{end_date_raw[4:6]}-{end_date_raw[6:8]}" \
        if len(end_date_raw) == 8 else end_date_raw

    print(f"Symbols: {len(symbols)} ({', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''})")
    print(f"Latencies: {latencies} ms")
    print(f"Date range: {start_date_fmt} ~ {end_date_fmt}")

    output_dir = Path(base_output_dir) / spec.name
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    all_run_results: list[BacktestRunResult] = []
    failures: list[dict] = []
    total = len(symbols) * len(latencies)
    t0 = time.time()

    # Sequential execution
    done = 0
    for symbol in symbols:
        logger.info("Building states for %s...", symbol)
        t_build_0 = time.monotonic()
        try:
            states = build_states(
                data_dir, symbol, args.start_date, args.end_date,
                resample, None,
            )
        except Exception as e:
            logger.warning("Failed to build states for %s: %s", symbol, e)
            for lat in latencies:
                failures.append({"symbol": symbol, "latency_ms": lat,
                                 "error": f"build_states: {e}"})
            done += len(latencies)
            continue
        t_build = time.monotonic() - t_build_0

        if not states:
            logger.warning("No states for %s, skipping", symbol)
            for lat in latencies:
                failures.append({"symbol": symbol, "latency_ms": lat,
                                 "error": "no market states"})
            done += len(latencies)
            continue

        logger.info("Built %d states for %s in %.1fs", len(states), symbol, t_build)

        for lat in latencies:
            done += 1
            logger.info("[%d/%d] %s latency=%sms", done, total, symbol, lat)

            run_dir = _run_output_dir(output_dir, symbol, lat)

            run_result = run_single_backtest(
                strategy_cls=strategy_factory,
                symbol=symbol, states=states, data_dir=data_dir,
                latency_ms=lat, initial_cash=initial_cash,
                seed=seed, compute_attribution=bt.get("compute_attribution", True),
                start_date=start_date_fmt, end_date=end_date_fmt,
                summary_only=True,
                run_output_dir=run_dir,
                state_build_s=t_build,
            )
            all_run_results.append(run_result)
            if run_result.ok:
                all_results.append(run_result.summary)
                logger.info("  -> OK (%.1fs)", run_result.elapsed_s)
            else:
                failures.append({"symbol": run_result.symbol,
                                 "latency_ms": run_result.latency_ms,
                                 "error": run_result.error})

    elapsed = time.time() - t0
    n_success = len(all_results)
    n_fail = len(failures)

    # Save detailed results CSV
    if all_results:
        csv_path = output_dir / "universe_results.csv"
        keys = list(all_results[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nDetailed results: {csv_path}")

    # Save raw JSON
    json_path = output_dir / "universe_results.json"
    json_path.write_text(
        json.dumps(all_results, indent=2, default=float, ensure_ascii=False),
        encoding="utf-8",
    )

    # Save failure report
    if failures:
        fail_path = output_dir / "failed_runs.json"
        fail_path.write_text(
            json.dumps(failures, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Failure report: {fail_path}")

    # Print summary
    all_passed = n_fail == 0 and n_success > 0
    all_failed = n_success == 0
    status_label = "COMPLETE" if all_passed else "PARTIAL" if n_success > 0 else "FAILED"

    print(f"\n{'=' * 70}")
    print(f"Universe Backtest {status_label}")
    print(f"{'=' * 70}")
    print(f"Strategy: {spec.name}")
    print(f"Symbols tested: {len(symbols)}")
    print(f"Latencies: {latencies}")
    print(f"Succeeded: {n_success}/{total}")
    if n_fail > 0:
        print(f"Failed:    {n_fail}/{total}")
        unique_errors = sorted({f["error"] for f in failures})
        for err in unique_errors[:5]:
            count = sum(1 for f in failures if f["error"] == err)
            print(f"  - [{count}x] {err}")
        if len(unique_errors) > 5:
            print(f"  ... and {len(unique_errors) - 5} more distinct errors")
    print(f"Time: {elapsed:.1f}s")
    if n_success > 0:
        print(f"Avg per run: {elapsed / n_success:.1f}s")
    print(f"Results: {output_dir}")

    # Exit code: non-zero if all runs failed
    if all_failed:
        print(f"\nERROR: All {total} runs failed. Exiting with code 1.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
