"""File-based StrategySpecV2 registry."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator

from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_compiler import compile_strategy

from .models import StrategyMetadata, StrategyStatus

logger = logging.getLogger(__name__)


def _load_spec(path: Path) -> StrategySpecV2:
    return StrategySpecV2.load(path)


class StrategyRegistry:
    """File-based registry with metadata / status / version management."""

    def __init__(self, registry_dir: str | Path = "strategies/") -> None:
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)

    def _spec_path(self, name: str, version: str) -> Path:
        return self.registry_dir / f"{name}_v{version}.json"

    def _meta_path(self, name: str, version: str) -> Path:
        return self.registry_dir / f"{name}_v{version}.meta.json"

    def _strategy_id(self, name: str, version: str) -> str:
        return f"{name}_v{version}"

    def _ensure_exists(self, name: str, version: str) -> None:
        path = self._spec_path(name, version)
        if not path.exists():
            raise FileNotFoundError(f"Strategy not found: {path}")

    def save_spec(
        self,
        spec: StrategySpecV2,
        *,
        generation_backend: str = "",
        generation_mode: str = "",
        trace_path: str = "",
        extra: dict | None = None,
        spec_format: str = "v2",
    ) -> Path:
        if spec_format != "v2":
            raise ValueError("Only StrategySpec v2 is supported")

        sp = self._spec_path(spec.name, spec.version)
        spec.save(sp)

        meta = StrategyMetadata(
            strategy_id=self._strategy_id(spec.name, spec.version),
            name=spec.name,
            version=spec.version,
            status=StrategyStatus.DRAFT,
            generation_backend=generation_backend,
            generation_mode=generation_mode,
            spec_format="v2",
            spec_path=str(sp),
            trace_path=trace_path,
            extra=extra or {},
        )
        meta.save(self._meta_path(spec.name, spec.version))
        logger.info("Saved strategy '%s' v%s to %s", spec.name, spec.version, sp)
        return sp

    def load_spec(self, name: str, version: str) -> StrategySpecV2:
        self._ensure_exists(name, version)
        return _load_spec(self._spec_path(name, version))

    def get_metadata(self, name: str, version: str) -> StrategyMetadata:
        mp = self._meta_path(name, version)
        if not mp.exists():
            raise FileNotFoundError(f"Metadata not found: {mp}")
        return StrategyMetadata.load(mp)

    def list_specs(
        self,
        *,
        name_filter: str | None = None,
        status_filter: StrategyStatus | None = None,
    ) -> list[dict]:
        results: list[dict] = []
        for path in sorted(self.registry_dir.glob("*.meta.json")):
            try:
                meta = StrategyMetadata.load(path)
            except Exception as exc:
                logger.warning("Failed to load metadata %s: %s", path, exc)
                continue

            if name_filter and meta.name != name_filter:
                continue
            if status_filter and meta.status != status_filter:
                continue

            results.append(meta.to_dict())
        return results

    def update_status(
        self, name: str, version: str, new_status: StrategyStatus
    ) -> StrategyMetadata:
        meta = self.get_metadata(name, version)
        meta.transition_to(new_status)
        meta.save(self._meta_path(name, version))
        logger.info("Strategy '%s' v%s -> %s", name, version, new_status.value)
        return meta

    def promote_for_backtest(self, name: str, version: str) -> StrategyMetadata:
        meta = self.get_metadata(name, version)
        meta.transition_to(StrategyStatus.PROMOTED_TO_BACKTEST)
        meta.approved_for_backtest = True
        meta.save(self._meta_path(name, version))
        logger.info("Promoted '%s' v%s for backtest", name, version)
        return meta

    def promote_for_live(self, name: str, version: str) -> StrategyMetadata:
        meta = self.get_metadata(name, version)
        meta.transition_to(StrategyStatus.PROMOTED_TO_LIVE)
        meta.approved_for_live = True
        meta.save(self._meta_path(name, version))
        logger.info("Promoted '%s' v%s for live", name, version)
        return meta

    def resolve_version(self, name: str, version: str | None = None) -> str:
        if version is not None:
            self._ensure_exists(name, version)
            return version

        candidates = sorted(self.registry_dir.glob(f"{name}_v*.json"))
        candidates = [c for c in candidates if not c.name.endswith(".meta.json")]
        if not candidates:
            raise FileNotFoundError(f"No strategy named '{name}' in registry")
        last = candidates[-1].stem
        return last.split("_v", 1)[1]

    def latest_approved(self, name: str) -> StrategySpecV2:
        eligible_statuses = {
            StrategyStatus.APPROVED,
            StrategyStatus.PROMOTED_TO_BACKTEST,
            StrategyStatus.PROMOTED_TO_LIVE,
        }
        candidates: list[tuple[str, str]] = []
        for mp in sorted(self.registry_dir.glob(f"{name}_v*.meta.json")):
            try:
                meta = StrategyMetadata.load(mp)
            except Exception:
                continue
            if meta.status in eligible_statuses:
                candidates.append((meta.version, meta.name))

        if not candidates:
            raise FileNotFoundError(f"No approved strategy named '{name}' in registry")

        ver, nm = candidates[-1]
        return _load_spec(self._spec_path(nm, ver))

    BACKTEST_ELIGIBLE: set[StrategyStatus] = {
        StrategyStatus.APPROVED,
        StrategyStatus.PROMOTED_TO_BACKTEST,
        StrategyStatus.PROMOTED_TO_LIVE,
    }

    LIVE_ELIGIBLE: set[StrategyStatus] = {
        StrategyStatus.PROMOTED_TO_LIVE,
    }

    def check_execution_gate(
        self,
        name: str,
        version: str,
        *,
        require_live: bool = False,
    ) -> StrategyMetadata:
        meta = self.get_metadata(name, version)

        if not meta.static_review_passed:
            raise PermissionError(
                f"Strategy '{name}' v{version} has not passed static review"
            )

        eligible = self.LIVE_ELIGIBLE if require_live else self.BACKTEST_ELIGIBLE
        if meta.status not in eligible:
            raise PermissionError(
                f"Strategy '{name}' v{version} status is {meta.status.value!r}, "
                f"required one of {sorted(s.value for s in eligible)}"
            )
        return meta

    def load_spec_for_execution(
        self,
        name: str,
        version: str,
        *,
        require_live: bool = False,
    ) -> StrategySpecV2:
        self.check_execution_gate(name, version, require_live=require_live)
        return self.load_spec(name, version)

    def compile(self, name: str, version: str):
        spec = self.load_spec(name, version)
        return compile_strategy(spec)

    def iter_specs(self) -> Iterator[StrategySpecV2]:
        for path in sorted(self.registry_dir.glob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            try:
                yield _load_spec(path)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", path, exc)

    def save(self, spec: StrategySpecV2) -> Path:
        return self.save_spec(spec)

    def load(self, name: str, version: str | None = None) -> StrategySpecV2:
        resolved = self.resolve_version(name, version)
        return self.load_spec(name, resolved)

    def list_strategies(self) -> list[dict]:
        result = []
        for path in sorted(self.registry_dir.glob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            try:
                spec = _load_spec(path)
                info: dict = {
                    "name": spec.name,
                    "version": spec.version,
                    "description": spec.description,
                    "spec_format": "v2",
                    "path": str(path),
                    "n_entry_policies": len(spec.entry_policies),
                    "n_exit_policies": len(spec.exit_policies),
                }
                result.append(info)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", path, exc)
        return result


def _detect_spec_format(path: Path) -> str:
    """Compatibility shim retained for scripts; always validates as v2-only."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid strategy spec JSON: {path}") from exc
    if data.get("spec_format") != "v2":
        raise ValueError(f"Unsupported spec format at {path}: expected spec_format='v2'")
    return "v2"


def _load_spec_by_format(path: Path, spec_format: str) -> StrategySpecV2:
    if spec_format != "v2":
        raise ValueError("Unsupported spec format: only v2 is allowed")
    return StrategySpecV2.load(path)
