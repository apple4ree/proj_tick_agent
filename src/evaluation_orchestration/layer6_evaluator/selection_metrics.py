"""Deterministic selection metrics for walk-forward validation."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class SelectionScore:
    """Score decomposition for a single backtest run."""

    total_score: float
    components: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


_DEFAULT_SCORING_CFG: dict[str, Any] = {
    "scales": {
        "net_pnl": 100000.0,
        "pnl_per_parent": 3000.0,
        "pnl_per_fill": 1000.0,
    },
    "components": {
        "edge_net_pnl_weight": 1.0,
        "edge_pnl_per_parent_weight": 0.7,
        "edge_pnl_per_fill_weight": 0.4,
    },
    "targets": {
        "children_per_parent": 3.0,
        "cancel_rate": 0.35,
        "maker_fill_ratio": 0.35,
        "adverse_selection_share": 0.45,
    },
    "penalties": {
        "children_per_parent_weight": 0.12,
        "cancel_rate_weight": 1.2,
        "child_order_count_weight": 0.001,
        "maker_fill_ratio_gap_weight": 1.0,
        "queue_blocked_count_weight": 0.002,
        "blocked_miss_count_weight": 0.01,
        "commission_weight": 0.0005,
        "slippage_weight": 0.0005,
        "impact_weight": 0.0005,
        "adverse_selection_weight": 1.0,
    },
    "flags": {
        "children_per_parent_churn_heavy": 8.0,
        "cancel_rate_churn_heavy": 0.7,
        "maker_fill_ratio_queue_ineffective": 0.15,
        "blocked_miss_queue_ineffective": 10.0,
        "cost_to_abs_pnl_dominated": 1.0,
        "adverse_selection_dominated": 0.6,
    },
}

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


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


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


class SelectionMetrics:
    """Compute deterministic selection scores from summary/diagnostics."""

    def __init__(self, cfg: Mapping[str, Any] | None = None) -> None:
        selection = _selection_root(cfg)
        scoring_cfg = dict(selection.get("scoring") or selection)
        self._cfg = _deep_merge(_DEFAULT_SCORING_CFG, scoring_cfg)
        self._family_cfg = _deep_merge(
            _DEFAULT_FAMILY_PENALTY_CFG,
            dict(selection.get("family_penalty") or {}),
        )
        self._duplicate_cfg = _deep_merge(
            _DEFAULT_DUPLICATE_PENALTY_CFG,
            dict(selection.get("duplicate_penalty") or {}),
        )
        self._neighbor_cfg = _deep_merge(
            _DEFAULT_NEIGHBOR_PENALTY_CFG,
            dict(selection.get("neighbor_penalty") or {}),
        )

    def score_run(
        self,
        summary: dict[str, Any],
        diagnostics: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> SelectionScore:
        if not isinstance(summary, dict) or not summary:
            return SelectionScore(
                total_score=-1_000_000_000.0,
                components={},
                penalties={"missing_summary": 1_000_000_000.0},
                metadata={"valid": False, "reason": "missing_summary"},
            )
        if "execution_error" in summary:
            return SelectionScore(
                total_score=-1_000_000_000.0,
                components={},
                penalties={"execution_error": 1_000_000_000.0},
                metadata={"valid": False, "reason": str(summary.get("execution_error"))},
            )

        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        lifecycle = diagnostics.get("lifecycle") if isinstance(diagnostics.get("lifecycle"), dict) else {}
        queue = diagnostics.get("queue") if isinstance(diagnostics.get("queue"), dict) else {}
        cancel_reasons = diagnostics.get("cancel_reasons") if isinstance(diagnostics.get("cancel_reasons"), dict) else {}
        cancel_shares = cancel_reasons.get("shares") if isinstance(cancel_reasons.get("shares"), dict) else {}

        parent_order_count = _safe_float(lifecycle.get("parent_order_count", summary.get("parent_order_count")))
        child_order_count = _safe_float(lifecycle.get("child_order_count", summary.get("child_order_count")))
        n_fills = _safe_float(lifecycle.get("n_fills", summary.get("n_fills")))
        cancel_rate = _safe_float(lifecycle.get("cancel_rate", summary.get("cancel_rate")))
        children_per_parent = _safe_float(
            lifecycle.get("children_per_parent", summary.get("children_per_parent")),
            default=(child_order_count / parent_order_count if parent_order_count > 0 else 0.0),
        )

        maker_fill_ratio = _safe_float(queue.get("maker_fill_ratio", summary.get("maker_fill_ratio")))
        queue_blocked_count = _safe_float(queue.get("queue_blocked_count"))
        blocked_miss_count = _safe_float(queue.get("blocked_miss_count"))
        queue_ready_count = _safe_float(queue.get("queue_ready_count"))

        net_pnl = _safe_float(summary.get("net_pnl"))
        total_commission = _safe_float(summary.get("total_commission"))
        total_slippage = _safe_float(summary.get("total_slippage"))
        total_impact = _safe_float(summary.get("total_impact"))

        adverse_selection_share = _safe_float(cancel_shares.get("adverse_selection"))

        scales = self._cfg["scales"]
        comp_w = self._cfg["components"]
        targets = self._cfg["targets"]
        penalties_cfg = self._cfg["penalties"]
        flags_cfg = self._cfg["flags"]

        pnl_per_parent = net_pnl / max(parent_order_count, 1.0)
        pnl_per_fill = net_pnl / max(n_fills, 1.0)

        components = {
            "edge_net_pnl": comp_w["edge_net_pnl_weight"] * math.tanh(net_pnl / max(_safe_float(scales["net_pnl"], 1.0), 1.0)),
            "edge_pnl_per_parent": comp_w["edge_pnl_per_parent_weight"]
            * math.tanh(pnl_per_parent / max(_safe_float(scales["pnl_per_parent"], 1.0), 1.0)),
            "edge_pnl_per_fill": comp_w["edge_pnl_per_fill_weight"]
            * math.tanh(pnl_per_fill / max(_safe_float(scales["pnl_per_fill"], 1.0), 1.0)),
        }

        penalties = {
            "churn": (
                max(0.0, children_per_parent - _safe_float(targets["children_per_parent"]))
                * _safe_float(penalties_cfg["children_per_parent_weight"])
                + max(0.0, cancel_rate - _safe_float(targets["cancel_rate"]))
                * _safe_float(penalties_cfg["cancel_rate_weight"])
                + max(0.0, child_order_count - parent_order_count)
                * _safe_float(penalties_cfg["child_order_count_weight"])
            ),
            "queue_fragility": (
                max(0.0, _safe_float(targets["maker_fill_ratio"]) - maker_fill_ratio)
                * _safe_float(penalties_cfg["maker_fill_ratio_gap_weight"])
                + queue_blocked_count * _safe_float(penalties_cfg["queue_blocked_count_weight"])
                + blocked_miss_count * _safe_float(penalties_cfg["blocked_miss_count_weight"])
            ),
            "cost": (
                total_commission * _safe_float(penalties_cfg["commission_weight"])
                + total_slippage * _safe_float(penalties_cfg["slippage_weight"])
                + total_impact * _safe_float(penalties_cfg["impact_weight"])
            ),
            "adverse_selection": (
                max(0.0, adverse_selection_share - _safe_float(targets["adverse_selection_share"]))
                * _safe_float(penalties_cfg["adverse_selection_weight"])
            ),
        }

        component_total = sum(components.values())
        base_penalty_total = sum(penalties.values())
        pre_context_total_score = component_total - base_penalty_total
        context_penalties, context_metadata = self._resolve_context_penalties(context)
        penalties.update(context_penalties)
        context_penalty_total = sum(context_penalties.values())
        total_score = pre_context_total_score - context_penalty_total

        total_cost = total_commission + total_slippage + total_impact
        abs_pnl = max(abs(net_pnl), 1.0)

        flags = {
            "churn_heavy": (
                children_per_parent >= _safe_float(flags_cfg["children_per_parent_churn_heavy"])
                or cancel_rate >= _safe_float(flags_cfg["cancel_rate_churn_heavy"])
            ),
            "queue_ineffective": (
                maker_fill_ratio <= _safe_float(flags_cfg["maker_fill_ratio_queue_ineffective"])
                and (
                    blocked_miss_count >= _safe_float(flags_cfg["blocked_miss_queue_ineffective"])
                    or queue_ready_count <= 0.0
                )
            ),
            "cost_dominated": total_cost >= abs_pnl * _safe_float(flags_cfg["cost_to_abs_pnl_dominated"]),
            "adverse_selection_dominated": adverse_selection_share >= _safe_float(flags_cfg["adverse_selection_dominated"]),
        }

        metadata = {
            "valid": True,
            "net_pnl": net_pnl,
            "pnl_per_parent": pnl_per_parent,
            "pnl_per_fill": pnl_per_fill,
            "parent_order_count": parent_order_count,
            "child_order_count": child_order_count,
            "children_per_parent": children_per_parent,
            "cancel_rate": cancel_rate,
            "maker_fill_ratio": maker_fill_ratio,
            "queue_blocked_count": queue_blocked_count,
            "blocked_miss_count": blocked_miss_count,
            "adverse_selection_share": adverse_selection_share,
            "total_cost": total_cost,
            "flags": flags,
            "pre_context_total_score": float(pre_context_total_score),
            "context_penalty_total": float(context_penalty_total),
            "selection_context": context_metadata,
        }

        return SelectionScore(
            total_score=float(total_score),
            components={k: float(v) for k, v in components.items()},
            penalties={k: float(v) for k, v in penalties.items()},
            metadata=metadata,
        )

    def _resolve_context_penalties(
        self,
        context: dict[str, Any] | None,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        if not isinstance(context, Mapping):
            return {}, {"context_provided": False}

        duplicate_lookup = context.get("duplicate_neighbor_lookup")
        duplicate_lookup = dict(duplicate_lookup) if isinstance(duplicate_lookup, Mapping) else {}

        family_trial_count = _safe_int(
            context.get("trial_count_for_family", context.get("family_trial_count"))
        )
        active_family_count = _safe_int(
            context.get(
                "active_trial_count_for_family",
                context.get("family_active_count", context.get("active_family_count")),
            )
        )
        global_trial_count = _safe_int(context.get("global_trial_count"))
        family_pass_rate = context.get("family_pass_rate")
        family_pass_rate_value = (
            None if family_pass_rate is None else _safe_float(family_pass_rate)
        )
        duplicate_neighbor_score = _safe_float(
            context.get(
                "duplicate_neighbor_score",
                duplicate_lookup.get("similarity"),
            )
        )
        duplicate_match_type = str(
            context.get("duplicate_match_type", duplicate_lookup.get("match_type") or "none")
        ).strip().lower()

        penalties: dict[str, float] = {}

        family_enabled = bool(self._family_cfg.get("enabled", True))
        family_weight = max(0.0, _safe_float(self._family_cfg.get("weight"), 0.0))
        soft_count = max(0, _safe_int(self._family_cfg.get("soft_family_trial_count"), 0))
        hard_count = max(soft_count, _safe_int(self._family_cfg.get("hard_family_trial_count"), soft_count))
        active_multiplier = 1.0 + (0.25 * max(active_family_count - 1, 0))
        pass_rate_multiplier = 1.0
        if family_pass_rate_value is not None and family_pass_rate_value < 0.5:
            pass_rate_multiplier += min(0.5, 0.5 - family_pass_rate_value)

        family_crowding = 0.0
        excessive_search = 0.0
        if family_enabled and family_trial_count > soft_count:
            family_crowding = (
                family_weight
                * ((family_trial_count - soft_count) / max(float(soft_count), 1.0))
                * active_multiplier
                * pass_rate_multiplier
            )
        if family_enabled and family_trial_count > hard_count:
            excessive_search = (
                family_weight
                * 1.5
                * ((family_trial_count - hard_count) / max(float(hard_count - soft_count), 1.0))
            )

        penalties["family_crowding"] = family_crowding
        penalties["excessive_search"] = excessive_search

        duplicate_penalty = 0.0
        if duplicate_match_type == "duplicate" and bool(self._duplicate_cfg.get("enabled", True)):
            duplicate_penalty = max(0.0, _safe_float(self._duplicate_cfg.get("weight"), 0.0)) * max(0.0, duplicate_neighbor_score)
        elif duplicate_match_type == "neighbor" and bool(self._neighbor_cfg.get("enabled", True)):
            duplicate_penalty = max(0.0, _safe_float(self._neighbor_cfg.get("weight"), 0.0)) * max(0.0, duplicate_neighbor_score)
        penalties["duplicate_proximity"] = duplicate_penalty

        return penalties, {
            "context_provided": True,
            "family_id": context.get("family_id"),
            "trial_count_for_family": family_trial_count,
            "active_trial_count_for_family": active_family_count,
            "global_trial_count": global_trial_count,
            "family_pass_rate": family_pass_rate_value,
            "duplicate_match_type": duplicate_match_type,
            "duplicate_neighbor_score": float(max(0.0, duplicate_neighbor_score)),
            "duplicate_neighbor_lookup": duplicate_lookup,
            "family_penalty_cfg": {
                "enabled": family_enabled,
                "weight": family_weight,
                "soft_family_trial_count": soft_count,
                "hard_family_trial_count": hard_count,
            },
        }
