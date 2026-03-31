"""Promotion candidate CLI: deterministic gate + export bundle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from strategy_block.strategy_promotion import (  # noqa: E402
    DeploymentContractBuilder,
    PromotionBundleExporter,
    PromotionGate,
)
from strategy_block.strategy_registry.trial_registry import TrialRecord, TrialRegistry  # noqa: E402
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2  # noqa: E402
from utils.config import get_paths, load_config  # noqa: E402


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _load_promotion_config(*, profile: str | None, promotion_config: str | None) -> dict[str, Any]:
    conf_dir = PROJECT_ROOT / "conf"
    merged = _load_yaml(conf_dir / "promotion.yaml")

    if profile:
        profile_payload = _load_yaml(conf_dir / "profiles" / f"{profile}.yaml")
        promotion_override = profile_payload.get("promotion")
        if isinstance(promotion_override, dict):
            merged = _deep_merge(merged, {"promotion": promotion_override})

    if promotion_config:
        override_payload = _load_yaml(Path(promotion_config))
        if isinstance(override_payload.get("promotion"), dict):
            merged = _deep_merge(merged, {"promotion": override_payload["promotion"]})
        else:
            merged = _deep_merge(merged, override_payload)

    return merged


def _adhoc_trial(spec: StrategySpecV2, walk_forward_report: dict[str, Any]) -> TrialRecord:
    decision = dict(walk_forward_report.get("decision") or {})
    return TrialRecord(
        trial_id=str(walk_forward_report.get("trial_id") or "adhoc"),
        strategy_name=spec.name,
        strategy_version=spec.version,
        source_spec_path=str(walk_forward_report.get("spec_path") or ""),
        parent_trial_id=None,
        family_id=None,
        stage="WF_PASSED" if bool(decision.get("passed", False)) else "BACKTESTED",
        status="ACTIVE",
        reject_reason=None,
        metadata={"adhoc": True},
    )


def _compact_reasons(reasons: list[str]) -> str:
    if not reasons:
        return ""
    return "; ".join(str(r) for r in reasons)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate promotion gate and export handoff bundle")
    parser.add_argument("--spec", required=True, help="Path to strategy spec JSON")
    parser.add_argument("--walk-forward-report", required=True, help="Path to walk_forward_report.json")
    parser.add_argument("--trial-id", default=None, help="Optional trial registry id")
    parser.add_argument("--promotion-config", default=None, help="Optional promotion YAML override")
    parser.add_argument("--profile", default=None, help="Config profile override (dev/smoke/prod)")
    parser.add_argument("--out-dir", default=None, help="Optional explicit bundle output directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    spec_path = Path(args.spec)
    report_path = Path(args.walk_forward_report)
    spec = StrategySpecV2.load(spec_path)
    walk_forward_report = json.loads(report_path.read_text(encoding="utf-8"))

    app_cfg = load_config(profile=args.profile)
    paths = get_paths(app_cfg)
    promotion_cfg = _load_promotion_config(profile=args.profile, promotion_config=args.promotion_config)

    trial_registry = TrialRegistry(Path(paths["registry_dir"]) / "trials")
    trial_record: TrialRecord | None = None
    if args.trial_id:
        trial_record = trial_registry.get(args.trial_id)
        if trial_record is None:
            print("PROMOTION_STATUS=FAILED")
            print("PROMOTION_BUNDLE=")
            print("PROMOTION_REASONS=trial_not_found")
            sys.exit(1)

    decision_info = dict(walk_forward_report.get("decision") or {})
    wf_passed = bool(decision_info.get("passed", False))

    if trial_record is not None:
        if wf_passed:
            trial_registry.update_stage(
                trial_record.trial_id,
                "WF_PASSED",
                walk_forward_report_path=str(report_path.resolve()),
            )
            trial_record = trial_registry.get(trial_record.trial_id)
        else:
            trial_registry.reject(
                trial_record.trial_id,
                "REJECTED_WALK_FORWARD",
                walk_forward_reasons=list(decision_info.get("reasons") or []),
                walk_forward_report_path=str(report_path.resolve()),
            )
            trial_record = trial_registry.get(trial_record.trial_id)

    candidate = trial_record or _adhoc_trial(spec, walk_forward_report)

    gate = PromotionGate()
    gate_decision = gate.evaluate(
        trial_record=candidate,
        walk_forward_report=walk_forward_report,
        cfg=promotion_cfg,
    )

    if not gate_decision.passed:
        if trial_record is not None and wf_passed:
            trial_registry.reject(
                trial_record.trial_id,
                "REJECTED_PROMOTION_GATE",
                promotion_reasons=gate_decision.reasons,
                promotion_gate_metadata=gate_decision.metadata,
            )
        print("PROMOTION_STATUS=FAILED")
        print("PROMOTION_BUNDLE=")
        print(f"PROMOTION_REASONS={_compact_reasons(gate_decision.reasons)}")
        sys.exit(1)

    if trial_record is not None:
        trial_registry.update_stage(
            trial_record.trial_id,
            "PROMOTION_CANDIDATE",
            promotion_gate_metadata=gate_decision.metadata,
        )
        trial_record = trial_registry.get(trial_record.trial_id)

    contract = DeploymentContractBuilder().build(
        spec=spec,
        trial_record=trial_record,
        walk_forward_report=walk_forward_report,
        selection_cfg=promotion_cfg,
    )

    bundle_cfg = dict((promotion_cfg.get("promotion") or {}).get("bundle") or {})
    default_root = Path(str(bundle_cfg.get("output_root", "outputs/promotion_reports")))
    target_name = args.trial_id or f"{spec.name}_v{spec.version}"
    out_dir = Path(args.out_dir) if args.out_dir else (default_root / target_name)

    extra_artifacts: dict[str, str] = {}
    if trial_record is not None:
        trial_path = Path(paths["registry_dir"]) / "trials" / f"{trial_record.trial_id}.json"
        if trial_path.exists():
            extra_artifacts["trial_record"] = str(trial_path)

    bundle_path = PromotionBundleExporter().export(
        contract=contract,
        spec_path=str(spec_path),
        walk_forward_report_path=str(report_path),
        out_dir=str(out_dir),
        extra_artifacts=extra_artifacts or None,
        include_known_failure_modes=bool(bundle_cfg.get("include_known_failure_modes", True)),
        include_readme=bool(bundle_cfg.get("include_readme", True)),
    )

    if trial_record is not None:
        trial_registry.update_stage(trial_record.trial_id, "CONTRACT_EXPORTED", promotion_bundle=bundle_path)
        trial_registry.update_stage(trial_record.trial_id, "HANDOFF_READY", promotion_bundle=bundle_path)

    print("PROMOTION_STATUS=PASSED")
    print(f"PROMOTION_BUNDLE={bundle_path}")
    print(f"PROMOTION_REASONS={_compact_reasons(gate_decision.reasons)}")


if __name__ == "__main__":
    main()
