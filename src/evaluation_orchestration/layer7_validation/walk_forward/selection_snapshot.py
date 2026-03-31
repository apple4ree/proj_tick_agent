"""Selection-context resolution and snapshot artifacts for walk-forward decisions."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from evaluation_orchestration.layer6_evaluator.family_aggregation import FamilyAggregation
from strategy_block.strategy_registry.family_fingerprint import FamilyFingerprintBuilder
from strategy_block.strategy_registry.family_index import FamilyIndex
from strategy_block.strategy_registry.trial_accounting import TrialAccounting
from strategy_block.strategy_registry.trial_registry import TrialRecord, TrialRegistry
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import get_paths, load_config


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _selection_root(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cfg, Mapping):
        return {}
    if isinstance(cfg.get("selection"), Mapping):
        return dict(cfg["selection"])
    return dict(cfg)


class SelectionContextResolver:
    """Build best-effort family/trial context for selection discipline."""

    def __init__(
        self,
        *,
        trial_accounting: TrialAccounting | None = None,
        family_aggregation: FamilyAggregation | None = None,
        fingerprint_builder: FamilyFingerprintBuilder | None = None,
    ) -> None:
        self._trial_accounting = trial_accounting or TrialAccounting()
        self._family_aggregation = family_aggregation or FamilyAggregation()
        self._fingerprint_builder = fingerprint_builder or FamilyFingerprintBuilder()

    def build_family_context(
        self,
        *,
        spec_path: str,
        trial_id: str | None = None,
        profile: str | None = None,
        config_path: str | None = None,
        app_cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "trial_id": trial_id,
            "family_id": None,
            "trial_count_for_family": 0,
            "active_trial_count_for_family": 0,
            "global_trial_count": 0,
            "global_active_trial_count": 0,
            "global_rejected_trial_count": 0,
            "same_family_pass_candidate_count": 0,
            "family_pass_rate": None,
            "duplicate_match_type": "none",
            "duplicate_neighbor_score": 0.0,
            "duplicate_neighbor_lookup": {},
            "family_summary": {},
            "trial_accounting_snapshot": {},
            "family_fingerprint": {},
            "context_errors": [],
        }

        cfg = app_cfg if isinstance(app_cfg, dict) else {}
        if not cfg:
            try:
                cfg = load_config(config_path=config_path, profile=profile)
            except Exception as exc:  # noqa: BLE001
                context["context_errors"].append(f"config:{exc}")
                cfg = {}

        paths = get_paths(cfg)
        records: list[TrialRecord] = []
        trial_record: TrialRecord | None = None

        try:
            trial_registry = TrialRegistry(Path(paths["registry_dir"]) / "trials")
            records = trial_registry.list_all()
            accounting_snapshot = self._trial_accounting.build_snapshot(records)
            context["trial_accounting_snapshot"] = asdict(accounting_snapshot)
            context["global_trial_count"] = accounting_snapshot.total_trials
            context["global_active_trial_count"] = accounting_snapshot.active_trials
            context["global_rejected_trial_count"] = accounting_snapshot.rejected_trials
            if trial_id:
                trial_record = trial_registry.get(trial_id)
                if trial_record is not None:
                    context["family_id"] = trial_record.family_id
                    context["trial_stage"] = trial_record.stage
                    context["trial_status"] = trial_record.status
        except Exception as exc:  # noqa: BLE001
            context["context_errors"].append(f"registry:{exc}")

        fingerprint = None
        try:
            spec = StrategySpecV2.load(spec_path)
            metadata = dict(trial_record.metadata or {}) if trial_record is not None else {}
            fingerprint = self._fingerprint_builder.build(spec, metadata=metadata)
            context["family_fingerprint"] = asdict(fingerprint)
            if not context.get("family_id"):
                context["family_id"] = fingerprint.family_id
        except Exception as exc:  # noqa: BLE001
            context["context_errors"].append(f"fingerprint:{exc}")

        family_id = str(context.get("family_id") or "").strip() or None
        context["family_id"] = family_id
        family_reports = self._load_family_reports(
            [record for record in records if family_id and record.family_id == family_id]
        )
        family_summary = self._family_aggregation.summarize_family_runs(
            family_id=family_id or "",
            records=records,
            walk_forward_reports=family_reports,
        )
        context["family_summary"] = family_summary
        context["trial_count_for_family"] = _safe_int(
            family_summary.get("family_trial_count")
        )
        context["active_trial_count_for_family"] = _safe_int(
            family_summary.get("family_active_count")
        )
        context["same_family_pass_candidate_count"] = _safe_int(
            family_summary.get("family_pass_candidate_count")
        )
        context["family_pass_rate"] = family_summary.get("family_pass_rate")

        if fingerprint is not None:
            try:
                family_index = FamilyIndex(Path(paths["outputs_dir"]) / "trials" / "family_index")
                duplicate_lookup = family_index.find_duplicate_or_neighbor(fingerprint)
                duplicate_lookup = self._normalize_duplicate_lookup(
                    duplicate_lookup,
                    trial_id=trial_id,
                    family_id=family_id,
                )
                if duplicate_lookup is not None:
                    context["duplicate_neighbor_lookup"] = duplicate_lookup
                    context["duplicate_match_type"] = str(
                        duplicate_lookup.get("match_type") or "none"
                    ).strip().lower()
                    context["duplicate_neighbor_score"] = _safe_float(
                        duplicate_lookup.get("similarity"),
                        0.0,
                    )
            except Exception as exc:  # noqa: BLE001
                context["context_errors"].append(f"family_index:{exc}")

        return context

    def _load_family_reports(self, records: list[TrialRecord]) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for record in records:
            metadata = dict(record.metadata or {})
            report_path = str(metadata.get("walk_forward_report_path") or "").strip()
            if not report_path:
                continue
            path = Path(report_path)
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                reports.append(payload)
        return reports

    def _normalize_duplicate_lookup(
        self,
        lookup: dict[str, Any] | None,
        *,
        trial_id: str | None,
        family_id: str | None,
    ) -> dict[str, Any] | None:
        if not isinstance(lookup, Mapping):
            return None
        normalized = dict(lookup)
        member_trial_ids = [
            str(value) for value in normalized.get("member_trial_ids") or []
            if str(value)
        ]
        normalized["member_trial_ids"] = member_trial_ids
        normalized["member_count"] = len(member_trial_ids)

        if trial_id and normalized.get("family_id") == family_id:
            other_member_ids = [member for member in member_trial_ids if member != trial_id]
            if not other_member_ids:
                return None
            normalized["member_trial_ids"] = other_member_ids
            normalized["member_count"] = len(other_member_ids)
        return normalized


class SelectionSnapshotBuilder:
    """Build/save selection trace artifacts for audit and later analysis."""

    def build(
        self,
        *,
        report: dict[str, Any],
        family_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision = dict(report.get("decision") or {})
        decision_metadata = dict(decision.get("metadata") or {})
        compact_family_context = dict(family_context or {}) if isinstance(family_context, Mapping) else {}

        per_window_score_summary: list[dict[str, Any]] = []
        for idx, window in enumerate(report.get("window_results") or []):
            if not isinstance(window, Mapping):
                continue
            metadata = dict(window.get("metadata") or {})
            window_payload = dict(window.get("window") or {})
            per_window_score_summary.append(
                {
                    "window_index": _safe_int(metadata.get("window_index"), idx),
                    "forward_start": window_payload.get("forward_start"),
                    "forward_end": window_payload.get("forward_end"),
                    "total_score": _safe_float(window.get("total_score"), 0.0),
                    "pre_context_total_score": _safe_float(
                        metadata.get("pre_context_total_score"),
                        _safe_float(window.get("total_score"), 0.0),
                    ),
                    "context_penalty_total": _safe_float(
                        metadata.get("context_penalty_total"),
                        0.0,
                    ),
                    "components": dict(window.get("components") or {}),
                    "penalties": dict(window.get("penalties") or {}),
                    "flags": dict(metadata.get("flags") or {}),
                    "valid": bool(metadata.get("valid", True)),
                }
            )

        return {
            "trial_id": report.get("trial_id"),
            "spec_path": report.get("spec_path"),
            "mode": report.get("mode") or report.get("execution_mode"),
            "trial_accounting_snapshot": dict(
                compact_family_context.get("trial_accounting_snapshot") or {}
            ),
            "family_context": compact_family_context,
            "duplicate_neighbor_lookup": dict(
                compact_family_context.get("duplicate_neighbor_lookup") or {}
            ),
            "per_window_score_summary": per_window_score_summary,
            "aggregate_score_summary": {
                "pre_context_aggregate_score": _safe_float(
                    decision_metadata.get("pre_context_aggregate_score"),
                    _safe_float(decision.get("aggregate_score"), 0.0),
                ),
                "before_family_penalty": _safe_float(
                    decision_metadata.get("base_aggregate_score"),
                    _safe_float(decision.get("aggregate_score"), 0.0),
                ),
                "after_family_penalty": _safe_float(
                    decision.get("aggregate_score"),
                    0.0,
                ),
                "selector_family_penalty_total": _safe_float(
                    decision_metadata.get("selector_family_penalty_total"),
                    0.0,
                ),
            },
            "final_selection": {
                "passed": bool(decision.get("passed", False)),
                "reasons": [str(reason) for reason in decision.get("reasons") or []],
                "applied_penalty_reasons": [
                    str(reason)
                    for reason in decision_metadata.get("applied_penalty_reasons") or []
                ],
                "metadata": decision_metadata,
            },
        }

    def target_path(self, *, report: dict[str, Any], cfg: Mapping[str, Any] | None = None) -> Path:
        selection_root = _selection_root(cfg)
        snapshot_cfg = selection_root.get("selection_snapshot")
        snapshot_cfg = dict(snapshot_cfg) if isinstance(snapshot_cfg, Mapping) else {}
        output_root = Path(str(snapshot_cfg.get("output_root") or "outputs/selection_snapshots"))
        trial_id = str(report.get("trial_id") or "").strip()
        spec_path = str(report.get("spec_path") or "").strip()
        identifier = trial_id or Path(spec_path).stem or "adhoc"
        return output_root / identifier / "selection_snapshot.json"

    def save(self, path: str | Path, snapshot: dict[str, Any]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return target
