"""
백테스트 Job 제출 스크립트.

backtest worker가 실행 중일 때, 이 스크립트로 backtest job을 제출합니다.

사용법:
    cd /home/dgu/tick/proj_rl_agent

    # 단일 종목 백테스트
    PYTHONPATH=src python scripts/internal/ops/submit_backtest_job.py \
        --strategy imbalance_momentum --version 1.0 \
        --symbol 005930 --start-date 2026-03-13

    # Universe 백테스트
    PYTHONPATH=src python scripts/internal/ops/submit_backtest_job.py \
        --strategy imbalance_momentum --version 1.0 \
        --universe --start-date 2026-03-13
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluation_orchestration.orchestration.manager import OrchestrationManager
from utils.config import load_config, get_paths, get_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit a backtest job")
    parser.add_argument("--strategy", required=True, help="Strategy name")
    parser.add_argument("--version", required=True, help="Strategy version")
    parser.add_argument("--config", default=None,
                        help="Optional YAML override merged on top of the default config stack "
                             "(app+paths+generation+backtest_base+backtest_worker+workers+profile)")
    parser.add_argument("--profile", default=None,
                        help="Config profile (dev, smoke, prod) — merged after base files, before --config")
    parser.add_argument("--universe", action="store_true", help="Run universe backtest")
    parser.add_argument("--symbol", default=None, help="Symbol for single backtest (e.g. 005930)")
    parser.add_argument("--start-date", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="End date (YYYY-MM-DD)")
    return parser.parse_args()


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

    extra = {}
    if args.symbol:
        extra["symbol"] = args.symbol
    if args.start_date:
        extra["start_date"] = args.start_date
    if args.end_date:
        extra["end_date"] = args.end_date
    extra["initial_cash"] = bt["initial_cash"]
    extra["seed"] = bt["seed"]
    if args.universe:
        extra["latencies"] = bt["latencies_ms"]
        extra["data_dir"] = paths["data_dir"]

    manager = OrchestrationManager(paths["jobs_dir"])
    job = manager.submit_backtest(
        args.strategy, args.version,
        universe=args.universe,
        extra=extra,
    )

    job_type = "universe_backtest" if args.universe else "single_backtest"
    print(f"Submitted {job_type} job: {job.job_id}")
    print(f"  strategy: {args.strategy} v{args.version}")
    if args.symbol:
        print(f"  symbol:   {args.symbol}")
    if args.start_date:
        print(f"  dates:    {args.start_date} ~ {args.end_date or args.start_date}")
    print(f"  queue:    {paths['jobs_dir']}")


if __name__ == "__main__":
    main()
