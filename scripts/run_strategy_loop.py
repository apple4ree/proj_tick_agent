"""
코드 전략 생성 → Hard Gate → 백테스트 → 피드백 → Memory 저장 반복 루프 CLI.

사용법:
    cd /home/dgu/tick/proj_rl_agent

    # mock 모드 (LLM 없이 테스트, OOS 없음)
    PYTHONPATH=src python scripts/run_strategy_loop.py \\
        --research-goal "order imbalance momentum" \\
        --symbol 005930 --is-start 20260313 --is-end 20260313 \\
        --mode mock --n-iter 3

    # IS/OOS 분리 (실제 OpenAI 사용)
    OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/run_strategy_loop.py \\
        --research-goal "spread mean reversion" \\
        --symbol 005930 \\
        --is-start 20260313 --is-end 20260319 \\
        --oos-start 20260320 --oos-end 20260326 \\
        --mode live --model gpt-4o-mini --n-iter 10
"""
from __future__ import annotations

import argparse
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

from strategy_loop.date_ranges import DateRanges
from strategy_loop.loop_runner import LoopRunner
from strategy_loop.openai_client import OpenAIClient
from utils.config import load_config, get_paths


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Iterative strategy loop runner.")
    p.add_argument("--research-goal", required=True, help="Natural language research goal for strategy generation")
    p.add_argument("--symbols", default=None, help="Comma-separated KRX symbol codes, e.g. 005930,000660,005380")
    p.add_argument("--symbol", default=None, help="Single KRX symbol code (shorthand for --symbols with one entry)")
    # IS date range (required)
    p.add_argument("--is-start", required=True, help="In-sample start date YYYYMMDD")
    p.add_argument("--is-end", required=True, help="In-sample end date YYYYMMDD")
    # OOS date range (optional)
    p.add_argument("--oos-start", default=None, help="Out-of-sample start date YYYYMMDD (omit for no OOS check)")
    p.add_argument("--oos-end", default=None, help="Out-of-sample end date YYYYMMDD")
    p.add_argument("--n-iter", type=int, default=5, help="Max number of loop iterations (default: 5)")
    p.add_argument("--optimize-n-trials", type=int, default=20,
                   help="Optuna threshold optimization trials per iteration (0 = disabled, default: 20)")
    p.add_argument("--mode", choices=["live", "mock"], default="mock", help="LLM mode (default: mock)")
    p.add_argument("--model", default="gpt-4o", help="OpenAI model name (live mode only)")
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
    runner = LoopRunner(
        client=client,
        memory_dir=memory_dir,
        output_dir=output_dir,
        optimize_n_trials=args.optimize_n_trials,
    )

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.symbol:
        symbols = [args.symbol]
    else:
        raise SystemExit("error: one of --symbol or --symbols is required")

    # Validate OOS args: both must be provided together or not at all
    if bool(args.oos_start) != bool(args.oos_end):
        raise SystemExit("error: --oos-start and --oos-end must be provided together")

    date_ranges = DateRanges(
        is_start=args.is_start,
        is_end=args.is_end,
        oos_start=args.oos_start,
        oos_end=args.oos_end,
    )

    oos_str = f"  OOS={date_ranges.oos_start}..{date_ranges.oos_end}" if date_ranges.has_oos else "  OOS=none"
    print("=" * 72)
    print(f"Strategy Loop | goal='{args.research_goal}' | symbols={','.join(symbols)}")
    print(f"  IS={date_ranges.is_start}..{date_ranges.is_end}{oos_str}")
    print(f"  llm_mode={args.mode} | strategy_mode=code | n_iter={args.n_iter}")
    print("=" * 72)

    result = runner.run(
        research_goal=args.research_goal,
        n_iterations=args.n_iter,
        data_dir=data_dir,
        symbols=symbols,
        date_ranges=date_ranges,
        cfg=cfg,
    )

    print("\n─── Loop Summary ─────────────────────────────────────────────")
    print(f"  Final verdict : {result.verdict}")
    print(f"  OOS verdict   : {result.oos_verdict}")
    print(f"  Best run_id   : {result.best_run_id or 'none'}")
    print(f"  Iterations    : {len(result.iterations)}")
    for rec in result.iterations:
        status = "SKIP" if rec.skipped else ("PASS" if rec.feedback and rec.feedback.get("verdict") == "pass" else "iter")
        verdict_str = rec.feedback["verdict"] if rec.feedback else (rec.skip_reason or "—")
        print(f"    [{rec.iteration:2d}] {rec.run_id}  {status:<6}  {verdict_str}")
    print()


if __name__ == "__main__":
    main()
