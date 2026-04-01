"""
전략 생성 → Hard Gate → 백테스트 → 피드백 → Memory 저장 반복 루프 CLI.

사용법:
    cd /home/dgu/tick/proj_rl_agent

    # mock 모드 (LLM 없이 테스트)
    PYTHONPATH=src python scripts/run_strategy_loop.py \\
        --research-goal "order imbalance momentum" \\
        --symbol 005930 --start-date 20260313 \\
        --mode mock --n-iter 3

    # 실제 OpenAI 사용
    OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/run_strategy_loop.py \\
        --research-goal "spread mean reversion" \\
        --symbol 005930 --start-date 20260313 --end-date 20260314 \\
        --mode live --model gpt-4o-mini --n-iter 10
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Force line-buffered stdout so progress is visible even when piped
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _p in (PROJECT_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from strategy_loop.loop_runner import LoopRunner
from strategy_loop.openai_client import OpenAIClient
from utils.config import load_config, get_paths, get_backtest


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Iterative strategy loop runner.")
    p.add_argument("--research-goal", required=True, help="Natural language research goal for strategy generation")
    p.add_argument("--symbol", required=True, help="KRX symbol code, e.g. 005930")
    p.add_argument("--start-date", required=True, help="Start date YYYYMMDD")
    p.add_argument("--end-date", default=None, help="End date YYYYMMDD (default: same as start)")
    p.add_argument("--n-iter", type=int, default=5, help="Max number of loop iterations (default: 5)")
    p.add_argument("--mode", choices=["live", "mock"], default="mock", help="LLM mode (default: mock)")
    p.add_argument("--model", default="gpt-4o-mini", help="OpenAI model name (live mode only)")
    p.add_argument("--memory-dir", default=None, help="Directory for memory storage (default: outputs/memory)")
    p.add_argument("--output-dir", default=None, help="Directory for backtest artifacts (default: outputs/backtests)")
    p.add_argument("--config", default=None, help="Optional YAML config override path")
    p.add_argument("--profile", default=None, help="Config profile (dev, smoke, prod)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(config_path=args.config, profile=args.profile)

    app = cfg.get("app", {})
    logging.basicConfig(
        level=getattr(logging, app.get("log_level", "INFO")),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    paths = get_paths(cfg)
    data_dir = paths["data_dir"]
    outputs_dir = paths.get("outputs_dir", "outputs")

    memory_dir = args.memory_dir or (outputs_dir + "/memory")
    output_dir = args.output_dir or (outputs_dir + "/backtests")

    client = OpenAIClient(model=args.model, mode=args.mode)
    runner = LoopRunner(client=client, memory_dir=memory_dir, output_dir=output_dir)

    print("=" * 72)
    print(f"Strategy Loop | goal='{args.research_goal}' | symbol={args.symbol}")
    print(f"  dates={args.start_date}..{args.end_date or args.start_date} | n_iter={args.n_iter} | mode={args.mode}")
    print("=" * 72)

    result = runner.run(
        research_goal=args.research_goal,
        n_iterations=args.n_iter,
        data_dir=data_dir,
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        cfg=cfg,
    )

    print("\n─── Loop Summary ─────────────────────────────────────────────")
    print(f"  Final verdict : {result.verdict}")
    print(f"  Best run_id   : {result.best_run_id or 'none'}")
    print(f"  Iterations    : {len(result.iterations)}")
    for rec in result.iterations:
        status = "SKIP" if rec.skipped else ("PASS" if rec.feedback and rec.feedback.get("verdict") == "pass" else "iter")
        verdict_str = rec.feedback["verdict"] if rec.feedback else (rec.skip_reason or "—")
        print(f"    [{rec.iteration:2d}] {rec.run_id}  {status:<6}  {verdict_str}")
    print()


if __name__ == "__main__":
    main()
