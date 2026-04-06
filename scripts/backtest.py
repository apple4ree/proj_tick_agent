"""
계층형 백테스트 진입점.

기본 config stack (자동 로드):
    app → paths → generation → backtest_base → backtest_worker → workers

사용법:
    cd /home/dgu/tick/proj_rl_agent

    # 기본 실행 (config stack에서 data_dir, fee, latency 등 자동 로드)
    PYTHONPATH=src python scripts/backtest.py \
        --code-file strategies/examples/order_imbalance_code.py \
        --symbol 005930 --start-date 20260313

    # Profile override (config stack 위에 profile YAML을 merge)
    PYTHONPATH=src python scripts/backtest.py \
        --code-file strategies/examples/order_imbalance_code.py \
        --symbol 005930 --start-date 20260313 --profile smoke

    # Explicit override (profile 위에 추가 YAML을 merge)
    PYTHONPATH=src python scripts/backtest.py \
        --code-file strategies/examples/order_imbalance_code.py \
        --symbol 005930 --start-date 20260313 --config custom_override.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from data.layer0_data import DataIngester, MarketStateBuilder, validate_resample_freq
from evaluation_orchestration.layer7_validation import (
    BacktestConfig,
    BacktestResult,
    PipelineRunner,
)
from monitoring import attach_to_pipeline
from monitoring.verifiers.batch_verifier import run_all_verifiers
from monitoring.reporters.exporter import export_monitoring_run
from strategy_block.strategy.base import Strategy
from strategy_loop.code_strategy import CodeStrategy
from utils.config import load_config, get_paths, get_backtest

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run layered tick-data backtests.")
    parser.add_argument("--code-file", required=True, help="Path to strategy Python code file")
    parser.add_argument("--symbol", required=True, help="KRX symbol code, e.g. 005930")
    parser.add_argument("--start-date", required=True, help="Start date YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="End date YYYYMMDD (default: same as start)")
    parser.add_argument("--config", default=None,
                        help="Optional YAML override merged on top of the default config stack "
                             "(app+paths+generation+backtest_base+backtest_worker+workers+profile)")
    parser.add_argument("--profile", default=None,
                        help="Config profile (dev, smoke, prod) — merged after base files, before --config")
    return parser.parse_args()


def normalize_date_str(value: str) -> str:
    return value.replace("-", "").strip()


def config_date_str(value: str) -> str:
    normalized = normalize_date_str(value)
    return f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}"


def select_dates(data_dir: str | Path, symbol: str, start_date: str, end_date: str | None = None) -> list[str]:
    ingester = DataIngester(data_dir)
    available_dates = ingester.list_dates(symbol)

    start = normalize_date_str(start_date)
    end = normalize_date_str(end_date or start_date)
    selected = [date for date in available_dates if start <= date <= end]

    if not selected:
        raise FileNotFoundError(
            f"No data found for symbol={symbol} in range {start}..{end} under {Path(data_dir)}"
        )
    return selected


def build_states_for_range(
    *,
    data_dir: str | Path,
    symbol: str,
    start_date: str,
    end_date: str | None = None,
    resample_freq: str | None = None,
    trade_lookback: int = 100,
) -> list:
    builder = MarketStateBuilder(
        data_dir=data_dir,
        trade_lookback=trade_lookback,
        resample_freq=resample_freq,
    )

    states = []
    for date in select_dates(data_dir=data_dir, symbol=symbol, start_date=start_date, end_date=end_date):
        states.extend(
            builder.build_states_from_symbol_date(
                symbol=symbol,
                date=date,
                resample_freq=resample_freq,
            )
        )

    if not states:
        raise ValueError(f"No valid MarketState rows built for symbol={symbol}")
    return states


def build_config(args: argparse.Namespace, bt_cfg: dict | None = None) -> BacktestConfig:
    """Build BacktestConfig from CLI args + YAML config."""
    bt = bt_cfg or {}
    end_date_str = args.end_date or args.start_date
    return BacktestConfig(
        symbol=args.symbol,
        start_date=config_date_str(args.start_date),
        end_date=config_date_str(end_date_str),
        initial_cash=bt.get("initial_cash", 1e8),
        seed=bt.get("seed", 42),
        slicing_algo=bt.get("slicing_algo", "TWAP"),
        placement_style=bt.get("placement_style", "spread_adaptive"),
        latency_ms=bt.get("latency_ms", 1.0),
        fee_model=bt.get("fee_model", "krx"),
        exchange_model=bt.get("exchange_model", "partial_fill"),
        queue_model=bt.get("queue_model", "prob_queue"),
        queue_position_assumption=bt.get("queue_position_assumption", 0.5),
        market_data_delay_ms=float(bt.get("market_data_delay_ms", 0.0)),
        decision_compute_ms=float(bt.get("decision_compute_ms", 0.0)),
        compute_attribution=bt.get("compute_attribution", True),
    )


def _load_code(path: str) -> str:
    """Load strategy Python code from a file."""
    return Path(path).read_text(encoding="utf-8")


def _build_strategy(args: argparse.Namespace) -> Strategy:
    """Build a CodeStrategy from a Python file."""
    code = _load_code(args.code_file)
    strategy_name = Path(args.code_file).stem or "code_strategy"
    return CodeStrategy(code=code, name=strategy_name)


def run_backtest(args: argparse.Namespace, cfg: dict | None = None) -> BacktestResult:
    """Run a backtest from CLI arguments + config."""
    cfg = cfg or load_config(config_path=getattr(args, "config", None),
                              profile=getattr(args, "profile", None))
    paths = get_paths(cfg)
    bt = get_backtest(cfg)
    config = build_config(args, bt)
    strategy = _build_strategy(args)

    data_dir = paths["data_dir"]
    resample = bt.get("resample", "1s")
    validate_resample_freq(resample)
    lookback = bt.get("trade_lookback", 100)
    output_dir = paths.get("outputs_dir", "outputs") + "/backtests"

    states = build_states_for_range(
        data_dir=data_dir,
        symbol=config.symbol,
        start_date=config.start_date,
        end_date=config.end_date,
        resample_freq=resample,
        trade_lookback=lookback,
    )

    runner = PipelineRunner(
        config=config,
        data_dir=data_dir,
        output_dir=output_dir,
        strategy=strategy,
    )
    runner = attach_to_pipeline(runner)
    result = runner.run(states)

    monitoring_dir = Path(output_dir) / "monitoring"
    monitoring_dir.mkdir(parents=True, exist_ok=True)
    report = run_all_verifiers(runner.bus)
    export_monitoring_run(runner.bus, report, monitoring_dir, result.run_id)

    return result


def backtest_config_from_cfg(
    cfg: dict,
    *,
    symbol: str,
    start_date: str,
    end_date: str | None = None,
    **overrides: object,
) -> BacktestConfig:
    """Build a :class:`BacktestConfig` from a YAML config dict."""
    bt = get_backtest(cfg)
    end_date = end_date or start_date
    base = {
        "symbol": symbol,
        "start_date": config_date_str(start_date),
        "end_date": config_date_str(end_date),
        "initial_cash": bt.get("initial_cash", 1e8),
        "seed": bt.get("seed", 42),
        "slicing_algo": bt.get("slicing_algo", "TWAP"),
        "placement_style": bt.get("placement_style", "spread_adaptive"),
        "latency_ms": bt.get("latency_ms", 1.0),
        "fee_model": bt.get("fee_model", "krx"),
        "exchange_model": bt.get("exchange_model", "partial_fill"),
        "queue_model": bt.get("queue_model", "prob_queue"),
        "queue_position_assumption": bt.get("queue_position_assumption", 0.5),
        "market_data_delay_ms": float(bt.get("market_data_delay_ms", 0.0)),
        "decision_compute_ms": float(bt.get("decision_compute_ms", 0.0)),
        "compute_attribution": bt.get("compute_attribution", True),
    }
    base.update(overrides)
    return BacktestConfig(**base)


def run_backtest_with_states(
    config: BacktestConfig,
    states: list,
    data_dir: str | Path,
    output_dir: str | Path = "outputs/backtests",
    strategy: Strategy | None = None,
    *,
    yaml_cfg: dict | None = None,
) -> BacktestResult:
    """
    Run a backtest programmatically with pre-built states.

    매개변수
    ----------
    config : BacktestConfig
        Backtest configuration.
    states : list
        List of MarketState objects.
    data_dir : str | Path
        Data directory for H0STASP0 files.
    output_dir : str | Path
        Directory for output artifacts.
    strategy : Strategy
        Strategy instance.
    yaml_cfg : dict, optional
        Merged config from :func:`utils.config.load_config`.
        When provided, ``data_dir`` and ``output_dir`` defaults are
        derived from the ``paths`` section if not explicitly overridden
        by the caller.

    반환값
    -------
    BacktestResult
    """
    if yaml_cfg is not None:
        paths = get_paths(yaml_cfg)
        if data_dir is None:
            data_dir = paths["data_dir"]
        if output_dir == "outputs/backtests":
            output_dir = paths.get("outputs_dir", "outputs") + "/backtests"

    runner = PipelineRunner(
        config=config,
        data_dir=data_dir,
        output_dir=output_dir,
        strategy=strategy,
    )
    return runner.run(states)


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
    config = build_config(args, bt)

    symbol = config.symbol
    start_date = config.start_date
    end_date = config.end_date
    data_dir = paths["data_dir"]
    resample = bt.get("resample", "1s")
    validate_resample_freq(resample)
    lookback = bt.get("trade_lookback", 100)
    output_dir = paths.get("outputs_dir", "outputs") + "/backtests"

    print("=" * 72)
    print(f"Layered Backtest | symbol={symbol} | dates={normalize_date_str(start_date)}..{normalize_date_str(end_date)}")
    print("=" * 72)

    states = build_states_for_range(
        data_dir=data_dir,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        resample_freq=resample,
        trade_lookback=lookback,
    )

    strategy = _build_strategy(args)

    runner = PipelineRunner(
        config=config,
        data_dir=data_dir,
        output_dir=output_dir,
        strategy=strategy,
    )
    runner = attach_to_pipeline(runner)
    result = runner.run(states)
    summary = result.summary()

    monitoring_dir = Path(output_dir) / "monitoring"
    monitoring_dir.mkdir(parents=True, exist_ok=True)
    report = run_all_verifiers(runner.bus)
    export_monitoring_run(runner.bus, report, monitoring_dir, result.run_id)

    print(json.dumps(summary, indent=2, sort_keys=True, default=float))
    run_dir = Path(output_dir) / result.run_id
    print(f"Saved run artifacts: {run_dir}")

    bus_summary = runner.bus.summary()
    print("\n─── Event Bus Summary ─────────────────────────")
    for event_type, count in sorted(bus_summary.items()):
        print(f"  {event_type:<30} {count:>6}")
    print(f"\n─── Verification Results ──────────────────────")
    print(f"  fee      pass rate: {report.fee_pass_rate*100:.1f}%  ({len(report.fee_failures)} failures)")
    print(f"  slippage pass rate: {report.slippage_pass_rate*100:.1f}%  ({len(report.slippage_failures)} failures)")
    print(f"  latency  pass rate: {report.latency_pass_rate*100:.1f}%  ({len(report.latency_failures)} failures)")
    print(f"\n─── Monitoring Exports ─────────────────────────")
    print(f"  {monitoring_dir / result.run_id}")


if __name__ == "__main__":
    main()
