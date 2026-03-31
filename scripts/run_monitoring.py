"""
run_monitoring.py
-----------------
Run a backtest with full monitoring instrumentation and export results.

Usage
-----
cd /home/dgu/tick/proj_rl_agent

PYTHONPATH=src python scripts/run_monitoring.py \
    --spec strategies/examples/my_strategy.json \
    --symbol 005930 --start-date 20260312 \
    [--end-date 20260312] \
    [--data-dir data/] \
    [--output-dir output/] \
    [--export-dir output/monitoring/] \
    [--verbose] \
    [--filter-order-ids id1,id2] \
    [--verify-queue] \
    [--no-verify-fees] \
    [--no-verify-slippage]
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
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from monitoring import MonitorConfig, attach_to_pipeline
from monitoring.verifiers.batch_verifier import run_all_verifiers
from monitoring.reporters.exporter import export_monitoring_run
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_monitoring")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Run backtest with monitoring instrumentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--spec",          required=True,  help="Path to strategy spec JSON")
    p.add_argument("--symbol",        required=True)
    p.add_argument("--start-date",    required=True)
    p.add_argument("--end-date",      default=None)
    p.add_argument("--data-dir",      default=str(PROJECT_ROOT / "data"))
    p.add_argument("--output-dir",    default=str(PROJECT_ROOT / "output"))
    p.add_argument("--export-dir",    default=None,
                   help="Directory for monitoring exports (default: output-dir/monitoring/)")
    p.add_argument("--verbose",       action="store_true",
                   help="Collect every QueueTickEvent (large output)")
    p.add_argument("--filter-order-ids", default=None,
                   help="Comma-separated child_ids to trace in non-verbose mode")
    p.add_argument("--verify-queue",  action="store_true",
                   help="Run queue arithmetic verifier (verbose mode only)")
    p.add_argument("--no-verify-fees",     action="store_true")
    p.add_argument("--no-verify-slippage", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    data_dir   = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    export_dir = Path(args.export_dir) if args.export_dir else (output_dir / "monitoring")
    export_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load data
    logger.info("Loading states for %s %s→%s", args.symbol, args.start_date, args.end_date or args.start_date)
    ingester = DataIngester(data_dir=data_dir)
    builder  = MarketStateBuilder()
    raw = ingester.load_date_range(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date or args.start_date,
    )
    states = builder.build(raw)
    logger.info("Loaded %d states", len(states))

    # 2. Compile strategy
    strategy = compile_strategy(StrategySpecV2.load(args.spec))
    logger.info("Strategy: %s", strategy.name)

    # 3. Build config
    config = BacktestConfig(
        symbol=args.symbol,
        start_date=args.start_date.replace("-", ""),
        end_date=(args.end_date or args.start_date).replace("-", ""),
    )

    # 4. Attach monitoring
    filter_ids = (
        set(args.filter_order_ids.split(",")) if args.filter_order_ids else None
    )
    mc = MonitorConfig(
        verbose=args.verbose,
        filter_order_ids=filter_ids,
        verify_fees=not args.no_verify_fees,
        verify_slippage=not args.no_verify_slippage,
        verify_queue_arithmetic=args.verify_queue,
        export_dir=export_dir,
    )
    runner = PipelineRunner(config=config, data_dir=data_dir,
                            output_dir=output_dir, strategy=strategy)
    runner = attach_to_pipeline(runner, mc)

    # 5. Run
    result = runner.run(states)
    logger.info("Backtest complete — run_id=%s  n_fills=%d", result.run_id, result.n_fills)

    # 6. Verify
    report = run_all_verifiers(
        runner.bus,
        verify_queue=mc.verify_queue_arithmetic,
    )

    # 7. Export
    paths = export_monitoring_run(runner.bus, report, export_dir, result.run_id)
    logger.info("Monitoring exports written to %s", export_dir / result.run_id)

    # 8. Console summary
    bus_summary = runner.bus.summary()
    print("\n─── Event Bus Summary ─────────────────────────")
    for event_type, count in sorted(bus_summary.items()):
        print(f"  {event_type:<30} {count:>6}")

    print("\n─── Verification Results ──────────────────────")
    print(f"  fee         pass rate: {report.fee_pass_rate*100:.1f}%  ({len(report.fee_failures)} failures)")
    print(f"  slippage    pass rate: {report.slippage_pass_rate*100:.1f}%  ({len(report.slippage_failures)} failures)")
    print(f"  latency     pass rate: {report.latency_pass_rate*100:.1f}%  ({len(report.latency_failures)} failures)")
    if mc.verify_queue_arithmetic:
        print(f"  queue arith pass rate: {report.queue_pass_rate*100:.1f}%  ({len(report.queue_failures)} failures)")

    if report.fee_failures:
        print("\n  Top fee violations:")
        for r in report.fee_failures[:5]:
            print(f"    child={r.child_id[:8]} error_krw={r.error_krw:.4f}")
    if report.latency_failures:
        print("\n  Top latency violations:")
        for r in report.latency_failures[:5]:
            print(f"    child={r.child_id[:8]} violation={r.violation}")

    print(f"\n─── Exports ────────────────────────────────────")
    for name, path in sorted(paths.items()):
        print(f"  {name:<30} {path}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
