"""전략 사양 검토 스크립트 (StrategySpec v2 only)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path_ in (PROJECT_ROOT, SRC_ROOT):
    if str(path_) not in sys.path:
        sys.path.insert(0, str(path_))

from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_review.review_common import ReviewResult
from strategy_block.strategy_review.v2.llm_reviewer_v2 import LLMReviewerV2
from strategy_block.strategy_review.v2.patcher_v2 import StrategyRepairPatcherV2
from strategy_block.strategy_review.v2.pipeline_v2 import (
    run_auto_repair,
    run_llm_review,
    run_static_review,
)
from strategy_block.strategy_review.v2.repair_planner_v2 import RepairPlannerV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from utils.config import build_backtest_environment_context, get_generation, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review StrategySpec v2 JSON")
    parser.add_argument("spec_path", help="Path to strategy spec JSON (v2)")
    parser.add_argument(
        "--mode",
        default="static",
        choices=["static", "llm-review", "auto-repair"],
        help="Review mode: static (default), llm-review, auto-repair",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Optional YAML override merged on top of the default config stack "
            "(app+paths+generation+backtest_base+backtest_worker+workers+profile)"
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Config profile (dev/smoke/prod) merged before --config",
    )
    return parser.parse_args()


def _print_review_dict(*, spec: StrategySpecV2, title: str, review: dict[str, Any]) -> None:
    print(f"\n{'─' * 50}")
    print(f"{title}: {spec.name} (v{spec.version}, format=v2)")
    print(f"{'─' * 50}")

    passed = bool(review.get("passed", False))
    status = "PASSED" if passed else "FAILED"
    print(f"  Review: {status}")

    issues = list(review.get("issues") or [])
    if not issues:
        print("  No issues found.")
        return

    for issue in issues:
        sev = str(issue.get("severity", "")).upper()
        cat = str(issue.get("category", ""))
        desc = str(issue.get("description", ""))
        print(f"    [{sev}] ({cat}) {desc}")
        suggestion = issue.get("suggestion")
        if suggestion:
            print(f"           -> {suggestion}")


def _print_llm_review(report: Any) -> None:
    if report is None:
        return
    data = report.model_dump() if hasattr(report, "model_dump") else dict(report)
    print(f"\n{'─' * 50}")
    print("LLM Semantic Review")
    print(f"{'─' * 50}")
    print(f"  Assessment: {data.get('overall_assessment', 'unknown')}")
    print(f"  Repair recommended: {bool(data.get('repair_recommended', False))}")
    print(f"  Summary: {data.get('summary', '')}")
    for issue in data.get("issues", [])[:10]:
        sev = str(issue.get("severity", "")).upper()
        cat = str(issue.get("category", ""))
        desc = str(issue.get("description", ""))
        print(f"    [{sev}] ({cat}) {desc}")


def _print_repair_plan(plan: Any) -> None:
    if plan is None:
        return
    data = plan.model_dump() if hasattr(plan, "model_dump") else dict(plan)
    print(f"\n{'─' * 50}")
    print("Repair Plan")
    print(f"{'─' * 50}")
    print(f"  Summary: {data.get('summary', '')}")
    ops = list(data.get("operations") or [])
    print(f"  Operations: {len(ops)}")
    for op in ops:
        print(
            "    - "
            f"{op.get('op')} target={op.get('target')} value={op.get('value')}"
        )


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _artifact_dir(spec_path: Path) -> Path:
    return spec_path.parent / f"{spec_path.stem}_review_artifacts"


def _save_artifacts(
    *,
    output_dir: Path,
    static_review: dict[str, Any],
    llm_review: Any | None,
    repair_plan: Any | None,
    repaired_spec: dict[str, Any] | None,
    final_static_review: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    _save_json(output_dir / "static_review.json", static_review)
    if llm_review is not None:
        payload = llm_review.model_dump() if hasattr(llm_review, "model_dump") else llm_review
        _save_json(output_dir / "llm_review.json", payload)
    if repair_plan is not None:
        payload = repair_plan.model_dump() if hasattr(repair_plan, "model_dump") else repair_plan
        _save_json(output_dir / "repair_plan.json", payload)
    if repaired_spec is not None:
        _save_json(output_dir / "repaired_spec.json", repaired_spec)
    _save_json(output_dir / "final_static_review.json", final_static_review)


def _resolve_llm_runtime(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], str, str | None, str | Path | None]:
    cfg = load_config(config_path=args.config, profile=args.profile)
    gen = get_generation(cfg)
    env_context = build_backtest_environment_context(cfg)
    client_mode = str(gen.get("mode", "mock"))
    model = str(gen.get("openai_model", "gpt-4o"))
    replay_path = gen.get("replay_path")
    return env_context, client_mode, model, replay_path


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.WARNING)

    spec_path = Path(args.spec_path)
    spec = StrategySpecV2.load(spec_path)
    static_reviewer = StrategyReviewerV2()

    env_context, client_mode, model, replay_path = _resolve_llm_runtime(args)

    static_result: ReviewResult = run_static_review(
        spec,
        reviewer=static_reviewer,
        backtest_environment=env_context,
    )
    final_static_review = static_result.to_dict()
    llm_review = None
    repair_plan = None
    repaired_spec: dict[str, Any] | None = None
    llm_review_run = False
    repair_applied = False

    if args.mode == "llm-review":
        llm_reviewer = LLMReviewerV2(
            backend="openai",
            client_mode=client_mode,
            model=model,
            replay_path=replay_path,
        )
        llm_review = run_llm_review(
            spec=spec,
            static_review=static_result,
            backtest_environment=env_context,
            llm_reviewer=llm_reviewer,
            backend="openai",
            client_mode=client_mode,
            model=model,
            replay_path=replay_path,
        )
        llm_review_run = True

    elif args.mode == "auto-repair":
        llm_reviewer = LLMReviewerV2(
            backend="openai",
            client_mode=client_mode,
            model=model,
            replay_path=replay_path,
        )
        planner = RepairPlannerV2(
            backend="openai",
            client_mode=client_mode,
            model=model,
            replay_path=replay_path,
        )
        patcher = StrategyRepairPatcherV2()

        pipeline_result = run_auto_repair(
            spec=spec,
            backtest_environment=env_context,
            static_reviewer=static_reviewer,
            llm_reviewer=llm_reviewer,
            repair_planner=planner,
            patcher=patcher,
            backend="openai",
            client_mode=client_mode,
            model=model,
            replay_path=replay_path,
        )

        llm_review = pipeline_result.llm_review
        repair_plan = pipeline_result.repair_plan
        repair_applied = bool(pipeline_result.repair_applied)
        repaired_spec = pipeline_result.repaired_spec
        final_static_review = dict(pipeline_result.final_static_review)
        llm_review_run = True

    _print_review_dict(spec=spec, title="Static Review", review=static_result.to_dict())
    _print_llm_review(llm_review)
    _print_repair_plan(repair_plan)

    if args.mode == "auto-repair":
        _print_review_dict(spec=spec, title="Final Static Re-Review", review=final_static_review)

    # Always-on artifact policy for advanced review modes.
    if args.mode in {"llm-review", "auto-repair"}:
        output_dir = _artifact_dir(spec_path)
        _save_artifacts(
            output_dir=output_dir,
            static_review=static_result.to_dict(),
            llm_review=llm_review,
            repair_plan=repair_plan,
            repaired_spec=repaired_spec,
            final_static_review=final_static_review,
        )
        print(f"ARTIFACT_DIR={output_dir.resolve()}")

    final_passed = bool(final_static_review.get("passed", False))
    status = "PASSED" if final_passed else "FAILED"
    print(f"LLM_REVIEW_RUN={'true' if llm_review_run else 'false'}")
    print(f"REPAIR_APPLIED={'true' if repair_applied else 'false'}")
    print(f"REVIEW_STATUS={status}")

    if not final_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
