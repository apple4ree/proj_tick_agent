from __future__ import annotations

from typing import Any

from strategy_block.strategy_review.leakage_lints.models import LeakageLintIssue
from strategy_block.strategy_specs.v2.ast_nodes import (
    AllExpr,
    AnyExpr,
    ComparisonExpr,
    ExprNode,
    LagExpr,
    NotExpr,
    PersistExpr,
    RollingExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2


class LookaheadGuard:
    """Lint for explicit or suspicious future-looking references."""

    _SUSPICIOUS_FUTURE_TOKENS: tuple[str, ...] = (
        "future",
        "next_",
        "next",
        "lead",
        "forward",
        "t_plus",
        "t+1",
    )

    def run(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None = None,
    ) -> list[LeakageLintIssue]:
        del backtest_environment  # unused by this guard
        issues: list[LeakageLintIssue] = []

        for node in self._collect_all_nodes(spec):
            if isinstance(node, LagExpr) and node.steps < 1:
                issues.append(
                    LeakageLintIssue(
                        code="LOOKAHEAD_INVALID_LAG_STEPS",
                        severity="error",
                        message=f"lag steps must be >=1, got {node.steps}",
                        details={"steps": node.steps},
                    )
                )
            if isinstance(node, RollingExpr) and node.window < 2:
                issues.append(
                    LeakageLintIssue(
                        code="LOOKAHEAD_INVALID_ROLLING_WINDOW",
                        severity="error",
                        message=f"rolling window must be >=2, got {node.window}",
                        details={"window": node.window},
                    )
                )
            if isinstance(node, PersistExpr):
                if node.window < 1:
                    issues.append(
                        LeakageLintIssue(
                            code="LOOKAHEAD_INVALID_PERSIST_WINDOW",
                            severity="error",
                            message=f"persist window must be >=1, got {node.window}",
                            details={"window": node.window},
                        )
                    )
                if node.min_true > node.window:
                    issues.append(
                        LeakageLintIssue(
                            code="LOOKAHEAD_INVALID_PERSIST_MIN_TRUE",
                            severity="error",
                            message=f"persist min_true ({node.min_true}) cannot exceed window ({node.window})",
                            details={"min_true": node.min_true, "window": node.window},
                        )
                    )

            feature_name = self._extract_feature_name(node)
            if feature_name and self._contains_future_token(feature_name):
                issues.append(
                    LeakageLintIssue(
                        code="LOOKAHEAD_SUSPICIOUS_FEATURE",
                        severity="error",
                        message=(
                            f"feature '{feature_name}' appears future-looking; explicit lookahead-like naming is rejected"
                        ),
                        details={"feature": feature_name},
                    )
                )

        return self._dedupe(issues)

    def _extract_feature_name(self, node: ExprNode) -> str | None:
        if isinstance(node, ComparisonExpr) and node.left is None:
            return node.feature
        if isinstance(node, LagExpr):
            return node.feature
        if isinstance(node, RollingExpr):
            return node.feature
        return getattr(node, "feature", None)

    def _contains_future_token(self, feature_name: str) -> bool:
        lower = feature_name.lower()
        return any(token in lower for token in self._SUSPICIOUS_FUTURE_TOKENS)

    def _collect_all_nodes(self, spec: StrategySpecV2) -> list[ExprNode]:
        nodes: list[ExprNode] = []

        for pc in spec.preconditions:
            self._walk(pc.condition, nodes)
        for ep in spec.entry_policies:
            self._walk(ep.trigger, nodes)
            self._walk(ep.strength, nodes)
        for xp in spec.exit_policies:
            for rule in xp.rules:
                self._walk(rule.condition, nodes)

        if spec.execution_policy is not None:
            if spec.execution_policy.do_not_trade_when is not None:
                self._walk(spec.execution_policy.do_not_trade_when, nodes)
            for rule in spec.execution_policy.adaptation_rules:
                self._walk(rule.condition, nodes)

        for rr in spec.risk_policy.degradation_rules:
            self._walk(rr.condition, nodes)

        for regime in spec.regimes:
            self._walk(regime.when, nodes)
            if regime.execution_override is not None:
                if regime.execution_override.do_not_trade_when is not None:
                    self._walk(regime.execution_override.do_not_trade_when, nodes)
                for rule in regime.execution_override.adaptation_rules:
                    self._walk(rule.condition, nodes)
            if regime.risk_override is not None:
                for rule in regime.risk_override.degradation_rules:
                    self._walk(rule.condition, nodes)

        if spec.state_policy is not None:
            for guard in spec.state_policy.guards:
                self._walk(guard.condition, nodes)

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

    def _dedupe(self, issues: list[LeakageLintIssue]) -> list[LeakageLintIssue]:
        seen: set[tuple[str, str]] = set()
        deduped: list[LeakageLintIssue] = []
        for issue in issues:
            key = (issue.code, issue.message)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        return deduped
