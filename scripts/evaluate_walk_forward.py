"""Walk-forward evaluator CLI."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path_ in (PROJECT_ROOT, SRC_ROOT):
    if str(path_) not in sys.path:
        sys.path.insert(0, str(path_))

from evaluation_orchestration.layer6_evaluator.selection_metrics import SelectionMetrics
from evaluation_orchestration.layer7_validation.walk_forward import (  # noqa: E402
    WalkForwardHarness,
    WalkForwardReportBuilder,
    WalkForwardSelector,
)
from evaluation_orchestration.layer7_validation.walk_forward.selection_snapshot import (  # noqa: E402
    SelectionContextResolver,
)
from utils.config import load_config  # noqa: E402


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_selection_config(*, profile: str | None, selection_config: str | None) -> dict[str, Any]:
    conf_dir = PROJECT_ROOT / "conf"
    merged = _load_yaml(conf_dir / "selection.yaml")

    if profile:
        profile_payload = _load_yaml(conf_dir / "profiles" / f"{profile}.yaml")
        if isinstance(profile_payload.get("selection"), dict):
            merged = _deep_merge(merged, {"selection": profile_payload["selection"]})

    if selection_config:
        override_payload = _load_yaml(Path(selection_config))
        if "selection" in override_payload and isinstance(override_payload["selection"], dict):
            merged = _deep_merge(merged, {"selection": override_payload["selection"]})
        else:
            merged = _deep_merge(merged, override_payload)

    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a strategy with deterministic walk-forward windows")
    parser.add_argument("--spec", required=True, help="Path to strategy spec JSON")

    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--symbol", help="Single symbol (required unless --universe)")
    scope.add_argument("--universe", action="store_true", help="Evaluate across discovered universe")

    parser.add_argument("--start-date", required=True, help="Start date YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="End date YYYYMMDD")
    parser.add_argument("--profile", default=None, help="Config profile override (dev/smoke/prod)")
    parser.add_argument("--selection-config", default=None, help="Optional YAML override for selection config")
    parser.add_argument("--trial-id", default=None, help="Optional trial registry id")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    selection_cfg = load_selection_config(
        profile=args.profile,
        selection_config=args.selection_config,
    )
    app_cfg = load_config(
        config_path=args.selection_config,
        profile=args.profile,
    )

    selection_root = selection_cfg.get("selection") if isinstance(selection_cfg.get("selection"), dict) else {}
    output_root = str(selection_root.get("output_root") or "outputs/walk_forward")

    run_cfg = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "profile": args.profile,
        "output_root": output_root,
        "selection": selection_root,
    }

    family_context = SelectionContextResolver().build_family_context(
        spec_path=args.spec,
        trial_id=args.trial_id,
        profile=args.profile,
        config_path=args.selection_config,
        app_cfg=app_cfg,
    )

    harness = WalkForwardHarness(selection_metrics=SelectionMetrics(selection_cfg))
    window_results = harness.run_spec(
        spec_path=args.spec,
        symbol=args.symbol,
        universe=bool(args.universe),
        cfg=run_cfg,
        trial_id=args.trial_id,
        selection_context=family_context,
    )

    selector = WalkForwardSelector()
    decision = selector.select(
        window_results,
        cfg=selection_cfg,
        family_context=family_context,
    )

    report_builder = WalkForwardReportBuilder()
    report = report_builder.build(
        decision,
        window_results,
        family_context=family_context,
    )
    report["spec_path"] = str(Path(args.spec).resolve())
    report["mode"] = "universe" if args.universe else "single"
    report["symbol"] = args.symbol

    selection_snapshot = report_builder.build_selection_snapshot(
        report,
        family_context=family_context,
    )

    trial_segment = f"trial_{args.trial_id}" if args.trial_id else "adhoc"
    scope_segment = "universe" if args.universe else str(args.symbol)
    out_dir = Path(output_root) / Path(args.spec).stem / trial_segment / scope_segment
    saved_paths = report_builder.save(
        str(out_dir),
        report,
        selection_cfg=selection_cfg,
        selection_snapshot=selection_snapshot,
    )

    report_path = Path(saved_paths["report_path"])
    print(f"WALK_FORWARD_STATUS={'PASSED' if decision.passed else 'FAILED'}")
    print(f"WALK_FORWARD_REPORT={report_path}")
    print(f"WALK_FORWARD_OUTDIR={out_dir.resolve()}")

    if not decision.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
