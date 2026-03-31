"""Family-level rollups for selection discipline and audit traces."""
from __future__ import annotations

from typing import Any

from strategy_block.strategy_registry.trial_registry import TrialRecord


_PASS_CANDIDATE_STAGES: frozenset[str] = frozenset(
    {"WF_PASSED", "PROMOTION_CANDIDATE", "CONTRACT_EXPORTED", "HANDOFF_READY"}
)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class FamilyAggregation:
    """Summarize family-level trial/search pressure from registry records."""

    def summarize_family_runs(
        self,
        *,
        family_id: str,
        records: list[TrialRecord],
        walk_forward_reports: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        target = str(family_id or "").strip()
        family_records = [record for record in records if target and record.family_id == target]
        active_records = [record for record in family_records if record.status == "ACTIVE"]
        rejected_records = [record for record in family_records if record.status == "REJECTED"]
        pass_candidate_count = sum(
            1
            for record in active_records
            if str(record.stage or "").strip() in _PASS_CANDIDATE_STAGES
        )

        aggregate_score_summary: dict[str, Any] | None = None
        pass_rate: float | None = None
        report_count = 0
        pass_count = 0

        scores: list[float] = []
        for report in walk_forward_reports or []:
            if not isinstance(report, dict):
                continue
            decision = report.get("decision")
            if not isinstance(decision, dict):
                continue
            report_count += 1
            if bool(decision.get("passed", False)):
                pass_count += 1
            aggregate_score = _safe_float(decision.get("aggregate_score"))
            if aggregate_score is not None:
                scores.append(aggregate_score)

        if report_count > 0:
            pass_rate = round(pass_count / report_count, 6)
        if scores:
            aggregate_score_summary = {
                "count": len(scores),
                "min": round(min(scores), 6),
                "max": round(max(scores), 6),
                "mean": round(sum(scores) / len(scores), 6),
            }

        return {
            "family_id": target or None,
            "family_trial_count": len(family_records),
            "family_active_count": len(active_records),
            "family_reject_count": len(rejected_records),
            "family_pass_candidate_count": pass_candidate_count,
            "family_report_count": report_count,
            "family_pass_count": pass_count,
            "family_pass_rate": pass_rate,
            "family_aggregate_score_summary": aggregate_score_summary,
        }
