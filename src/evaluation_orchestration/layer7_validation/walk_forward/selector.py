"""Deterministic walk-forward selection decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import pstdev
from typing import Any, Mapping

from .harness import WalkForwardRunResult


@dataclass
class WalkForwardDecision:
    passed: bool
    reasons: list[str]
    aggregate_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


_DEFAULT_FAMILY_PENALTY_CFG: dict[str, Any] = {
    "enabled": True,
    "weight": 0.15,
    "soft_family_trial_count": 3,
    "hard_family_trial_count": 6,
}

_DEFAULT_DUPLICATE_PENALTY_CFG: dict[str, Any] = {
    "enabled": True,
    "weight": 0.8,
    "hard_fail_similarity": 0.985,
}

_DEFAULT_NEIGHBOR_PENALTY_CFG: dict[str, Any] = {
    "enabled": True,
    "weight": 0.3,
}


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


def _selector_cfg(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cfg, Mapping):
        return {}
    root: Mapping[str, Any] = cfg
    if isinstance(root.get("selection"), Mapping):
        root = root["selection"]
    if isinstance(root.get("walk_forward"), Mapping):
        root = root["walk_forward"]
    if isinstance(root.get("selector"), Mapping):
        return dict(root["selector"])
    return dict(root)


def _merge_cfg(base: Mapping[str, Any], override: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if isinstance(override, Mapping):
        for key, value in override.items():
            merged[key] = value
    return merged


class WalkForwardSelector:
    """Apply deterministic pass/fail rules over window scores."""

    def select(
        self,
        window_results: list[WalkForwardRunResult],
        cfg: dict[str, Any],
        *,
        family_context: dict[str, Any] | None = None,
    ) -> WalkForwardDecision:
        selection_root = _selection_root(cfg)
        selector = _selector_cfg(cfg)
        family_penalty_cfg = _merge_cfg(
            _DEFAULT_FAMILY_PENALTY_CFG,
            selection_root.get("family_penalty") if isinstance(selection_root.get("family_penalty"), Mapping) else None,
        )
        duplicate_penalty_cfg = _merge_cfg(
            _DEFAULT_DUPLICATE_PENALTY_CFG,
            selection_root.get("duplicate_penalty") if isinstance(selection_root.get("duplicate_penalty"), Mapping) else None,
        )
        neighbor_penalty_cfg = _merge_cfg(
            _DEFAULT_NEIGHBOR_PENALTY_CFG,
            selection_root.get("neighbor_penalty") if isinstance(selection_root.get("neighbor_penalty"), Mapping) else None,
        )

        min_windows = max(1, int(selector.get("min_windows", 2)))
        min_pass_windows = max(1, int(selector.get("min_pass_windows", 1)))
        min_average_score = _safe_float(selector.get("min_average_score", -0.25))
        min_window_score = _safe_float(selector.get("min_window_score", -0.75))
        max_churn_heavy_share = _safe_float(selector.get("max_churn_heavy_share", 0.7))
        max_cost_dominated_share = _safe_float(selector.get("max_cost_dominated_share", 0.8))
        max_adverse_selection_share = _safe_float(selector.get("max_adverse_selection_share", 0.8))
        max_score_std = _safe_float(selector.get("max_score_std", 2.5))
        max_same_family_promoted_candidates = max(
            0,
            _safe_int(selector.get("max_same_family_promoted_candidates"), 2),
        )

        valid_results = [
            result for result in window_results
            if bool(result.selection_score.metadata.get("valid", True))
        ]

        reasons: list[str] = []
        if len(valid_results) < min_windows:
            reasons.append(
                f"too_few_valid_runs: valid={len(valid_results)} min_windows={min_windows}"
            )

        scores = [float(result.selection_score.total_score) for result in valid_results]
        pre_context_scores = [
            _safe_float(
                result.selection_score.metadata.get("pre_context_total_score"),
                float(result.selection_score.total_score),
            )
            for result in valid_results
        ]
        base_aggregate_score = sum(scores) / len(scores) if scores else -1_000_000_000.0
        pre_context_aggregate_score = (
            sum(pre_context_scores) / len(pre_context_scores)
            if pre_context_scores else base_aggregate_score
        )
        pass_windows = sum(1 for s in scores if s >= min_window_score)

        selector_penalties, applied_penalty_reasons, hard_fail_reasons = self._family_penalties(
            family_context=family_context,
            family_penalty_cfg=family_penalty_cfg,
            duplicate_penalty_cfg=duplicate_penalty_cfg,
            neighbor_penalty_cfg=neighbor_penalty_cfg,
            max_same_family_promoted_candidates=max_same_family_promoted_candidates,
        )
        selector_penalty_total = sum(selector_penalties.values())
        aggregate_score = base_aggregate_score - selector_penalty_total
        reasons.extend(hard_fail_reasons)

        if pass_windows < min_pass_windows:
            reasons.append(
                f"insufficient_pass_windows: pass={pass_windows} min_pass_windows={min_pass_windows}"
            )

        if scores and aggregate_score < min_average_score:
            reasons.append(
                f"average_score_below_threshold: avg={aggregate_score:.4f} min_average_score={min_average_score:.4f}"
            )
            reasons.extend(
                reason
                for reason in ("family_trial_count_too_high", "same_family_candidate_pressure", "duplicate_candidate_penalty_applied")
                if reason in applied_penalty_reasons and reason not in reasons
            )

        flags = [
            dict(result.selection_score.metadata.get("flags") or {})
            for result in valid_results
        ]
        churn_share = (
            sum(1 for f in flags if bool(f.get("churn_heavy"))) / len(flags)
            if flags else 1.0
        )
        cost_share = (
            sum(1 for f in flags if bool(f.get("cost_dominated"))) / len(flags)
            if flags else 1.0
        )
        adverse_share = (
            sum(1 for f in flags if bool(f.get("adverse_selection_dominated"))) / len(flags)
            if flags else 1.0
        )

        if flags and churn_share > max_churn_heavy_share:
            reasons.append(
                f"churn_heavy_share_too_high: share={churn_share:.3f} max={max_churn_heavy_share:.3f}"
            )
        if flags and cost_share > max_cost_dominated_share:
            reasons.append(
                f"cost_dominated_share_too_high: share={cost_share:.3f} max={max_cost_dominated_share:.3f}"
            )
        if flags and adverse_share > max_adverse_selection_share:
            reasons.append(
                f"adverse_selection_share_too_high: share={adverse_share:.3f} max={max_adverse_selection_share:.3f}"
            )

        score_std = pstdev(scores) if len(scores) >= 2 else 0.0
        if len(scores) >= 2 and score_std > max_score_std:
            reasons.append(
                f"score_volatility_too_high: std={score_std:.3f} max={max_score_std:.3f}"
            )

        metadata = {
            "n_windows": len(window_results),
            "n_valid_windows": len(valid_results),
            "n_pass_windows": int(pass_windows),
            "scores": scores,
            "pre_context_scores": pre_context_scores,
            "min_window_score": min_window_score,
            "min_average_score": min_average_score,
            "churn_heavy_share": float(churn_share),
            "cost_dominated_share": float(cost_share),
            "adverse_selection_dominated_share": float(adverse_share),
            "score_std": float(score_std),
            "pre_context_aggregate_score": float(pre_context_aggregate_score),
            "base_aggregate_score": float(base_aggregate_score),
            "selector_family_penalties": {k: float(v) for k, v in selector_penalties.items()},
            "selector_family_penalty_total": float(selector_penalty_total),
            "applied_penalty_reasons": list(applied_penalty_reasons),
            "family_context": self._compact_family_context(family_context),
            "duplicate_neighbor_lookup": self._duplicate_lookup(family_context),
        }

        return WalkForwardDecision(
            passed=(len(reasons) == 0),
            reasons=reasons,
            aggregate_score=float(aggregate_score),
            metadata=metadata,
        )

    def _family_penalties(
        self,
        *,
        family_context: dict[str, Any] | None,
        family_penalty_cfg: dict[str, Any],
        duplicate_penalty_cfg: dict[str, Any],
        neighbor_penalty_cfg: dict[str, Any],
        max_same_family_promoted_candidates: int,
    ) -> tuple[dict[str, float], list[str], list[str]]:
        if not isinstance(family_context, Mapping):
            return {}, [], []

        penalties: dict[str, float] = {}
        applied_penalty_reasons: list[str] = []
        hard_fail_reasons: list[str] = []

        family_enabled = bool(family_penalty_cfg.get("enabled", True))
        family_weight = max(0.0, _safe_float(family_penalty_cfg.get("weight"), 0.0))
        family_trial_count = _safe_int(
            family_context.get("trial_count_for_family", family_context.get("family_trial_count"))
        )
        active_family_count = _safe_int(
            family_context.get(
                "active_trial_count_for_family",
                family_context.get("family_active_count", family_context.get("active_family_count")),
            )
        )
        soft_family_trial_count = max(
            0,
            _safe_int(family_penalty_cfg.get("soft_family_trial_count"), 0),
        )
        hard_family_trial_count = max(
            soft_family_trial_count,
            _safe_int(family_penalty_cfg.get("hard_family_trial_count"), soft_family_trial_count),
        )

        if family_enabled and family_trial_count > soft_family_trial_count:
            penalties["family_trial_pressure"] = (
                family_weight
                * 0.5
                * ((family_trial_count - soft_family_trial_count) / max(float(soft_family_trial_count), 1.0))
                * (1.0 + 0.25 * max(active_family_count - 1, 0))
            )
            applied_penalty_reasons.append("family_trial_count_too_high")

        if family_enabled and family_trial_count > hard_family_trial_count:
            penalties["excessive_search_pressure"] = (
                family_weight
                * ((family_trial_count - hard_family_trial_count) / max(float(hard_family_trial_count - soft_family_trial_count), 1.0))
            )
            if "family_trial_count_too_high" not in applied_penalty_reasons:
                applied_penalty_reasons.append("family_trial_count_too_high")

        same_family_pass_candidate_count = _safe_int(
            family_context.get("same_family_pass_candidate_count")
        )
        if family_enabled and same_family_pass_candidate_count > max_same_family_promoted_candidates:
            penalties["same_family_candidate_pressure"] = (
                family_weight
                * 0.75
                * (same_family_pass_candidate_count - max_same_family_promoted_candidates)
            )
            applied_penalty_reasons.append("same_family_candidate_pressure")

        duplicate_lookup = self._duplicate_lookup(family_context)
        duplicate_match_type = str(
            family_context.get(
                "duplicate_match_type",
                duplicate_lookup.get("match_type") or "none",
            )
        ).strip().lower()
        duplicate_neighbor_score = _safe_float(
            family_context.get(
                "duplicate_neighbor_score",
                duplicate_lookup.get("similarity"),
            )
        )

        if duplicate_match_type == "duplicate" and bool(duplicate_penalty_cfg.get("enabled", True)):
            penalties["duplicate_candidate_penalty"] = (
                max(0.0, _safe_float(duplicate_penalty_cfg.get("weight"), 0.0))
                * max(0.0, duplicate_neighbor_score)
            )
            applied_penalty_reasons.append("duplicate_candidate_penalty_applied")
            hard_fail_similarity = _safe_float(
                duplicate_penalty_cfg.get("hard_fail_similarity"),
                0.985,
            )
            if duplicate_neighbor_score >= hard_fail_similarity:
                hard_fail_reasons.append(
                    f"duplicate_candidate_penalty_applied: similarity={duplicate_neighbor_score:.3f} threshold={hard_fail_similarity:.3f}"
                )
        elif duplicate_match_type == "neighbor" and bool(neighbor_penalty_cfg.get("enabled", True)):
            penalties["neighbor_candidate_penalty"] = (
                max(0.0, _safe_float(neighbor_penalty_cfg.get("weight"), 0.0))
                * max(0.0, duplicate_neighbor_score)
            )

        return penalties, self._dedupe(applied_penalty_reasons), self._dedupe(hard_fail_reasons)

    def _compact_family_context(self, family_context: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(family_context, Mapping):
            return None
        return {
            "family_id": family_context.get("family_id"),
            "trial_count_for_family": _safe_int(
                family_context.get("trial_count_for_family", family_context.get("family_trial_count"))
            ),
            "active_trial_count_for_family": _safe_int(
                family_context.get(
                    "active_trial_count_for_family",
                    family_context.get("family_active_count", family_context.get("active_family_count")),
                )
            ),
            "family_pass_rate": family_context.get("family_pass_rate"),
            "same_family_pass_candidate_count": _safe_int(
                family_context.get("same_family_pass_candidate_count")
            ),
            "duplicate_match_type": family_context.get("duplicate_match_type"),
            "duplicate_neighbor_score": _safe_float(
                family_context.get("duplicate_neighbor_score"),
                0.0,
            ),
            "context_errors": list(family_context.get("context_errors") or []),
        }

    def _duplicate_lookup(self, family_context: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(family_context, Mapping):
            return {}
        lookup = family_context.get("duplicate_neighbor_lookup")
        return dict(lookup) if isinstance(lookup, Mapping) else {}

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out
