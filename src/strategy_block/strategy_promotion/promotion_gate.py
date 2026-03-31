"""Deterministic promotion gate for deployment-candidate handoff."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from strategy_block.strategy_registry.trial_registry import TrialRecord


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


def _resolve_gate_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    if isinstance(cfg.get("promotion"), Mapping):
        promotion = dict(cfg.get("promotion") or {})
    else:
        promotion = dict(cfg)
    gate = promotion.get("gate")
    if isinstance(gate, Mapping):
        return dict(gate)
    return {}


@dataclass
class PromotionDecision:
    passed: bool
    reasons: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


class PromotionGate:
    """Apply deterministic hard-gate checks for promotion candidates."""

    def evaluate(
        self,
        *,
        trial_record: TrialRecord,
        walk_forward_report: dict[str, Any],
        cfg: dict[str, Any],
    ) -> PromotionDecision:
        gate_cfg = _resolve_gate_cfg(cfg)
        reasons: list[str] = []

        decision = dict(walk_forward_report.get("decision") or {})
        decision_meta = dict(decision.get("metadata") or {})

        wf_passed = bool(decision.get("passed", False))
        aggregate_score = _safe_float(decision.get("aggregate_score"), -1e9)
        n_valid_windows = _safe_int(decision_meta.get("n_valid_windows"), 0)
        n_pass_windows = _safe_int(decision_meta.get("n_pass_windows"), 0)
        forward_survival = (n_pass_windows / n_valid_windows) if n_valid_windows > 0 else 0.0

        churn_share = _safe_float(decision_meta.get("churn_heavy_share"), 0.0)
        cost_share = _safe_float(decision_meta.get("cost_dominated_share"), 0.0)
        adverse_share = _safe_float(decision_meta.get("adverse_selection_dominated_share"), 0.0)
        queue_share = self._flag_share(walk_forward_report, "queue_ineffective")

        if bool(gate_cfg.get("require_walk_forward_passed", True)) and not wf_passed:
            reasons.append("walk_forward_not_passed")

        min_aggregate_score = _safe_float(gate_cfg.get("min_aggregate_score"), -0.25)
        if aggregate_score < min_aggregate_score:
            reasons.append(
                f"aggregate_score_below_threshold:{aggregate_score:.6f}<{min_aggregate_score:.6f}"
            )

        min_valid_windows = max(1, _safe_int(gate_cfg.get("min_valid_windows"), 2))
        if n_valid_windows < min_valid_windows:
            reasons.append(
                f"insufficient_valid_windows:{n_valid_windows}<{min_valid_windows}"
            )

        min_forward_survival_ratio = _safe_float(gate_cfg.get("min_forward_survival_ratio"), 0.5)
        if forward_survival < min_forward_survival_ratio:
            reasons.append(
                f"forward_survival_below_threshold:{forward_survival:.6f}<{min_forward_survival_ratio:.6f}"
            )

        if churn_share > _safe_float(gate_cfg.get("max_churn_heavy_share"), 0.7):
            reasons.append("churn_heavy_share_too_high")
        if cost_share > _safe_float(gate_cfg.get("max_cost_dominated_share"), 0.8):
            reasons.append("cost_dominated_share_too_high")
        if adverse_share > _safe_float(gate_cfg.get("max_adverse_selection_dominated_share"), 0.8):
            reasons.append("adverse_selection_share_too_high")
        if queue_share > _safe_float(gate_cfg.get("max_queue_ineffective_share"), 0.8):
            reasons.append("queue_ineffective_share_too_high")

        if trial_record.status == "REJECTED":
            reasons.append("trial_already_rejected")

        if bool(gate_cfg.get("require_trial_active", True)) and trial_record.status != "ACTIVE":
            reasons.append(f"trial_status_not_active:{trial_record.status}")

        required_stages = gate_cfg.get(
            "required_trial_stages",
            [
                "BACKTESTED",
                "WF_PASSED",
                "PROMOTION_CANDIDATE",
                "CONTRACT_EXPORTED",
                "HANDOFF_READY",
            ],
        )
        if isinstance(required_stages, list) and required_stages:
            if trial_record.stage not in {str(v) for v in required_stages}:
                reasons.append(f"trial_stage_not_eligible:{trial_record.stage}")

        if bool(gate_cfg.get("require_family_id", True)) and not trial_record.family_id:
            reasons.append("missing_family_id")

        required_meta = gate_cfg.get("required_trial_metadata", [])
        if isinstance(required_meta, list):
            for key in required_meta:
                key_str = str(key)
                if key_str and key_str not in (trial_record.metadata or {}):
                    reasons.append(f"missing_trial_metadata:{key_str}")

        metadata = {
            "trial_id": trial_record.trial_id,
            "trial_stage": trial_record.stage,
            "trial_status": trial_record.status,
            "walk_forward_passed": wf_passed,
            "aggregate_score": aggregate_score,
            "n_valid_windows": n_valid_windows,
            "n_pass_windows": n_pass_windows,
            "forward_survival_ratio": round(forward_survival, 6),
            "churn_heavy_share": round(churn_share, 6),
            "cost_dominated_share": round(cost_share, 6),
            "adverse_selection_dominated_share": round(adverse_share, 6),
            "queue_ineffective_share": round(queue_share, 6),
            "gate_cfg": {
                "min_aggregate_score": min_aggregate_score,
                "min_valid_windows": min_valid_windows,
                "min_forward_survival_ratio": min_forward_survival_ratio,
                "max_churn_heavy_share": _safe_float(gate_cfg.get("max_churn_heavy_share"), 0.7),
                "max_cost_dominated_share": _safe_float(gate_cfg.get("max_cost_dominated_share"), 0.8),
                "max_adverse_selection_dominated_share": _safe_float(gate_cfg.get("max_adverse_selection_dominated_share"), 0.8),
                "max_queue_ineffective_share": _safe_float(gate_cfg.get("max_queue_ineffective_share"), 0.8),
            },
        }

        return PromotionDecision(passed=(len(reasons) == 0), reasons=reasons, metadata=metadata)

    def _flag_share(self, report: Mapping[str, Any], key: str) -> float:
        windows = report.get("window_results") or []
        if not windows:
            return 0.0
        positives = 0
        total = 0
        for window in windows:
            if not isinstance(window, Mapping):
                continue
            metadata = dict(window.get("metadata") or {})
            flags = dict(metadata.get("flags") or {})
            positives += 1 if bool(flags.get(key)) else 0
            total += 1
        return (positives / total) if total > 0 else 0.0
