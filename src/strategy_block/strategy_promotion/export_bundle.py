"""Promotion bundle exporter (handoff artifacts, not live deploy)."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract_models import DeploymentContract


class PromotionBundleExporter:
    """Export deterministic promotion bundles for downstream handoff."""

    def export(
        self,
        *,
        contract: DeploymentContract,
        spec_path: str,
        walk_forward_report_path: str,
        out_dir: str,
        extra_artifacts: dict[str, str] | None = None,
        include_known_failure_modes: bool = True,
        include_readme: bool = True,
    ) -> str:
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)

        spec_src = Path(spec_path)
        report_src = Path(walk_forward_report_path)

        contract_out = root / "contract.json"
        spec_out = root / "spec.json"
        report_out = root / "walk_forward_report.json"
        manifest_out = root / "bundle_manifest.json"

        contract_out.write_text(
            json.dumps(asdict(contract), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        shutil.copy2(spec_src, spec_out)
        shutil.copy2(report_src, report_out)

        if include_known_failure_modes and contract.known_failure_modes:
            (root / "known_failure_modes.json").write_text(
                json.dumps(contract.known_failure_modes, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        copied_extra: dict[str, str] = {}
        if extra_artifacts:
            extra_dir = root / "extra"
            extra_dir.mkdir(parents=True, exist_ok=True)
            for key, source in sorted(extra_artifacts.items()):
                src = Path(source)
                if not src.exists() or not src.is_file():
                    continue
                target = extra_dir / f"{key}{src.suffix}"
                shutil.copy2(src, target)
                copied_extra[key] = str(target)

        manifest = {
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "strategy_name": contract.strategy_name,
            "strategy_version": contract.strategy_version,
            "trial_id": contract.trial_id,
            "family_id": contract.family_id,
            "bundle_root": str(root.resolve()),
            "options": {
                "include_known_failure_modes": bool(include_known_failure_modes),
                "include_readme": bool(include_readme),
            },
            "artifacts": {
                "contract": self._file_meta(contract_out),
                "spec": self._file_meta(spec_out),
                "walk_forward_report": self._file_meta(report_out),
            },
            "extra_artifacts": {
                key: self._file_meta(Path(path))
                for key, path in copied_extra.items()
            },
        }
        manifest_out.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if include_readme:
            readme = root / "README.md"
            readme.write_text(
                "\n".join(
                    [
                        f"# Promotion Bundle: {contract.strategy_name} v{contract.strategy_version}",
                        "",
                        "This bundle is a handoff artifact for downstream deployment review.",
                        "It does not execute live trading by itself.",
                        "",
                        "## Files",
                        "- contract.json",
                        "- spec.json",
                        "- walk_forward_report.json",
                        "- bundle_manifest.json",
                        "- known_failure_modes.json (optional)",
                    ]
                ),
                encoding="utf-8",
            )

        return str(root.resolve())

    def _file_meta(self, path: Path) -> dict[str, Any]:
        return {
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": self._sha256(path),
        }

    def _sha256(self, path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
