#!/usr/bin/env python
"""
scripts/internal/workers/run_generation_worker.py
------------------------------------------
CLI entry-point for the strategy generation worker.

기본 config stack (자동 로드):
    app → paths → generation → backtest_base → backtest_worker → workers

Usage
-----
# Via shell launcher (recommended)
./scripts/internal/workers/run_generation_worker.sh

# With profile override (merged on top of the default config stack)
PYTHONPATH=src python scripts/internal/workers/run_generation_worker.py --profile dev

# With an explicit override file (merged last, after profile)
PYTHONPATH=src python scripts/internal/workers/run_generation_worker.py --config path/to/override.yaml

# Single-shot
PYTHONPATH=src python scripts/internal/workers/run_generation_worker.py --once
"""
from __future__ import annotations

import argparse
import logging
import sys

from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.generation_worker import GenerationWorker
from strategy_block.strategy_registry.registry import StrategyRegistry
from utils.config import load_config, get_paths, get_generation, get_workers


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Strategy generation worker")
    p.add_argument(
        "--config", default=None,
        help="Optional YAML override merged on top of the default config stack "
             "(app+paths+generation+backtest_base+backtest_worker+workers+profile)",
    )
    p.add_argument(
        "--profile", default=None,
        help="Config profile (dev, smoke, prod) — merged after base files, before --config",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Process a single job and exit",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = load_config(config_path=args.config, profile=args.profile)

    app = cfg.get("app", {})
    logging.basicConfig(
        level=getattr(logging, app.get("log_level", "INFO")),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    paths = get_paths(cfg)
    workers = get_workers(cfg)

    once = args.once or workers.get("once", False)

    queue = FileQueue(paths["jobs_dir"])
    registry = StrategyRegistry(paths["registry_dir"])
    worker = GenerationWorker(queue, registry, trace_dir=paths["traces_dir"])

    if once:
        job = worker.run_once()
        if job is None:
            print("No generate_strategy jobs in queue.")
            sys.exit(0)
        print(f"Job {job.job_id}: {job.status.value}")
        if job.status.value == "failed":
            print(f"  error: {job.error_message}")
            sys.exit(1)
    else:
        poll = workers.get("generation_poll_interval", 5.0)
        worker.run_loop(poll_interval=poll)


if __name__ == "__main__":
    main()
