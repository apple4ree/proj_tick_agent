"""
strategy_registry/registry.py
-----------------------------
Extended registry for managing strategy specs, metadata, and lifecycle.

Acts as the single source of truth for both the generation plane (produces
specs) and the execution plane (consumes specs).  Every spec is stored with
a companion ``.meta.json`` file that tracks status, version, provenance, and
promotion state.

File layout
-----------
::

    <registry_dir>/
        <name>_v<version>.json         # StrategySpec or StrategySpecV2
        <name>_v<version>.meta.json    # StrategyMetadata
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator, Union

from strategy_block.strategy_specs.schema import StrategySpec
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_compiler import compile_strategy

from .models import StrategyMetadata, StrategyStatus, VALID_TRANSITIONS

logger = logging.getLogger(__name__)

# Union type for any spec version
AnySpec = Union[StrategySpec, StrategySpecV2]


def _load_spec_by_format(path: Path, spec_format: str) -> AnySpec:
    """Load a spec JSON using the correct class based on *spec_format*."""
    if spec_format == "v2":
        return StrategySpecV2.load(path)
    return StrategySpec.load(path)


def _detect_spec_format(path: Path) -> str:
    """Detect spec format by reading the JSON and checking spec_format field."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("spec_format", "v1")
    except Exception:
        return "v1"


class StrategyRegistry:
    """File-based registry with metadata / status / version management.

    Parameters
    ----------
    registry_dir : str | Path
        Root directory for spec + meta files.
    """

    def __init__(self, registry_dir: str | Path = "strategies/") -> None:
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)

    # -- internal helpers -----------------------------------------------------

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

    def _resolve_spec_format(self, name: str, version: str) -> str:
        """Determine spec_format using metadata first, falling back to JSON."""
        mp = self._meta_path(name, version)
        if mp.exists():
            try:
                meta = StrategyMetadata.load(mp)
                return meta.spec_format
            except Exception:
                pass
        # Fallback: peek at JSON content
        return _detect_spec_format(self._spec_path(name, version))

    # -- core CRUD ------------------------------------------------------------

    def save_spec(
        self,
        spec: AnySpec,
        *,
        generation_backend: str = "",
        generation_mode: str = "",
        trace_path: str = "",
        extra: dict | None = None,
        spec_format: str = "v1",
    ) -> Path:
        """Save a strategy spec and create its metadata record.

        Works with both StrategySpec (v1) and StrategySpecV2 (v2).
        Returns the path where the spec was saved.
        """
        sp = self._spec_path(spec.name, spec.version)
        spec.save(sp)

        # Auto-detect spec_format from spec type
        detected_format = spec_format
        if hasattr(spec, "spec_format"):
            detected_format = getattr(spec, "spec_format", spec_format)

        meta = StrategyMetadata(
            strategy_id=self._strategy_id(spec.name, spec.version),
            name=spec.name,
            version=spec.version,
            status=StrategyStatus.DRAFT,
            generation_backend=generation_backend,
            generation_mode=generation_mode,
            spec_format=detected_format,
            spec_path=str(sp),
            trace_path=trace_path,
            extra=extra or {},
        )
        meta.save(self._meta_path(spec.name, spec.version))
        logger.info("Saved strategy '%s' v%s to %s", spec.name, spec.version, sp)
        return sp

    def load_spec(self, name: str, version: str) -> AnySpec:
        """Load a strategy spec by name and **explicit** version.

        Automatically detects v1/v2 format via metadata or JSON content
        and returns the appropriate spec class.
        """
        self._ensure_exists(name, version)
        fmt = self._resolve_spec_format(name, version)
        return _load_spec_by_format(self._spec_path(name, version), fmt)

    def get_metadata(self, name: str, version: str) -> StrategyMetadata:
        """Return the metadata record for a given spec version."""
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
        """List specs with optional name / status filtering.

        Returns a list of dicts with basic info + metadata fields.
        """
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

    # -- status management ----------------------------------------------------

    def update_status(
        self, name: str, version: str, new_status: StrategyStatus
    ) -> StrategyMetadata:
        """Transition a strategy to *new_status*.

        Raises ``ValueError`` on illegal transitions.
        """
        meta = self.get_metadata(name, version)
        meta.transition_to(new_status)
        meta.save(self._meta_path(name, version))
        logger.info(
            "Strategy '%s' v%s -> %s", name, version, new_status.value
        )
        return meta

    def promote_for_backtest(self, name: str, version: str) -> StrategyMetadata:
        """Promote an approved strategy to backtest.

        Requires current status == APPROVED.
        """
        meta = self.get_metadata(name, version)
        meta.transition_to(StrategyStatus.PROMOTED_TO_BACKTEST)
        meta.approved_for_backtest = True
        meta.save(self._meta_path(name, version))
        logger.info("Promoted '%s' v%s for backtest", name, version)
        return meta

    def promote_for_live(self, name: str, version: str) -> StrategyMetadata:
        """Promote a backtested strategy to live.

        Requires current status == PROMOTED_TO_BACKTEST.
        """
        meta = self.get_metadata(name, version)
        meta.transition_to(StrategyStatus.PROMOTED_TO_LIVE)
        meta.approved_for_live = True
        meta.save(self._meta_path(name, version))
        logger.info("Promoted '%s' v%s for live", name, version)
        return meta

    # -- version queries ------------------------------------------------------

    def resolve_version(self, name: str, version: str | None = None) -> str:
        """Resolve a version string.

        If *version* is ``None``, returns the latest version available.
        Otherwise validates that the requested version exists and returns it.
        """
        if version is not None:
            self._ensure_exists(name, version)
            return version

        candidates = sorted(self.registry_dir.glob(f"{name}_v*.json"))
        # exclude .meta.json
        candidates = [c for c in candidates if not c.name.endswith(".meta.json")]
        if not candidates:
            raise FileNotFoundError(f"No strategy named '{name}' in registry")
        # extract version from last candidate
        last = candidates[-1].stem  # e.g. "foo_v2.1"
        return last.split("_v", 1)[1]

    def latest_approved(self, name: str) -> AnySpec:
        """Return the latest spec whose status is APPROVED or higher.

        "Higher" means PROMOTED_TO_BACKTEST or PROMOTED_TO_LIVE.
        Correctly loads v1 or v2 specs based on metadata.
        """
        eligible_statuses = {
            StrategyStatus.APPROVED,
            StrategyStatus.PROMOTED_TO_BACKTEST,
            StrategyStatus.PROMOTED_TO_LIVE,
        }
        candidates: list[tuple[str, str, str]] = []  # (version, name, format)
        for mp in sorted(self.registry_dir.glob(f"{name}_v*.meta.json")):
            try:
                meta = StrategyMetadata.load(mp)
            except Exception:
                continue
            if meta.status in eligible_statuses:
                candidates.append((meta.version, meta.name, meta.spec_format))

        if not candidates:
            raise FileNotFoundError(
                f"No approved strategy named '{name}' in registry"
            )
        # take the last (highest version) after sort
        ver, nm, fmt = candidates[-1]
        return _load_spec_by_format(self._spec_path(nm, ver), fmt)

    # -- execution gate -------------------------------------------------------

    #: Statuses that permit backtest execution.
    BACKTEST_ELIGIBLE: set[StrategyStatus] = {
        StrategyStatus.APPROVED,
        StrategyStatus.PROMOTED_TO_BACKTEST,
        StrategyStatus.PROMOTED_TO_LIVE,
    }

    #: Statuses that permit live execution.
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
        """Verify that a strategy is eligible for execution.

        Checks:
        1. Metadata exists for the given name + version.
        2. ``static_review_passed`` is ``True``.
        3. Status is in the eligible set (backtest or live).

        Returns the metadata on success.  Raises ``PermissionError`` on
        gate failure.
        """
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
    ) -> AnySpec:
        """Load a spec only if it passes the execution gate.

        This is the **only** path the execution plane should use to obtain
        a spec.  It guarantees version-pinned, gate-checked access.
        Returns the correct spec type (v1 or v2).
        """
        self.check_execution_gate(name, version, require_live=require_live)
        return self.load_spec(name, version)

    # -- compile shortcut -----------------------------------------------------

    def compile(self, name: str, version: str):
        """Load and compile a strategy spec (version-pinned, v1/v2 aware)."""
        spec = self.load_spec(name, version)
        return compile_strategy(spec)

    # -- iteration ------------------------------------------------------------

    def iter_specs(self) -> Iterator[AnySpec]:
        """Iterate over all strategy specs in the registry (v1 and v2)."""
        for path in sorted(self.registry_dir.glob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            try:
                fmt = _detect_spec_format(path)
                yield _load_spec_by_format(path, fmt)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", path, exc)

    # -- legacy compat (deprecated) -------------------------------------------

    def save(self, spec: AnySpec) -> Path:
        """Deprecated: use :meth:`save_spec` instead."""
        return self.save_spec(spec)

    def load(self, name: str, version: str | None = None) -> AnySpec:
        """Deprecated: use :meth:`load_spec` with explicit version."""
        resolved = self.resolve_version(name, version)
        return self.load_spec(name, resolved)

    def list_strategies(self) -> list[dict]:
        """Deprecated: use :meth:`list_specs` instead."""
        result = []
        for path in sorted(self.registry_dir.glob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            try:
                fmt = _detect_spec_format(path)
                spec = _load_spec_by_format(path, fmt)
                info: dict = {
                    "name": spec.name,
                    "version": spec.version,
                    "description": spec.description,
                    "spec_format": fmt,
                    "path": str(path),
                }
                if fmt == "v1":
                    info["n_signal_rules"] = len(spec.signal_rules)
                    info["n_filters"] = len(spec.filters)
                    info["n_exit_rules"] = len(spec.exit_rules)
                else:
                    info["n_entry_policies"] = len(spec.entry_policies)
                    info["n_exit_policies"] = len(spec.exit_policies)
                result.append(info)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", path, exc)
        return result
