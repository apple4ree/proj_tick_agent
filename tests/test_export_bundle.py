from __future__ import annotations

import json
import os
from pathlib import Path

from strategy_block.strategy_promotion.contract_models import DeploymentContract
from strategy_block.strategy_promotion.export_bundle import PromotionBundleExporter


def _contract() -> DeploymentContract:
    return DeploymentContract(
        strategy_name="demo",
        strategy_version="2.0",
        trial_id="trial-01",
        family_id="fam-01",
        allowed_symbols=["005930"],
        expected_holding_horizon_s=(5.0, 10.0),
        max_turnover=4.0,
        latency_budget_ms=80.0,
        forbidden_time_ranges=["09:00-09:05"],
        required_features=["order_imbalance"],
        regime_dependencies=["default"],
        disable_conditions=["disable_if_aggregate_score_below:0.0"],
        monitoring_metrics=["aggregate_score"],
        known_failure_modes=["walk_forward_reason:churn_heavy_share_too_high"],
        notes={"source": "test"},
    )


def test_export_bundle_writes_manifest_contract_and_inputs(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    report_path = tmp_path / "walk_forward_report.json"
    spec_path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    report_path.write_text(json.dumps({"decision": {"passed": True}}), encoding="utf-8")

    out_dir = tmp_path / "bundle"
    bundle = PromotionBundleExporter().export(
        contract=_contract(),
        spec_path=str(spec_path),
        walk_forward_report_path=str(report_path),
        out_dir=str(out_dir),
        extra_artifacts={"extra_meta": str(report_path)},
    )

    root = Path(bundle)
    assert (root / "contract.json").exists()
    assert (root / "spec.json").exists()
    assert (root / "walk_forward_report.json").exists()
    assert (root / "bundle_manifest.json").exists()
    assert (root / "known_failure_modes.json").exists()
    assert (root / "README.md").exists()
    assert (root / "extra" / "extra_meta.json").exists()

    manifest = json.loads((root / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert manifest["strategy_name"] == "demo"
    assert "contract" in manifest["artifacts"]
    assert "sha256" in manifest["artifacts"]["contract"]


def test_export_bundle_is_stable_on_rerun_and_supports_relative_paths(tmp_path: Path) -> None:
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        Path("spec.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
        Path("walk_forward_report.json").write_text(json.dumps({"decision": {"passed": True}}), encoding="utf-8")

        exporter = PromotionBundleExporter()
        first = exporter.export(
            contract=_contract(),
            spec_path="spec.json",
            walk_forward_report_path="walk_forward_report.json",
            out_dir="bundle",
        )
        second = exporter.export(
            contract=_contract(),
            spec_path="spec.json",
            walk_forward_report_path="walk_forward_report.json",
            out_dir="bundle",
        )

        assert first == second
        root = Path(second)
        assert (root / "contract.json").exists()
        assert (root / "bundle_manifest.json").exists()
    finally:
        os.chdir(cwd)


def test_export_bundle_respects_include_flags(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    report_path = tmp_path / "walk_forward_report.json"
    spec_path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    report_path.write_text(json.dumps({"decision": {"passed": True}}), encoding="utf-8")

    out_dir = tmp_path / "bundle_no_optional"
    bundle = PromotionBundleExporter().export(
        contract=_contract(),
        spec_path=str(spec_path),
        walk_forward_report_path=str(report_path),
        out_dir=str(out_dir),
        include_known_failure_modes=False,
        include_readme=False,
    )

    root = Path(bundle)
    assert (root / "contract.json").exists()
    assert (root / "bundle_manifest.json").exists()
    assert not (root / "known_failure_modes.json").exists()
    assert not (root / "README.md").exists()

    manifest = json.loads((root / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert manifest["options"]["include_known_failure_modes"] is False
    assert manifest["options"]["include_readme"] is False
