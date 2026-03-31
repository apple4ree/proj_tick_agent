"""Deterministic coarse family fingerprinting for strategy candidates."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ExprNode, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2


@dataclass(frozen=True)
class FamilyFingerprint:
    family_id: str
    motif: str
    side_model: str
    execution_style: str
    horizon_bucket: str
    regime_shape: str
    feature_signature: str
    raw_signature: str

    def feature_set(self) -> set[str]:
        if not self.feature_signature or self.feature_signature == "none":
            return set()
        return {item for item in self.feature_signature.split("|") if item}


_MOTIF_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("imbalance", ("imbalance", "ofi", "book_imb")),
    ("momentum", ("momentum", "trend", "return", "roc", "velocity")),
    ("spread_reversion", ("spread", "zscore", "reversion", "mean_rev")),
    ("queue_depletion", ("queue", "depletion", "depth_drop", "book_depth")),
    ("microprice", ("microprice", "micro_price", "book_pressure", "mid_gap")),
)


class FamilyFingerprintBuilder:
    """Build a coarse fingerprint that groups structurally similar candidates."""

    def build(
        self,
        spec: StrategySpecV2,
        metadata: dict[str, Any] | None = None,
    ) -> FamilyFingerprint:
        features = self._collect_features(spec)
        motif = self._infer_motif(features)
        side_model = self._infer_side_model(spec)
        execution_style = self._infer_execution_style(spec)
        horizon_bucket = self._infer_horizon_bucket(spec, metadata or {})
        regime_shape = self._infer_regime_shape(spec)
        feature_signature = "|".join(features) if features else "none"
        coarse_feature_bucket = self._coarse_feature_bucket(features)

        family_signature_payload = {
            "motif": motif,
            "side_model": side_model,
            "execution_style": execution_style,
            "horizon_bucket": horizon_bucket,
            "regime_shape": regime_shape,
            "feature_bucket": coarse_feature_bucket,
        }
        family_signature = json.dumps(
            family_signature_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        digest = hashlib.sha1(family_signature.encode("utf-8")).hexdigest()[:16]
        family_id = f"fam_{digest}"

        raw_signature_payload = {
            **family_signature_payload,
            "feature_signature": feature_signature,
        }
        raw_signature = json.dumps(
            raw_signature_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )

        return FamilyFingerprint(
            family_id=family_id,
            motif=motif,
            side_model=side_model,
            execution_style=execution_style,
            horizon_bucket=horizon_bucket,
            regime_shape=regime_shape,
            feature_signature=feature_signature,
            raw_signature=raw_signature,
        )

    def _collect_features(self, spec: StrategySpecV2) -> list[str]:
        features = {
            feature.strip().lower()
            for feature in spec.collect_all_features()
            if isinstance(feature, str) and feature.strip()
        }
        return sorted(features)

    def _infer_motif(self, features: list[str]) -> str:
        if not features:
            return "generic"
        scores: list[tuple[int, str]] = []
        for motif, patterns in _MOTIF_PATTERNS:
            score = 0
            for feature in features:
                if any(pattern in feature for pattern in patterns):
                    score += 1
            scores.append((score, motif))
        best_score, best_motif = max(scores, key=lambda item: (item[0], -len(item[1]), item[1]))
        return best_motif if best_score > 0 else "generic"

    def _infer_side_model(self, spec: StrategySpecV2) -> str:
        sides = {entry.side.lower().strip() for entry in spec.entry_policies if entry.side}
        if sides == {"long"}:
            return "long_only"
        if sides == {"short"}:
            return "short_only"
        if {"long", "short"}.issubset(sides):
            return "bi_directional"
        return "unknown"

    def _infer_execution_style(self, spec: StrategySpecV2) -> str:
        policy = spec.execution_policy
        if policy is None:
            return "implicit_default"

        placement_mode = (policy.placement_mode or "").lower()
        max_reprices = int(policy.max_reprices or 0)
        if placement_mode == "aggressive_cross":
            return "aggressive"
        if placement_mode in {"passive_only", "passive_join"}:
            return "passive_repricing" if max_reprices > 0 else "passive"
        if placement_mode == "adaptive":
            return "passive_repricing" if max_reprices > 0 else "unknown"
        return "unknown"

    def _infer_horizon_bucket(self, spec: StrategySpecV2, metadata: dict[str, Any]) -> str:
        holding_ticks = self._infer_holding_ticks(spec, metadata)
        if holding_ticks is None:
            return "unknown"
        if holding_ticks <= 20:
            return "short"
        if holding_ticks <= 120:
            return "medium"
        return "long"

    def _infer_holding_ticks(self, spec: StrategySpecV2, metadata: dict[str, Any]) -> float | None:
        upper_bounds: list[float] = []
        lower_bounds: list[float] = []
        for exit_policy in spec.exit_policies:
            for rule in exit_policy.rules:
                for node in self._iter_expr_nodes(rule.condition):
                    if not isinstance(node, ComparisonExpr):
                        continue
                    if not self._is_holding_ticks_expr(node):
                        continue
                    threshold = float(node.threshold)
                    if node.op in {"<", "<=", "=="}:
                        upper_bounds.append(max(1.0, threshold - 1.0 if node.op == "<" else threshold))
                    elif node.op in {">", ">="}:
                        lower_bounds.append(threshold)

        if upper_bounds:
            return max(1.0, min(upper_bounds))
        if lower_bounds:
            return max(1.0, min(lower_bounds))

        if spec.execution_policy is not None and spec.execution_policy.cancel_after_ticks > 0:
            return float(spec.execution_policy.cancel_after_ticks)

        for source in (metadata, spec.metadata):
            if not isinstance(source, dict):
                continue
            for key in ("inferred_holding_horizon_ticks", "holding_horizon_ticks", "holding_ticks"):
                value = source.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return float(value)
        return None

    def _is_holding_ticks_expr(self, node: ComparisonExpr) -> bool:
        if node.left is not None:
            return isinstance(node.left, PositionAttrExpr) and node.left.name == "holding_ticks"
        return node.feature == "holding_ticks"

    def _iter_expr_nodes(self, root: ExprNode) -> list[ExprNode]:
        nodes: list[ExprNode] = [root]
        visited: list[ExprNode] = []
        while nodes:
            node = nodes.pop()
            visited.append(node)
            children = getattr(node, "children", None)
            if isinstance(children, list):
                nodes.extend(children)
            child = getattr(node, "child", None)
            if isinstance(child, ExprNode):
                nodes.append(child)
            expr = getattr(node, "expr", None)
            if isinstance(expr, ExprNode):
                nodes.append(expr)
            left = getattr(node, "left", None)
            if isinstance(left, ExprNode):
                nodes.append(left)
        return visited

    def _infer_regime_shape(self, spec: StrategySpecV2) -> str:
        regime_count = len(spec.regimes)
        is_stateful = bool(
            spec.state_policy
            and (
                spec.state_policy.vars
                or spec.state_policy.guards
                or spec.state_policy.events
            )
        )
        has_risk_degrade = bool(spec.risk_policy.degradation_rules)
        has_exec_adapt = bool(spec.execution_policy and spec.execution_policy.adaptation_rules)
        for regime in spec.regimes:
            if regime.risk_override and regime.risk_override.degradation_rules:
                has_risk_degrade = True
            if regime.execution_override and regime.execution_override.adaptation_rules:
                has_exec_adapt = True
        return "+".join(
            [
                f"r{regime_count}",
                "stateful" if is_stateful else "stateless",
                "risk_degrade" if has_risk_degrade else "risk_static",
                "exec_adapt" if has_exec_adapt else "exec_static",
            ]
        )

    def _coarse_feature_bucket(self, features: list[str]) -> str:
        if not features:
            return "none"
        buckets: set[str] = set()
        for feature in features:
            for motif, patterns in _MOTIF_PATTERNS:
                if any(pattern in feature for pattern in patterns):
                    buckets.add(motif)
            token = feature.split("_", 1)[0]
            if token:
                buckets.add(token[:20])
        ordered = sorted(buckets)
        return "+".join(ordered[:4]) if ordered else "none"


def fingerprint_similarity(a: FamilyFingerprint, b: FamilyFingerprint) -> float:
    """Deterministic heuristic similarity in [0, 1] between two family fingerprints."""
    if a.family_id == b.family_id:
        return 1.0

    feature_a = a.feature_set()
    feature_b = b.feature_set()
    union = feature_a | feature_b
    feature_overlap = (len(feature_a & feature_b) / len(union)) if union else 1.0

    score = 0.0
    score += 0.25 if a.motif == b.motif else 0.0
    score += 0.15 if a.side_model == b.side_model else 0.0
    score += 0.20 if a.execution_style == b.execution_style else 0.0
    score += 0.15 if a.horizon_bucket == b.horizon_bucket else 0.0
    score += 0.10 if a.regime_shape == b.regime_shape else 0.0
    score += 0.15 * feature_overlap
    return round(max(0.0, min(score, 1.0)), 6)
