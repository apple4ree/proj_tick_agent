"""전략 생성 스크립트 (StrategySpec v2 only)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluation_orchestration.orchestration.manager import OrchestrationManager
from utils.config import load_config, get_paths, get_generation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit a StrategySpec v2 generation job")
    parser.add_argument("--goal", required=True,
                        help="Research goal — used to select templates or prompt LLM")
    parser.add_argument("--config", default=None,
                        help="Optional YAML override merged on top of the default config stack "
                             "(app+paths+generation+backtest_base+backtest_worker+workers+profile)")
    parser.add_argument("--profile", default=None,
                        help="Config profile (dev, smoke, prod) — merged after base files, before --config")
    parser.add_argument("--spec-format", default=None, choices=["v2"],
                        help="Spec format (fixed to v2)")
    parser.add_argument("--backend", default=None,
                        help="Override generation backend (template | openai)")
    parser.add_argument("--mode", default=None,
                        help="Override OpenAI mode (live | mock | replay)")
    parser.add_argument("--auto-approve", action="store_true", default=None,
                        help="Auto-approve generated spec")
    parser.add_argument("--direct", action="store_true",
                        help="Generate directly (bypass job queue). "
                             "Outputs GENERATED_SPEC=<path> for machine parsing.")
    return parser.parse_args()


def _trace_flags(trace: dict[str, Any]) -> dict[str, Any]:
    fallback = dict(trace.get("fallback") or {})
    events = list(fallback.get("events") or [])
    provenance = dict(trace.get("provenance") or {})

    return {
        "generation_outcome": trace.get("generation_outcome", "unknown"),
        "static_review_passed": bool(trace.get("static_review_passed", False)),
        "fallback_used": bool(trace.get("fallback_used", False) or fallback.get("used", False)),
        "fallback_count": int(fallback.get("count", len(events))),
        "fallback_events": events,
        "generation_class": provenance.get("generation_class", "unknown"),
        "requested_backend": provenance.get("requested_backend", ""),
        "effective_backend": provenance.get("effective_backend", ""),
        "requested_mode": provenance.get("requested_mode", ""),
        "effective_mode": provenance.get("effective_mode", ""),
        "spec_format": "v2",
    }


def _run_direct(args: argparse.Namespace, cfg: dict) -> None:
    from strategy_block.strategy_generation.generator import StrategyGenerator
    from strategy_block.strategy_registry.registry import StrategyRegistry
    from strategy_block.strategy_registry.models import StrategyStatus

    paths = get_paths(cfg)
    gen = get_generation(cfg)

    backend = args.backend or gen["backend"]
    mode = args.mode or gen["mode"]
    auto_approve = args.auto_approve if args.auto_approve is not None else gen["auto_approve"]

    generator = StrategyGenerator(
        latency_ms=gen["latency_ms"],
        backend=backend,
        mode=mode,
        spec_format="v2",
        allow_template_fallback=gen["allow_template_fallback"],
        allow_heuristic_fallback=gen["allow_heuristic_fallback"],
        fail_on_fallback=gen["fail_on_fallback"],
    )

    spec, trace = generator.generate(
        research_goal=args.goal,
        n_ideas=gen["n_ideas"],
        idea_index=gen["idea_index"],
    )

    trace_dir = Path(paths["traces_dir"])
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{spec.name}_v{spec.version}_trace.json"
    trace_path.write_text(
        json.dumps(trace, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    registry = StrategyRegistry(registry_dir=paths["registry_dir"])
    spec_path = registry.save_spec(
        spec,
        generation_backend=backend,
        generation_mode=mode,
        trace_path=str(trace_path),
        extra={"generation": _trace_flags(trace)},
        spec_format="v2",
    )

    meta = registry.get_metadata(spec.name, spec.version)
    meta.static_review_passed = True
    meta.save(registry._meta_path(spec.name, spec.version))
    registry.update_status(spec.name, spec.version, StrategyStatus.REVIEWED)
    if auto_approve:
        registry.update_status(spec.name, spec.version, StrategyStatus.APPROVED)

    print(f"Generated strategy: {spec.name} v{spec.version}")
    print("  spec_format: v2")
    print(f"  backend: {backend}")
    print(f"  mode:    {mode}")
    print(f"  outcome: {trace.get('generation_outcome', 'success')}")
    print(f"  fallback_used: {trace.get('fallback_used', False)}")
    print(f"GENERATED_SPEC={spec_path.resolve()}")


def _run_queue(args: argparse.Namespace, cfg: dict) -> None:
    paths = get_paths(cfg)
    gen = get_generation(cfg)

    payload = {
        "research_goal": args.goal,
        "spec_format": "v2",
        "backend": args.backend or gen["backend"],
        "mode": args.mode or gen["mode"],
        "latency_ms": gen["latency_ms"],
        "n_ideas": gen["n_ideas"],
        "idea_index": gen["idea_index"],
        "auto_approve": args.auto_approve if args.auto_approve is not None else gen["auto_approve"],
        "allow_template_fallback": gen["allow_template_fallback"],
        "allow_heuristic_fallback": gen["allow_heuristic_fallback"],
        "fail_on_fallback": gen["fail_on_fallback"],
    }

    manager = OrchestrationManager(paths["jobs_dir"])
    job = manager.submit_generation(payload)

    print(f"Submitted generation job: {job.job_id}")
    print(f"  goal:    {args.goal}")
    print("  spec_format: v2")
    print(f"  backend: {payload['backend']}")
    print(f"  mode:    {payload['mode']}")
    print(f"  queue:   {paths['jobs_dir']}")


def main() -> None:
    args = parse_args()
    cfg = load_config(config_path=args.config, profile=args.profile)

    app = cfg.get("app", {})
    logging.basicConfig(
        level=getattr(logging, app.get("log_level", "INFO")),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.spec_format not in (None, "v2"):
        raise ValueError("Only spec_format='v2' is supported")

    if args.direct:
        _run_direct(args, cfg)
    else:
        _run_queue(args, cfg)


if __name__ == "__main__":
    main()
