"""Walk-forward report assembly and persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .harness import WalkForwardRunResult
from .scorer import WalkForwardScorer
from .selection_snapshot import SelectionSnapshotBuilder
from .selector import WalkForwardDecision


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class WalkForwardReportBuilder:
    """Build/save compact walk-forward reports."""

    def __init__(
        self,
        scorer: WalkForwardScorer | None = None,
        selection_snapshot_builder: SelectionSnapshotBuilder | None = None,
    ) -> None:
        self._scorer = scorer or WalkForwardScorer()
        self._selection_snapshot_builder = selection_snapshot_builder or SelectionSnapshotBuilder()

    def build(
        self,
        decision: WalkForwardDecision,
        window_results: list[WalkForwardRunResult],
        *,
        family_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        windows = [self._scorer.score_window(result) for result in window_results]
        first_trial = next((result.trial_id for result in window_results if result.trial_id), None)

        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "trial_id": first_trial,
            "n_windows": len(window_results),
            "decision": {
                "passed": bool(decision.passed),
                "reasons": list(decision.reasons),
                "aggregate_score": float(decision.aggregate_score),
                "metadata": dict(decision.metadata),
            },
            "window_results": windows,
        }
        compact_family_context = self._compact_family_context(family_context)
        if compact_family_context is not None:
            report["family_context"] = compact_family_context
        return report

    def build_selection_snapshot(
        self,
        report: dict[str, Any],
        *,
        family_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._selection_snapshot_builder.build(
            report=report,
            family_context=family_context,
        )

    def save(
        self,
        out_dir: str,
        report: dict[str, Any],
        *,
        selection_cfg: Mapping[str, Any] | None = None,
        selection_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)

        snapshot_path: Path | None = None
        if isinstance(selection_snapshot, dict):
            snapshot_path = self._selection_snapshot_builder.target_path(
                report=report,
                cfg=selection_cfg,
            )
            report["selection_snapshot_path"] = str(snapshot_path.resolve())

        report_path = target / "walk_forward_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        saved_paths = {"report_path": str(report_path.resolve())}
        if snapshot_path is not None:
            self._selection_snapshot_builder.save(snapshot_path, selection_snapshot)
            saved_paths["selection_snapshot_path"] = str(snapshot_path.resolve())
        return saved_paths

    def _compact_family_context(
        self,
        family_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(family_context, Mapping):
            return None
        return {
            "family_id": family_context.get("family_id"),
            "trial_count_for_family": int(
                family_context.get("trial_count_for_family", family_context.get("family_trial_count", 0)) or 0
            ),
            "active_trial_count_for_family": int(
                family_context.get(
                    "active_trial_count_for_family",
                    family_context.get("family_active_count", family_context.get("active_family_count", 0)),
                ) or 0
            ),
            "family_pass_rate": family_context.get("family_pass_rate"),
            "same_family_pass_candidate_count": int(
                family_context.get("same_family_pass_candidate_count", 0) or 0
            ),
            "duplicate_match_type": family_context.get("duplicate_match_type"),
            "duplicate_neighbor_score": _safe_float(
                family_context.get("duplicate_neighbor_score"),
                0.0,
            ),
            "duplicate_neighbor_lookup": dict(
                family_context.get("duplicate_neighbor_lookup") or {}
            ),
            "context_errors": list(family_context.get("context_errors") or []),
        }
