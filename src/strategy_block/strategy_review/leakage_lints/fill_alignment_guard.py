from __future__ import annotations

from typing import Any

from strategy_block.strategy_review.leakage_lints.models import LeakageLintIssue
from strategy_block.strategy_review.review_common import POSITION_ATTR_ONLY
from strategy_block.strategy_specs.v2.ast_nodes import (
    AllExpr,
    AnyExpr,
    ComparisonExpr,
    CrossExpr,
    ExprNode,
    LagExpr,
    NotExpr,
    PersistExpr,
    PositionAttrExpr,
    RollingExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2


class FillAlignmentGuard:
    """Detect execution/position-derived namespace misuse in entry path."""

    def run(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None = None,
    ) -> list[LeakageLintIssue]:
        del backtest_environment  # unused by this guard
        issues: list[LeakageLintIssue] = []

        entry_nodes = self._collect_entry_path_nodes(spec)

        seen_position_attr: set[str] = set()
        for node in entry_nodes:
            if isinstance(node, PositionAttrExpr) and node.name not in seen_position_attr:
                seen_position_attr.add(node.name)
                issues.append(
                    LeakageLintIssue(
                        code="FILL_ALIGNMENT_POSITION_ATTR_IN_ENTRY_PATH",
                        severity="warning",
                        message=(
                            f"position_attr '{node.name}' is used in entry-path logic; this can couple entry to fill/position state"
                        ),
                        details={"position_attr": node.name},
                    )
                )

        seen_feature_misuse: set[str] = set()
        for node in entry_nodes:
            if isinstance(node, ComparisonExpr) and node.left is None and node.feature in POSITION_ATTR_ONLY:
                if node.feature in seen_feature_misuse:
                    continue
                seen_feature_misuse.add(node.feature)
                issues.append(
                    LeakageLintIssue(
                        code="FILL_ALIGNMENT_POSITION_ATTR_AS_FEATURE",
                        severity="error",
                        message=(
                            f"'{node.feature}' is a position attribute but appears as feature in entry path"
                        ),
                        details={"feature": node.feature},
                    )
                )

        seen_derived_misuse: set[str] = set()
        for node in entry_nodes:
            feat = self._derived_feature(node)
            if feat is None or feat not in POSITION_ATTR_ONLY:
                continue
            if feat in seen_derived_misuse:
                continue
            seen_derived_misuse.add(feat)
            issues.append(
                LeakageLintIssue(
                    code="FILL_ALIGNMENT_DERIVED_POSITION_ATTR_FEATURE",
                    severity="error",
                    message=(
                        f"'{feat}' is a position attribute but used in lag/rolling/cross expression in entry path"
                    ),
                    details={"feature": feat, "node_type": node.type},
                )
            )

        return issues

    def _derived_feature(self, node: ExprNode) -> str | None:
        if isinstance(node, (LagExpr, RollingExpr, CrossExpr)):
            return node.feature
        return None

    def _collect_entry_path_nodes(self, spec: StrategySpecV2) -> list[ExprNode]:
        nodes: list[ExprNode] = []

        for pc in spec.preconditions:
            self._walk(pc.condition, nodes)
        for ep in spec.entry_policies:
            self._walk(ep.trigger, nodes)
            self._walk(ep.strength, nodes)

        if spec.state_policy is not None:
            for guard in spec.state_policy.guards:
                self._walk(guard.condition, nodes)

        for rr in spec.risk_policy.degradation_rules:
            self._walk(rr.condition, nodes)

        if spec.execution_policy is not None:
            if spec.execution_policy.do_not_trade_when is not None:
                self._walk(spec.execution_policy.do_not_trade_when, nodes)
            for ar in spec.execution_policy.adaptation_rules:
                self._walk(ar.condition, nodes)

        for regime in spec.regimes:
            self._walk(regime.when, nodes)
            if regime.execution_override is not None:
                if regime.execution_override.do_not_trade_when is not None:
                    self._walk(regime.execution_override.do_not_trade_when, nodes)
                for ar in regime.execution_override.adaptation_rules:
                    self._walk(ar.condition, nodes)
            if regime.risk_override is not None:
                for rr in regime.risk_override.degradation_rules:
                    self._walk(rr.condition, nodes)

        return nodes

    def _walk(self, node: ExprNode, acc: list[ExprNode]) -> None:
        acc.append(node)
        if isinstance(node, (AllExpr, AnyExpr)):
            for child in node.children:
                self._walk(child, acc)
            return
        if isinstance(node, NotExpr):
            self._walk(node.child, acc)
            return
        if isinstance(node, PersistExpr):
            self._walk(node.expr, acc)
            return
        if isinstance(node, ComparisonExpr) and node.left is not None:
            self._walk(node.left, acc)
