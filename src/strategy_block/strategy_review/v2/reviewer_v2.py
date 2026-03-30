"""Static reviewer for StrategySpecV2.

Review categories:
1. schema
2. expression_safety
3. feature_availability
4. logical_contradiction
5. unreachable_entry
6. risk_inconsistency
7. exit_completeness
8. dead_regime
9. regime_reference_integrity
10. execution_risk_mismatch
11. latency_structure_warning
12. state_reference_integrity          (Phase 3)
13. state_deadlock                     (Phase 3)
14. guard_conflict                     (Phase 3)
15. degradation_conflict               (Phase 3)
16. exit_semantics_risk                (Phase 3)
17. position_attr_sanity              (Phase 3 stabilization)
18. state_event_order_risk            (Phase 3 stabilization)
19. execution_override_conflict       (Phase 3 stabilization)
20. regime_exit_coverage              (Phase 3 stabilization)
21. execution_policy_too_aggressive   (churn suppression)
22. churn_risk_high                   (churn suppression)
23. missing_robust_exit_for_short_horizon (churn suppression)
24. queue_latency_mismatch            (churn suppression)
25. missing_execution_policy_for_short_horizon (churn suppression)
26. execution_policy_implicit_risk     (churn suppression)
"""
from __future__ import annotations

from typing import Any

from strategy_block.strategy_specs.v2.schema_v2 import (
    StrategySpecV2,
    ExecutionPolicyV2,
)
from strategy_block.strategy_specs.v2.ast_nodes import (
    ExprNode,
    ComparisonExpr,
    AllExpr,
    AnyExpr,
    NotExpr,
    ConstExpr,
    StateVarExpr,
    PositionAttrExpr,
    LagExpr,
    RollingExpr,
    PersistExpr,
)
from strategy_block.strategy_review.review_common import (
    KNOWN_FEATURES,
    POSITION_ATTR_ONLY,
    ReviewIssue,
    ReviewResult,
)


class StrategyReviewerV2:
    """Rule-based reviewer for StrategySpecV2."""

    def review(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None = None,
    ) -> ReviewResult:
        issues: list[ReviewIssue] = []

        # Phase 1 checks
        self._check_schema(spec, issues)
        self._check_expression_safety(spec, issues)
        self._check_feature_availability(spec, issues)
        self._check_logical_contradiction(spec, issues)
        self._check_unreachable_entry(spec, issues)
        self._check_risk_inconsistency(spec, issues)
        self._check_exit_completeness(spec, issues)

        # Phase 2 checks
        self._check_dead_regime(spec, issues)
        self._check_regime_reference_integrity(spec, issues)
        self._check_execution_risk_mismatch(spec, issues)
        self._check_latency_structure_warning(spec, issues)

        # Execution policy churn checks
        self._check_execution_policy_churn(spec, issues, backtest_environment)

        # Phase 3 checks
        self._check_state_reference_integrity(spec, issues)
        self._check_state_deadlock(spec, issues)
        self._check_guard_conflict(spec, issues)
        self._check_degradation_conflict(spec, issues)
        self._check_exit_semantics_risk(spec, issues)
        self._check_position_attr_sanity(spec, issues)
        self._check_position_attr_as_feature(spec, issues)
        self._check_dead_exit_path(spec, issues)
        self._check_state_event_order_risk(spec, issues)
        self._check_execution_override_conflict(spec, issues)
        self._check_regime_exit_coverage(spec, issues)

        has_error = any(i.severity == "error" for i in issues)
        return ReviewResult(passed=not has_error, issues=issues)

    # ── 1. Schema ─────────────────────────────────────────────────

    def _check_schema(self, spec: StrategySpecV2, issues: list[ReviewIssue]) -> None:
        errors = spec.validate()
        for err in errors:
            issues.append(ReviewIssue(
                severity="error",
                category="schema",
                description=err,
            ))

    # ── 2. Expression safety ──────────────────────────────────────

    def _check_expression_safety(self, spec: StrategySpecV2,
                                  issues: list[ReviewIssue]) -> None:
        for i, ep in enumerate(spec.entry_policies):
            depth = self._expr_depth(ep.trigger)
            if depth > 10:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="expression_safety",
                    description=(
                        f"entry_policies[{i}].trigger has depth {depth} "
                        f"— deeply nested expressions may be hard to debug"
                    ),
                    suggestion="Simplify the expression tree to depth <= 5",
                ))

    def _expr_depth(self, node: ExprNode) -> int:
        if hasattr(node, "children"):
            if not node.children:
                return 1
            return 1 + max(self._expr_depth(c) for c in node.children)
        if hasattr(node, "child"):
            return 1 + self._expr_depth(node.child)
        if isinstance(node, PersistExpr):
            return 1 + self._expr_depth(node.expr)
        if isinstance(node, ComparisonExpr) and node.left is not None:
            return 1 + self._expr_depth(node.left)
        return 1

    # ── 3. Feature availability ───────────────────────────────────

    def _check_feature_availability(self, spec: StrategySpecV2,
                                     issues: list[ReviewIssue]) -> None:
        all_features = spec.collect_all_features()
        unknown = all_features - KNOWN_FEATURES
        if unknown:
            issues.append(ReviewIssue(
                severity="info",
                category="feature_availability",
                description=f"Unknown features: {', '.join(sorted(unknown))}",
                suggestion=(
                    "These features must be provided via state.features dict "
                    "at runtime. Ensure your feature pipeline populates them."
                ),
            ))

    # ── 4. Logical contradiction ──────────────────────────────────

    def _check_logical_contradiction(self, spec: StrategySpecV2,
                                      issues: list[ReviewIssue]) -> None:
        for i, ep in enumerate(spec.entry_policies):
            contradictions = self._find_contradictions(ep.trigger)
            for desc in contradictions:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="logical_contradiction",
                    description=f"entry_policies[{i}].trigger: {desc}",
                    suggestion="Review conditions — they may never be satisfied simultaneously",
                ))

        for i, pc in enumerate(spec.preconditions):
            contradictions = self._find_contradictions(pc.condition)
            for desc in contradictions:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="logical_contradiction",
                    description=f"preconditions[{i}]: {desc}",
                    suggestion="This precondition may block all signals",
                ))

        for i, regime in enumerate(spec.regimes):
            contradictions = self._find_contradictions(regime.when)
            for desc in contradictions:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="logical_contradiction",
                    description=f"regimes[{i}].when: {desc}",
                    suggestion="This regime condition may never be satisfied",
                ))

    def _find_contradictions(self, node: ExprNode) -> list[str]:
        results: list[str] = []
        if isinstance(node, AllExpr):
            comparisons: list[ComparisonExpr] = []
            for child in node.children:
                if isinstance(child, ComparisonExpr) and child.left is None:
                    comparisons.append(child)
                results.extend(self._find_contradictions(child))

            for a_idx, a in enumerate(comparisons):
                for b in comparisons[a_idx + 1:]:
                    if a.feature != b.feature:
                        continue
                    contradiction = self._pair_contradicts(a, b)
                    if contradiction:
                        results.append(contradiction)

        elif isinstance(node, AnyExpr):
            for child in node.children:
                results.extend(self._find_contradictions(child))
        elif isinstance(node, NotExpr):
            results.extend(self._find_contradictions(node.child))
        elif isinstance(node, PersistExpr):
            results.extend(self._find_contradictions(node.expr))
        elif isinstance(node, ComparisonExpr) and node.left is not None:
            results.extend(self._find_contradictions(node.left))

        return results

    def _pair_contradicts(self, a: ComparisonExpr, b: ComparisonExpr) -> str | None:
        if a.op == ">" and b.op == "<" and b.threshold <= a.threshold:
            return (
                f"'{a.feature} > {a.threshold}' AND '{b.feature} < {b.threshold}' "
                f"— impossible when {b.threshold} <= {a.threshold}"
            )
        if a.op == "<" and b.op == ">" and a.threshold <= b.threshold:
            return (
                f"'{a.feature} < {a.threshold}' AND '{b.feature} > {b.threshold}' "
                f"— impossible when {a.threshold} <= {b.threshold}"
            )
        if a.op == ">=" and b.op == "<=" and b.threshold < a.threshold:
            return (
                f"'{a.feature} >= {a.threshold}' AND '{b.feature} <= {b.threshold}' "
                f"— impossible when {b.threshold} < {a.threshold}"
            )
        if a.op == "<=" and b.op == ">=" and a.threshold < b.threshold:
            return (
                f"'{a.feature} <= {a.threshold}' AND '{b.feature} >= {b.threshold}' "
                f"— impossible when {a.threshold} < {b.threshold}"
            )
        return None

    # ── 5. Unreachable entry ──────────────────────────────────────

    def _check_unreachable_entry(self, spec: StrategySpecV2,
                                  issues: list[ReviewIssue]) -> None:
        for i, ep in enumerate(spec.entry_policies):
            cd = ep.constraints.cooldown_ticks
            if cd > 10000:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="unreachable_entry",
                    description=(
                        f"entry_policies[{i}]: cooldown_ticks={cd} is extremely large "
                        f"— this entry may never re-fire during a session"
                    ),
                    suggestion="Typical cooldown is 10-500 ticks for tick-level strategies",
                ))

    # ── 6. Risk inconsistency ─────────────────────────────────────

    def _check_risk_inconsistency(self, spec: StrategySpecV2,
                                   issues: list[ReviewIssue]) -> None:
        rp = spec.risk_policy
        if rp.inventory_cap < rp.max_position:
            issues.append(ReviewIssue(
                severity="warning",
                category="risk_inconsistency",
                description=(
                    f"inventory_cap ({rp.inventory_cap}) < max_position "
                    f"({rp.max_position}) — cap will bind before max_position"
                ),
                suggestion="Set inventory_cap >= max_position",
            ))
        ps = rp.position_sizing
        if ps.base_size > ps.max_size:
            issues.append(ReviewIssue(
                severity="warning",
                category="risk_inconsistency",
                description=(
                    f"position_sizing.base_size ({ps.base_size}) > max_size "
                    f"({ps.max_size})"
                ),
                suggestion="base_size should be <= max_size",
            ))

    # ── 7. Exit completeness ─────────────────────────────────────

    def _check_exit_completeness(self, spec: StrategySpecV2,
                                  issues: list[ReviewIssue]) -> None:
        has_close_all = False
        for xp in spec.exit_policies:
            for rule in xp.rules:
                if rule.action.type == "close_all":
                    has_close_all = True
                    break
            if has_close_all:
                break

        if not has_close_all:
            issues.append(ReviewIssue(
                severity="error",
                category="exit_completeness",
                description="No exit rule with action='close_all' found",
                suggestion=(
                    "Add at least one close_all exit rule (e.g. stop loss) "
                    "to bound downside risk"
                ),
            ))

    # ── 8. Dead regime (Phase 2) ──────────────────────────────────

    def _check_dead_regime(self, spec: StrategySpecV2,
                            issues: list[ReviewIssue]) -> None:
        for i, regime in enumerate(spec.regimes):
            contradictions = self._find_contradictions(regime.when)
            if contradictions:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="dead_regime",
                    description=(
                        f"regimes[{i}] '{regime.name}' has contradictory when-condition "
                        f"— this regime may never activate"
                    ),
                    suggestion="Check the regime's when condition for logical errors",
                ))

    # ── 9. Regime reference integrity (Phase 2) ───────────────────

    def _check_regime_reference_integrity(self, spec: StrategySpecV2,
                                           issues: list[ReviewIssue]) -> None:
        entry_names = {ep.name for ep in spec.entry_policies}
        exit_names = {xp.name for xp in spec.exit_policies}

        for i, regime in enumerate(spec.regimes):
            for ref in regime.entry_policy_refs:
                if ref not in entry_names:
                    issues.append(ReviewIssue(
                        severity="error",
                        category="regime_reference_integrity",
                        description=(
                            f"regimes[{i}] '{regime.name}': entry_policy_ref '{ref}' "
                            f"does not match any entry policy"
                        ),
                        suggestion=f"Available entry policies: {sorted(entry_names)}",
                    ))
            if regime.exit_policy_ref and regime.exit_policy_ref not in exit_names:
                issues.append(ReviewIssue(
                    severity="error",
                    category="regime_reference_integrity",
                    description=(
                        f"regimes[{i}] '{regime.name}': exit_policy_ref "
                        f"'{regime.exit_policy_ref}' does not match any exit policy"
                    ),
                    suggestion=f"Available exit policies: {sorted(exit_names)}",
                ))

    # ── 10. Execution/risk mismatch (Phase 2) ─────────────────────

    def _check_execution_risk_mismatch(self, spec: StrategySpecV2,
                                        issues: list[ReviewIssue]) -> None:
        xp = spec.execution_policy
        if xp is None:
            return

        if xp.cancel_after_ticks < 0:
            issues.append(ReviewIssue(
                severity="error",
                category="execution_risk_mismatch",
                description="execution_policy.cancel_after_ticks is negative",
            ))
        if xp.max_reprices < 0:
            issues.append(ReviewIssue(
                severity="error",
                category="execution_risk_mismatch",
                description="execution_policy.max_reprices is negative",
            ))

        if xp.placement_mode == "passive_only":
            rp = spec.risk_policy
            if rp.max_position > 500:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="execution_risk_mismatch",
                    description=(
                        f"placement_mode='passive_only' with max_position={rp.max_position} "
                        f"— large passive-only positions may be difficult to fill"
                    ),
                    suggestion="Consider reducing max_position or using adaptive placement",
                ))

    # ── 11. Latency/structure warning (Phase 2) ───────────────────

    def _check_latency_structure_warning(self, spec: StrategySpecV2,
                                          issues: list[ReviewIssue]) -> None:
        # NOTE: This check validates strategy-internal latency structure only.
        # It does not know about engine-side observation lag (market_data_delay_ms).
        # When market_data_delay_ms > 0, strategy-side lag/rolling expressions
        # stack on top of the observation delay. For example, LagExpr(steps=5)
        # with market_data_delay_ms=2000 at 1s resolution effectively looks back
        # ~7 seconds. A future enhancement could accept market_data_delay_ms here
        # and warn when short-horizon strategies are paired with large observation
        # delays (TODO: cross-cutting review with BacktestConfig).
        all_nodes = self._collect_all_nodes(spec)

        for node in all_nodes:
            if isinstance(node, RollingExpr):
                if node.window > 200:
                    issues.append(ReviewIssue(
                        severity="warning",
                        category="latency_structure_warning",
                        description=(
                            f"rolling('{node.feature}', window={node.window}) has a "
                            f"very large window — requires {node.window} ticks of history"
                        ),
                        suggestion="Windows > 200 may cause excessive memory usage",
                    ))
            elif isinstance(node, PersistExpr):
                if node.window > 200:
                    issues.append(ReviewIssue(
                        severity="warning",
                        category="latency_structure_warning",
                        description=(
                            f"persist(window={node.window}) has a very large window"
                        ),
                        suggestion="Windows > 200 may cause excessive memory usage",
                    ))
            elif isinstance(node, LagExpr):
                if node.steps > 200:
                    issues.append(ReviewIssue(
                        severity="warning",
                        category="latency_structure_warning",
                        description=(
                            f"lag('{node.feature}', steps={node.steps}) looks back "
                            f"very far in history"
                        ),
                        suggestion="Steps > 200 may cause excessive memory usage",
                    ))

    # ── 12. State reference integrity (Phase 3) ───────────────────

    def _check_state_reference_integrity(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
    ) -> None:
        state_vars = set(spec.state_policy.vars.keys()) if spec.state_policy else set()
        for node in self._collect_all_nodes(spec):
            if isinstance(node, StateVarExpr) and node.name not in state_vars:
                issues.append(ReviewIssue(
                    severity="error",
                    category="state_reference_integrity",
                    description=(
                        f"state_var '{node.name}' is referenced but not defined "
                        f"in state_policy.vars"
                    ),
                    suggestion=(
                        "Declare the variable in state_policy.vars or "
                        "replace the expression with a feature-based condition"
                    ),
                ))

    # ── 13. State deadlock (Phase 3) ──────────────────────────────

    def _check_state_deadlock(self, spec: StrategySpecV2,
                               issues: list[ReviewIssue]) -> None:
        sp = spec.state_policy
        if sp is None:
            return

        for i, guard in enumerate(sp.guards):
            if self._is_always_true(guard.condition):
                issues.append(ReviewIssue(
                    severity="error",
                    category="state_deadlock",
                    description=(
                        f"state_policy.guards[{i}] '{guard.name}' appears always true "
                        "and can permanently block entries"
                    ),
                    suggestion=(
                        "Use a conditional guard (e.g., state_var threshold) "
                        "or add a state event that can release the guard"
                    ),
                ))

        if spec.execution_policy and spec.execution_policy.do_not_trade_when is not None:
            if self._is_always_true(spec.execution_policy.do_not_trade_when):
                issues.append(ReviewIssue(
                    severity="error",
                    category="state_deadlock",
                    description="execution_policy.do_not_trade_when appears always true",
                    suggestion="Relax the condition or make it state/feature dependent",
                ))

        has_loss_increment = False
        has_loss_reset = False
        for event in sp.events:
            for upd in event.updates:
                if upd.var == "loss_streak" and upd.op == "increment":
                    has_loss_increment = True
                if upd.var == "loss_streak" and upd.op == "reset":
                    has_loss_reset = True
        if has_loss_increment and not has_loss_reset:
            issues.append(ReviewIssue(
                severity="error",
                category="state_deadlock",
                description=(
                    "loss_streak is incremented but never reset — "
                    "entry degradation/guards will become permanent over time"
                ),
                suggestion="Add an on_exit_profit or on_flatten reset for loss_streak",
            ))

    # ── 14. Guard conflict (Phase 3) ───────────────────────────────

    def _check_guard_conflict(self, spec: StrategySpecV2,
                               issues: list[ReviewIssue]) -> None:
        sp = spec.state_policy
        if sp is not None:
            names: set[str] = set()
            for i, guard in enumerate(sp.guards):
                if guard.name in names:
                    issues.append(ReviewIssue(
                        severity="warning",
                        category="guard_conflict",
                        description=(
                            f"state_policy.guards[{i}] has duplicate name '{guard.name}'"
                        ),
                        suggestion="Use unique guard names to avoid ambiguous diagnostics",
                    ))
                names.add(guard.name)


    # ── 15. Degradation conflict (Phase 3) ─────────────────────────

    def _check_degradation_conflict(self, spec: StrategySpecV2,
                                     issues: list[ReviewIssue]) -> None:
        def check_risk(path: str, risk_policy) -> None:
            for i, rule in enumerate(risk_policy.degradation_rules):
                if rule.action.type != "block_new_entries":
                    continue
                if self._is_always_true(rule.condition):
                    issues.append(ReviewIssue(
                        severity="error",
                        category="degradation_conflict",
                        description=(
                            f"{path}.degradation_rules[{i}] always blocks new entries"
                        ),
                        suggestion=(
                            "Use a conditional block rule or replace with scale_strength"
                        ),
                    ))

        check_risk("risk_policy", spec.risk_policy)
        for i, regime in enumerate(spec.regimes):
            if regime.risk_override is not None:
                check_risk(f"regimes[{i}].risk_override", regime.risk_override)

    # ── 16. Exit semantics risk (Phase 3) ──────────────────────────

    def _check_exit_semantics_risk(self, spec: StrategySpecV2,
                                    issues: list[ReviewIssue]) -> None:
        metadata_flag = bool(spec.metadata.get("entry_gates_apply_to_exit"))
        has_entry_gates = bool(spec.preconditions) or bool(spec.regimes)
        has_dnt = bool(spec.execution_policy and spec.execution_policy.do_not_trade_when is not None)
        has_robust_close = self._has_robust_close_all(spec)

        if metadata_flag:
            issues.append(ReviewIssue(
                severity="error",
                category="exit_semantics_risk",
                description=(
                    "metadata indicates entry gates may be applied to exits "
                    "(entry_gates_apply_to_exit=true)"
                ),
                suggestion=(
                    "Use exit-first runtime semantics so do_not_trade/preconditions/regime "
                    "cannot block in-position exits"
                ),
            ))

        if (has_entry_gates or has_dnt) and not has_robust_close:
            issues.append(ReviewIssue(
                severity="error",
                category="exit_semantics_risk",
                description=(
                    "Entry gating conditions are present (preconditions/regimes/do_not_trade_when) "
                    "and no robust close_all fail-safe exists — "
                    "positions may become trapped when gates block trading"
                ),
                suggestion=(
                    "Add a robust close_all fail-safe (e.g. stop-loss on unrealized_pnl_bps "
                    "or holding_ticks) that fires regardless of entry gates"
                ),
            ))

    # ── Helpers ─────────────────────────────────────────────────────

    def _iter_execution_policies(self, spec: StrategySpecV2):
        if spec.execution_policy is not None:
            yield "execution_policy", spec.execution_policy
        for i, regime in enumerate(spec.regimes):
            if regime.execution_override is not None:
                yield f"regimes[{i}].execution_override", regime.execution_override

    def _has_unconditional_close_all(self, spec: StrategySpecV2) -> bool:
        for xp in spec.exit_policies:
            for rule in xp.rules:
                if rule.action.type == "close_all" and self._is_always_true(rule.condition):
                    return True
        return False

    def _has_robust_close_all(self, spec: StrategySpecV2) -> bool:
        """Check if there is a robust close_all fail-safe.

        Robust means at least one of:
        - An unconditional close_all (ConstExpr always-true)
        - A close_all gated on holding_ticks (time-based → eventually fires)
        - A close_all gated on unrealized_pnl_bps (stop-loss → fires on drawdown)
        """
        for xp in spec.exit_policies:
            for rule in xp.rules:
                if rule.action.type != "close_all":
                    continue
                if self._is_always_true(rule.condition):
                    return True
                if self._condition_uses_position_attr(rule.condition, {"holding_ticks", "unrealized_pnl_bps"}):
                    return True
        return False

    def _condition_uses_position_attr(self, node: ExprNode, attr_names: set[str]) -> bool:
        """Check if a condition tree uses any of the given position_attr names."""
        nodes: list[ExprNode] = []
        self._walk_tree(node, nodes)
        for n in nodes:
            if isinstance(n, PositionAttrExpr) and n.name in attr_names:
                return True
            if isinstance(n, ComparisonExpr) and isinstance(n.left, PositionAttrExpr):
                if n.left.name in attr_names:
                    return True
        return False

    def _is_always_true(self, node: ExprNode) -> bool:
        if isinstance(node, ConstExpr):
            return node.value != 0.0
        if isinstance(node, AllExpr):
            return bool(node.children) and all(self._is_always_true(c) for c in node.children)
        if isinstance(node, AnyExpr):
            return any(self._is_always_true(c) for c in node.children)
        if isinstance(node, NotExpr):
            return self._is_always_false(node.child)
        if isinstance(node, ComparisonExpr) and node.left is not None and isinstance(node.left, ConstExpr):
            v = node.left.value
            t = node.threshold
            if node.op == ">":
                return v > t
            if node.op == "<":
                return v < t
            if node.op == ">=":
                return v >= t
            if node.op == "<=":
                return v <= t
            if node.op == "==":
                return abs(v - t) < 1e-9
        return False

    def _is_always_false(self, node: ExprNode) -> bool:
        if isinstance(node, ConstExpr):
            return node.value == 0.0
        if isinstance(node, AllExpr):
            return any(self._is_always_false(c) for c in node.children)
        if isinstance(node, AnyExpr):
            return bool(node.children) and all(self._is_always_false(c) for c in node.children)
        if isinstance(node, NotExpr):
            return self._is_always_true(node.child)
        if isinstance(node, ComparisonExpr) and node.left is not None and isinstance(node.left, ConstExpr):
            return not self._is_always_true(node)
        return False

    def _collect_all_nodes(self, spec: StrategySpecV2) -> list[ExprNode]:
        nodes: list[ExprNode] = []
        for pc in spec.preconditions:
            self._walk_tree(pc.condition, nodes)
        for ep in spec.entry_policies:
            self._walk_tree(ep.trigger, nodes)
            self._walk_tree(ep.strength, nodes)
        for xp in spec.exit_policies:
            for rule in xp.rules:
                self._walk_tree(rule.condition, nodes)
        for regime in spec.regimes:
            self._walk_tree(regime.when, nodes)
            if regime.risk_override is not None:
                for rr in regime.risk_override.degradation_rules:
                    self._walk_tree(rr.condition, nodes)
            if regime.execution_override is not None:
                if regime.execution_override.do_not_trade_when is not None:
                    self._walk_tree(regime.execution_override.do_not_trade_when, nodes)
                for ar in regime.execution_override.adaptation_rules:
                    self._walk_tree(ar.condition, nodes)
        if spec.execution_policy and spec.execution_policy.do_not_trade_when:
            self._walk_tree(spec.execution_policy.do_not_trade_when, nodes)
        if spec.execution_policy:
            for ar in spec.execution_policy.adaptation_rules:
                self._walk_tree(ar.condition, nodes)
        for rr in spec.risk_policy.degradation_rules:
            self._walk_tree(rr.condition, nodes)
        if spec.state_policy is not None:
            for guard in spec.state_policy.guards:
                self._walk_tree(guard.condition, nodes)
        return nodes

    def _walk_tree(self, node: ExprNode, acc: list[ExprNode]) -> None:
        acc.append(node)
        if hasattr(node, "children"):
            for child in node.children:
                self._walk_tree(child, acc)
        if hasattr(node, "child"):
            self._walk_tree(node.child, acc)
        if isinstance(node, PersistExpr):
            self._walk_tree(node.expr, acc)
        if isinstance(node, ComparisonExpr) and node.left is not None:
            self._walk_tree(node.left, acc)

    def _check_position_attr_sanity(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
    ) -> None:
        entry_nodes: list[ExprNode] = []

        for pc in spec.preconditions:
            self._walk_tree(pc.condition, entry_nodes)
        for ep in spec.entry_policies:
            self._walk_tree(ep.trigger, entry_nodes)
            self._walk_tree(ep.strength, entry_nodes)
        if spec.state_policy is not None:
            for guard in spec.state_policy.guards:
                self._walk_tree(guard.condition, entry_nodes)
        for rr in spec.risk_policy.degradation_rules:
            self._walk_tree(rr.condition, entry_nodes)
        if spec.execution_policy and spec.execution_policy.do_not_trade_when is not None:
            self._walk_tree(spec.execution_policy.do_not_trade_when, entry_nodes)
        if spec.execution_policy is not None:
            for ar in spec.execution_policy.adaptation_rules:
                self._walk_tree(ar.condition, entry_nodes)
        for regime in spec.regimes:
            self._walk_tree(regime.when, entry_nodes)
            if regime.risk_override is not None:
                for rr in regime.risk_override.degradation_rules:
                    self._walk_tree(rr.condition, entry_nodes)
            if regime.execution_override is not None:
                if regime.execution_override.do_not_trade_when is not None:
                    self._walk_tree(regime.execution_override.do_not_trade_when, entry_nodes)
                for ar in regime.execution_override.adaptation_rules:
                    self._walk_tree(ar.condition, entry_nodes)

        warned_attrs: set[str] = set()
        for node in entry_nodes:
            if not isinstance(node, PositionAttrExpr):
                continue
            if node.name in warned_attrs:
                continue
            warned_attrs.add(node.name)
            issues.append(ReviewIssue(
                severity="warning",
                category="position_attr_sanity",
                description=(
                    f"position_attr {node.name} appears in entry path logic and may evaluate as flat-state constant"
                ),
                suggestion=(
                    "Prefer position_attr in exit rules, or gate usage with explicit in-position state"
                ),
            ))

        for node in self._collect_all_nodes(spec):
            if not isinstance(node, ComparisonExpr):
                continue
            if not isinstance(node.left, PositionAttrExpr):
                continue
            if node.left.name != "unrealized_pnl_bps":
                continue
            if abs(node.threshold) > 1000.0:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="position_attr_sanity",
                    description=(
                        f"unrealized_pnl_bps threshold {node.threshold} looks unrealistically large"
                    ),
                    suggestion="Use tighter stop/take-profit thresholds for tick-level strategies",
                ))

    # ── 21. Position attr used as feature (hard error) ────────────────

    def _check_position_attr_as_feature(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
    ) -> None:
        """Reject specs where position_attr-only names appear as plain features.

        This catches the case where OpenAI (or a template) places e.g.
        ``holding_ticks`` or ``unrealized_pnl_bps`` in a ComparisonExpr.feature
        instead of ComparisonExpr.left=PositionAttrExpr(...).  At runtime
        the feature lookup silently returns 0.0, making the condition dead.
        """
        seen: set[str] = set()
        for node in self._collect_all_nodes(spec):
            if not isinstance(node, ComparisonExpr):
                continue
            # ComparisonExpr uses .feature when .left is None (simple form)
            if node.left is not None:
                continue
            if node.feature in POSITION_ATTR_ONLY and node.feature not in seen:
                seen.add(node.feature)
                issues.append(ReviewIssue(
                    severity="error",
                    category="position_attr_as_feature",
                    description=(
                        f"'{node.feature}' is a position attribute but is used as a "
                        f"plain feature — this silently evaluates to 0.0 at runtime"
                    ),
                    suggestion=(
                        f"Use position_attr='{node.feature}' in the condition "
                        f"instead of feature='{node.feature}'"
                    ),
                ))
            # Also check CrossExpr and RollingExpr that reference position attrs
            if isinstance(node, ComparisonExpr) and node.left is not None:
                from strategy_block.strategy_specs.v2.ast_nodes import (
                    CrossExpr as _CE,
                    RollingExpr as _RE,
                )
                if isinstance(node.left, (_CE, _RE)):
                    feat = getattr(node.left, "feature", "")
                    if feat in POSITION_ATTR_ONLY and feat not in seen:
                        seen.add(feat)
                        issues.append(ReviewIssue(
                            severity="error",
                            category="position_attr_as_feature",
                            description=(
                                f"'{feat}' is a position attribute but used "
                                f"in cross/rolling — must use position_attr"
                            ),
                            suggestion=f"Rewrite to use position_attr='{feat}'",
                        ))

        # Also check CrossExpr and RollingExpr at top level
        for node in self._collect_all_nodes(spec):
            from strategy_block.strategy_specs.v2.ast_nodes import CrossExpr as _CrossE
            if isinstance(node, _CrossE) and node.feature in POSITION_ATTR_ONLY:
                if node.feature not in seen:
                    seen.add(node.feature)
                    issues.append(ReviewIssue(
                        severity="error",
                        category="position_attr_as_feature",
                        description=(
                            f"CrossExpr uses '{node.feature}' which is a position "
                            f"attribute — cross conditions cannot use position attrs"
                        ),
                        suggestion="Use a feature-based cross or position_attr comparison",
                    ))
            if isinstance(node, RollingExpr) and node.feature in POSITION_ATTR_ONLY:
                if node.feature not in seen:
                    seen.add(node.feature)
                    issues.append(ReviewIssue(
                        severity="error",
                        category="position_attr_as_feature",
                        description=(
                            f"RollingExpr uses '{node.feature}' which is a position "
                            f"attribute — rolling aggregation cannot use position attrs"
                        ),
                        suggestion="Use a feature-based rolling or position_attr comparison",
                    ))

    # ── 22. Dead exit path (hard error) ─────────────────────────────────

    def _check_dead_exit_path(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
    ) -> None:
        """Reject specs where exit rules rely on position_attr names placed
        in the feature field.  Such conditions silently evaluate to 0.0 at
        runtime, making the exit rule effectively dead.

        Specifically flags when a close_all exit rule's condition tree
        contains a ComparisonExpr with feature ∈ POSITION_ATTR_ONLY and
        no left expr (i.e. the legacy simple-comparison form).  A dead
        stop-loss or time exit is a critical safety failure.
        """
        for xp_idx, xp in enumerate(spec.exit_policies):
            for rule_idx, rule in enumerate(xp.rules):
                dead_features = self._find_dead_features_in_exit(rule.condition)
                if dead_features:
                    issues.append(ReviewIssue(
                        severity="error",
                        category="dead_exit_path",
                        description=(
                            f"exit_policies[{xp_idx}].rules[{rule_idx}] '{rule.name}' "
                            f"uses {', '.join(sorted(dead_features))} as feature — "
                            f"evaluates to 0.0 at runtime, making this exit dead"
                        ),
                        suggestion=(
                            "Use position_attr instead of feature for "
                            + ", ".join(sorted(dead_features))
                        ),
                    ))

    def _find_dead_features_in_exit(self, node: ExprNode) -> set[str]:
        """Collect position_attr names misused as feature in an exit condition tree."""
        result: set[str] = set()
        nodes: list[ExprNode] = []
        self._walk_tree(node, nodes)
        for n in nodes:
            if isinstance(n, ComparisonExpr) and n.left is None:
                if n.feature in POSITION_ATTR_ONLY:
                    result.add(n.feature)
        return result

    def _check_state_event_order_risk(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
    ) -> None:
        sp = spec.state_policy
        if sp is None:
            return

        incremented: set[str] = set()
        reset: set[str] = set()
        updated_on_exit: set[str] = set()
        reset_on_flatten: set[str] = set()

        for event in sp.events:
            for upd in event.updates:
                if upd.op == "increment":
                    incremented.add(upd.var)
                if upd.op == "reset":
                    reset.add(upd.var)
                if event.on in {"on_exit_loss", "on_exit_profit"}:
                    updated_on_exit.add(upd.var)
                if event.on == "on_flatten" and upd.op == "reset":
                    reset_on_flatten.add(upd.var)

        for var in sorted(incremented - reset):
            issues.append(ReviewIssue(
                severity="warning",
                category="state_event_order_risk",
                description=f"state var {var} is incremented but never reset",
                suggestion="Add reset coverage on on_exit_profit or on_flatten",
            ))

        for var in sorted(updated_on_exit & reset_on_flatten):
            issues.append(ReviewIssue(
                severity="warning",
                category="state_event_order_risk",
                description=(
                    f"state var {var} is updated on exit and reset on flatten; same-tick updates may be cleared"
                ),
                suggestion="Confirm event ordering intent or split variables for post-exit vs flat memory",
            ))

    def _check_execution_override_conflict(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
    ) -> None:
        for path, xp in self._iter_execution_policies(spec):
            rules = xp.adaptation_rules
            for i in range(len(rules)):
                for j in range(i + 1, len(rules)):
                    a = rules[i]
                    b = rules[j]
                    if not self._is_always_true(a.condition):
                        continue
                    if not self._is_always_true(b.condition):
                        continue

                    conflict_fields: list[str] = []
                    if (a.override.placement_mode is not None
                            and b.override.placement_mode is not None
                            and a.override.placement_mode != b.override.placement_mode):
                        conflict_fields.append("placement_mode")
                    if (a.override.cancel_after_ticks is not None
                            and b.override.cancel_after_ticks is not None
                            and a.override.cancel_after_ticks != b.override.cancel_after_ticks):
                        conflict_fields.append("cancel_after_ticks")
                    if (a.override.max_reprices is not None
                            and b.override.max_reprices is not None
                            and a.override.max_reprices != b.override.max_reprices):
                        conflict_fields.append("max_reprices")

                    if conflict_fields:
                        issues.append(ReviewIssue(
                            severity="error",
                            category="execution_override_conflict",
                            description=(
                                f"{path}.adaptation_rules[{i}] and [{j}] are always true and conflict on {', '.join(conflict_fields)}"
                            ),
                            suggestion="Make rule conditions mutually exclusive or merge overrides",
                        ))

    def _check_regime_exit_coverage(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
    ) -> None:
        if not spec.regimes:
            return

        exit_by_name = {xp.name: xp for xp in spec.exit_policies}

        def has_close_all(policies: list) -> bool:
            for policy in policies:
                for rule in policy.rules:
                    if rule.action.type == "close_all":
                        return True
            return False

        global_has_close = has_close_all(spec.exit_policies)
        has_regime_entry = False
        for i, regime in enumerate(spec.regimes):
            if not regime.entry_policy_refs:
                continue
            has_regime_entry = True

            if not regime.exit_policy_ref:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="regime_exit_coverage",
                    description=(
                        f"regimes[{i}] {regime.name} has entry refs but no explicit exit policy ref"
                    ),
                    suggestion="Set exit_policy_ref explicitly or ensure global exits are robust",
                ))
                continue

            xp = exit_by_name.get(regime.exit_policy_ref)
            if xp is not None and not has_close_all([xp]):
                issues.append(ReviewIssue(
                    severity="warning",
                    category="regime_exit_coverage",
                    description=(
                        f"regimes[{i}] {regime.name} exit policy {regime.exit_policy_ref} has no close_all"
                    ),
                    suggestion="Add a close_all fail-safe in regime exit policy",
                ))

        if has_regime_entry and not global_has_close:
            issues.append(ReviewIssue(
                severity="error",
                category="regime_exit_coverage",
                description=(
                    "Regime entries exist but global exit policies have no close_all — "
                    "positions opened via regime may have no exit path"
                ),
                suggestion="Add at least one global close_all fallback",
            ))

        strong_entry_throttles = (
            any(ep.constraints.cooldown_ticks > 0 for ep in spec.entry_policies)
            or bool(spec.state_policy and spec.state_policy.guards)
            or any(rr.action.type == "block_new_entries" for rr in spec.risk_policy.degradation_rules)
        )
        for regime in spec.regimes:
            if regime.risk_override is not None and any(
                rr.action.type == "block_new_entries"
                for rr in regime.risk_override.degradation_rules
            ):
                strong_entry_throttles = True
                break

        has_holding_ticks_exit = False
        for node in self._collect_all_nodes(spec):
            if isinstance(node, ComparisonExpr) and isinstance(node.left, PositionAttrExpr):
                if node.left.name == "holding_ticks":
                    has_holding_ticks_exit = True
                    break

        if strong_entry_throttles and not has_holding_ticks_exit:
            issues.append(ReviewIssue(
                severity="warning",
                category="regime_exit_coverage",
                description="Strong entry throttles are present but no holding_ticks-based time exit was found",
                suggestion="Consider adding position_attr holding_ticks time exit",
            ))

    # ── Execution-policy churn checks ─────────────────────────────────

    # Execution budget constants (internal reviewer policy — not spec schema)
    _SHORT_HORIZON_THRESHOLD: int = 30
    _SHORT_HORIZON_THRESHOLD_MS: float = 30_000.0
    _AGGRESSIVE_CANCEL_HORIZON_MS: float = 3_000.0
    _MIN_CANCEL_AFTER_TICKS_SHORT: int = 5
    _MAX_REPRICES_SHORT: int = 3
    _MAX_REPRICES_GENERAL: int = 10
    _HIGH_SUBMIT_TO_TICK_RATIO: float = 0.50
    _HIGH_CANCEL_TO_TICK_RATIO: float = 0.40
    _HIGH_TOTAL_LATENCY_TO_TICK_RATIO: float = 0.80
    _PASSIVE_MODES: frozenset[str] = frozenset({
        "passive_join", "passive_only", "passive_aggressive",
    })
    _SHORT_HORIZON_STYLE_HINTS: frozenset[str] = frozenset({
        "momentum", "scalping", "execution_adaptive",
    })
    _MICROSTRUCTURE_SENSITIVE_FEATURES: frozenset[str] = frozenset({
        "order_imbalance",
        "depth_imbalance",
        "spread_bps",
        "trade_imbalance",
        "book_pressure",
        "queue_imbalance",
        "microprice_deviation_bps",
        "top_level_imbalance",
    })

    def _to_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            result = float(value)
            return result
        except (TypeError, ValueError):
            return None

    def _canonical_tick_interval_ms(
        self,
        backtest_environment: dict[str, Any] | None,
    ) -> float | None:
        if not backtest_environment:
            return None
        tick_ms = self._to_float(backtest_environment.get("canonical_tick_interval_ms"))
        if tick_ms is None or tick_ms <= 0.0:
            return None
        return tick_ms

    def tick_to_ms(
        self,
        ticks: float | int | None,
        backtest_environment: dict[str, Any] | None,
    ) -> float | None:
        tick_ms = self._canonical_tick_interval_ms(backtest_environment)
        if tick_ms is None or ticks is None:
            return None
        return float(ticks) * tick_ms

    def holding_horizon_ms(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None,
    ) -> float | None:
        horizon = self._infer_holding_horizon(spec)
        return self.tick_to_ms(horizon, backtest_environment)

    def cancel_horizon_ms(
        self,
        xp: ExecutionPolicyV2,
        backtest_environment: dict[str, Any] | None,
    ) -> float | None:
        return self.tick_to_ms(xp.cancel_after_ticks, backtest_environment)

    def submit_to_tick_ratio(
        self,
        backtest_environment: dict[str, Any] | None,
    ) -> float | None:
        tick_ms = self._canonical_tick_interval_ms(backtest_environment)
        if tick_ms is None:
            return None
        latency = dict((backtest_environment or {}).get("latency") or {})
        submit_ms = self._to_float(latency.get("order_submit_ms"))
        if submit_ms is None:
            return None
        return submit_ms / tick_ms

    def cancel_to_tick_ratio(
        self,
        backtest_environment: dict[str, Any] | None,
    ) -> float | None:
        tick_ms = self._canonical_tick_interval_ms(backtest_environment)
        if tick_ms is None:
            return None
        latency = dict((backtest_environment or {}).get("latency") or {})
        cancel_ms = self._to_float(latency.get("cancel_ms"))
        if cancel_ms is None:
            return None
        return cancel_ms / tick_ms

    def _fmt_ms(self, value: float | None) -> str:
        if value is None:
            return "unknown"
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.1f}"

    def _describe_horizon(
        self,
        *,
        horizon_ticks: int | None,
        horizon_ms: float | None,
        resample: str,
    ) -> str:
        if horizon_ticks is None and horizon_ms is None:
            return "unknown"
        if horizon_ticks is not None and horizon_ms is not None:
            return (
                f"holding≤{horizon_ticks} ticks (~{self._fmt_ms(horizon_ms)}ms "
                f"at {resample} cadence)"
            )
        if horizon_ticks is not None:
            return f"holding≤{horizon_ticks} ticks"
        return f"~{self._fmt_ms(horizon_ms)}ms at {resample} cadence"

    def _check_execution_policy_churn(
        self,
        spec: StrategySpecV2,
        issues: list[ReviewIssue],
        backtest_environment: dict[str, Any] | None = None,
    ) -> None:
        """Deterministic hard-gate and high-risk checks for churn-heavy execution policies."""
        env = dict(backtest_environment or {})
        latency = dict(env.get("latency") or {})
        queue = dict(env.get("queue") or {})
        semantics = dict(env.get("semantics") or {})

        resample = str(env.get("resample", "unknown"))
        tick_ms = self._canonical_tick_interval_ms(env)
        submit_ratio = self.submit_to_tick_ratio(env)
        cancel_ratio = self.cancel_to_tick_ratio(env)
        combined_ratio: float | None = None
        if submit_ratio is not None and cancel_ratio is not None:
            combined_ratio = submit_ratio + cancel_ratio

        queue_model = str(queue.get("queue_model", "unknown"))
        replace_model = str(semantics.get("replace_model", "unknown"))
        market_data_delay_ms = self._to_float(env.get("market_data_delay_ms"))
        decision_compute_ms = self._to_float(env.get("decision_compute_ms"))
        effective_delay_ms = self._to_float(env.get("effective_delay_ms"))
        order_submit_ms = self._to_float(latency.get("order_submit_ms"))
        cancel_ms = self._to_float(latency.get("cancel_ms"))

        ratio_bits: list[str] = []
        if submit_ratio is not None:
            ratio_bits.append(f"submit/tick={submit_ratio:.2f}")
        if cancel_ratio is not None:
            ratio_bits.append(f"cancel/tick={cancel_ratio:.2f}")
        if combined_ratio is not None:
            ratio_bits.append(f"submit+cancel/tick={combined_ratio:.2f}")
        ratio_text = f" ({'; '.join(ratio_bits)})" if ratio_bits else ""

        latency_context_bits: list[str] = []
        if order_submit_ms is not None:
            latency_context_bits.append(f"submit_ms={self._fmt_ms(order_submit_ms)}")
        if cancel_ms is not None:
            latency_context_bits.append(f"cancel_ms={self._fmt_ms(cancel_ms)}")
        if market_data_delay_ms is not None:
            latency_context_bits.append(f"market_data_delay_ms={self._fmt_ms(market_data_delay_ms)}")
        if decision_compute_ms is not None:
            latency_context_bits.append(f"decision_compute_ms={self._fmt_ms(decision_compute_ms)}")
        if effective_delay_ms is not None:
            latency_context_bits.append(f"effective_delay_ms={self._fmt_ms(effective_delay_ms)}")
        env_context = ", ".join(latency_context_bits)
        if env_context:
            env_context = f"; env[{env_context}, queue_model={queue_model}, replace_model={replace_model}]"
        else:
            env_context = f"; env[queue_model={queue_model}, replace_model={replace_model}]"

        horizon_ticks = self._infer_holding_horizon(spec)
        horizon_ms = self.holding_horizon_ms(spec, env)
        short_by_ticks = horizon_ticks is not None and horizon_ticks <= self._SHORT_HORIZON_THRESHOLD
        short_by_ms = horizon_ms is not None and horizon_ms <= self._SHORT_HORIZON_THRESHOLD_MS
        is_short_horizon = short_by_ticks or short_by_ms
        horizon_desc = self._describe_horizon(
            horizon_ticks=horizon_ticks,
            horizon_ms=horizon_ms,
            resample=resample,
        )

        xp = spec.execution_policy

        # ── Missing execution policy checks ───────────────────────────
        if xp is None:
            style_short_hint = self._style_short_horizon_hint(spec)
            microstructure_sensitive = self._is_microstructure_sensitive(spec)
            fast_entry_cadence = self._has_fast_entry_cadence(spec, env)

            if is_short_horizon:
                issues.append(ReviewIssue(
                    severity="error",
                    category="missing_execution_policy_for_short_horizon",
                    description=(
                        f"Short horizon ({horizon_desc}) but no explicit execution_policy — "
                        f"strategy relies on engine defaults under cadence/latency/queue friction"
                        f"{ratio_text}{env_context}"
                    ),
                    suggestion=(
                        "Add an explicit execution_policy with conservative defaults: "
                        "placement_mode='passive_join', cancel_after_ticks=10-20, "
                        "max_reprices=2-3"
                    ),
                ))
            elif horizon_ticks is not None or horizon_ms is not None:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="execution_policy_implicit_risk",
                    description=(
                        f"No explicit execution_policy for time-bounded strategy ({horizon_desc}) — "
                        f"placement/cancel/repricing behavior is left to engine defaults{ratio_text}"
                    ),
                    suggestion=(
                        "Add explicit execution_policy controls for placement_mode, "
                        "cancel_after_ticks, and max_reprices"
                    ),
                ))
            elif microstructure_sensitive and (style_short_hint or fast_entry_cadence):
                issues.append(ReviewIssue(
                    severity="error",
                    category="missing_execution_policy_for_short_horizon",
                    description=(
                        "No explicit holding_ticks horizon, but strategy appears short-horizon "
                        "(microstructure-sensitive entries + short-horizon style/cadence hints) and "
                        f"execution_policy is missing{ratio_text}{env_context}"
                    ),
                    suggestion=(
                        "Add explicit execution_policy controls: placement_mode, cancel_after_ticks, "
                        "and max_reprices (conservative defaults recommended)"
                    ),
                ))
            elif microstructure_sensitive:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="execution_policy_implicit_risk",
                    description=(
                        "Microstructure-sensitive strategy has no explicit execution_policy; "
                        "engine defaults may under-model execution friction for this alpha"
                        f"{ratio_text}"
                    ),
                    suggestion=(
                        "Add explicit execution_policy with bounded cancel_after_ticks/max_reprices"
                    ),
                ))
            return

        is_passive = xp.placement_mode in self._PASSIVE_MODES
        has_robust_exit = self._has_robust_close_all(spec)
        cancel_horizon_ms = self.cancel_horizon_ms(xp, env)

        high_latency_ratio = False
        if submit_ratio is not None and submit_ratio >= self._HIGH_SUBMIT_TO_TICK_RATIO:
            high_latency_ratio = True
        if cancel_ratio is not None and cancel_ratio >= self._HIGH_CANCEL_TO_TICK_RATIO:
            high_latency_ratio = True
        if combined_ratio is not None and combined_ratio >= self._HIGH_TOTAL_LATENCY_TO_TICK_RATIO:
            high_latency_ratio = True

        effective_short_max_reprices = self._MAX_REPRICES_SHORT
        if high_latency_ratio:
            effective_short_max_reprices = max(1, self._MAX_REPRICES_SHORT - 1)

        # Rule 1: short-horizon + aggressive passive execution → error
        if is_short_horizon and is_passive and xp.max_reprices > effective_short_max_reprices:
            issues.append(ReviewIssue(
                severity="error",
                category="execution_policy_too_aggressive",
                description=(
                    f"Short horizon ({horizon_desc}) with passive placement '{xp.placement_mode}' "
                    f"and max_reprices={xp.max_reprices} exceeds env-aware budget "
                    f"(allowed≤{effective_short_max_reprices}){ratio_text}{env_context}"
                ),
                suggestion=(
                    f"Reduce max_reprices to ≤{effective_short_max_reprices} "
                    f"or switch to a less churn-sensitive placement mode"
                ),
            ))

        # Rule 2: cancel_after_ticks too short for short-horizon passive
        cancel_short_by_ticks = 0 < xp.cancel_after_ticks < self._MIN_CANCEL_AFTER_TICKS_SHORT
        cancel_short_by_ms = (
            cancel_horizon_ms is not None
            and xp.cancel_after_ticks > 0
            and cancel_horizon_ms < self._AGGRESSIVE_CANCEL_HORIZON_MS
        )
        if is_short_horizon and is_passive and (cancel_short_by_ticks or cancel_short_by_ms):
            cancel_ms_text = self._fmt_ms(cancel_horizon_ms)
            issues.append(ReviewIssue(
                severity="error",
                category="churn_risk_high",
                description=(
                    f"cancel_after_ticks={xp.cancel_after_ticks} (~{cancel_ms_text}ms at {resample} cadence) "
                    f"is too short for short-horizon passive strategy ({horizon_desc}) — "
                    f"orders are likely cancelled before queue fill opportunity{ratio_text}{env_context}"
                ),
                suggestion=(
                    f"Increase cancel_after_ticks so cancel horizon is >= {int(self._AGGRESSIVE_CANCEL_HORIZON_MS)}ms "
                    f"and at least {self._MIN_CANCEL_AFTER_TICKS_SHORT} ticks for passive modes"
                ),
            ))

        # Rule 3: general max_reprices too large
        if xp.max_reprices > self._MAX_REPRICES_GENERAL:
            issues.append(ReviewIssue(
                severity="warning",
                category="churn_risk_high",
                description=(
                    f"max_reprices={xp.max_reprices} is excessively large — "
                    f"each reprice resets queue position and incurs submit/cancel latency{ratio_text}"
                ),
                suggestion=(
                    f"Reduce max_reprices to ≤{self._MAX_REPRICES_GENERAL} "
                    f"to limit execution churn"
                ),
            ))

        # Rule 4: short-horizon without robust exit → error
        if is_short_horizon and not has_robust_exit:
            issues.append(ReviewIssue(
                severity="error",
                category="missing_robust_exit_for_short_horizon",
                description=(
                    f"Short horizon ({horizon_desc}) but no robust close_all fail-safe "
                    f"(stop-loss or time exit) — positions may become trapped"
                ),
                suggestion=(
                    "Add a stop-loss on unrealized_pnl_bps and/or "
                    "a time exit on holding_ticks"
                ),
            ))

        # Rule 5: passive mode with cancel_after_ticks=0 (infinite) → warning
        if is_passive and xp.cancel_after_ticks == 0:
            issues.append(ReviewIssue(
                severity="warning",
                category="queue_latency_mismatch",
                description=(
                    f"Passive placement '{xp.placement_mode}' with cancel_after_ticks=0 (no timeout) "
                    f"can leave stale orders in queue indefinitely{env_context}"
                ),
                suggestion="Set a finite cancel_after_ticks to bound order lifetime",
            ))

        # Rule 6: horizon ultra-short + passive + repricing → error
        ultra_short = (
            (horizon_ticks is not None and horizon_ticks <= 3)
            or (horizon_ms is not None and horizon_ms <= 1_500.0)
        )
        if ultra_short and is_passive and xp.max_reprices > 1:
            issues.append(ReviewIssue(
                severity="error",
                category="execution_policy_too_aggressive",
                description=(
                    f"Ultra-short horizon ({horizon_desc}) with passive repricing "
                    f"(max_reprices={xp.max_reprices}) leaves almost no queue dwell time between reprices"
                    f"{ratio_text}"
                ),
                suggestion=(
                    "For ultra-short horizons, use immediate/aggressive placement "
                    "or set max_reprices=0-1"
                ),
            ))

        # Rule 7: latency-to-tick ratio is high → tighten passive repricing tolerance
        if (
            is_short_horizon
            and is_passive
            and high_latency_ratio
            and 1 < xp.max_reprices <= effective_short_max_reprices
        ):
            issues.append(ReviewIssue(
                severity="warning",
                category="churn_risk_high",
                description=(
                    f"Short-horizon passive strategy uses max_reprices={xp.max_reprices} in a high latency/tick "
                    f"environment{ratio_text}; churn cost may dominate edge"
                ),
                suggestion=(
                    "Prefer lower repricing budget (e.g., 1-2), longer cancel horizon, "
                    "and stronger time/stop exits"
                ),
            ))

    def _style_short_horizon_hint(self, spec: StrategySpecV2) -> bool:
        meta = dict(spec.metadata or {})
        for key in ("strategy_style", "plan_style"):
            raw = meta.get(key)
            if isinstance(raw, str) and raw.strip().lower() in self._SHORT_HORIZON_STYLE_HINTS:
                return True
        return False

    def _has_fast_entry_cadence(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None = None,
    ) -> bool:
        for ep in spec.entry_policies:
            cooldown = int(ep.constraints.cooldown_ticks)
            if 0 < cooldown <= self._SHORT_HORIZON_THRESHOLD:
                return True
            cooldown_ms = self.tick_to_ms(cooldown, backtest_environment)
            if cooldown_ms is not None and 0 < cooldown_ms <= self._SHORT_HORIZON_THRESHOLD_MS:
                return True
        return False

    def _is_microstructure_sensitive(self, spec: StrategySpecV2) -> bool:
        features = spec.collect_all_features()
        return bool(features & self._MICROSTRUCTURE_SENSITIVE_FEATURES)

    def _infer_holding_horizon(self, spec: StrategySpecV2) -> int | None:
        """Infer the expected holding horizon from time-exit thresholds.

        Returns the smallest holding_ticks threshold found in exit rules,
        or None if no time-exit exists.
        """
        min_horizon: int | None = None
        for xp in spec.exit_policies:
            for rule in xp.rules:
                cond = rule.condition
                if (
                    isinstance(cond, ComparisonExpr)
                    and isinstance(cond.left, PositionAttrExpr)
                    and cond.left.name == "holding_ticks"
                    and cond.op in {">=", ">"}
                ):
                    val = int(cond.threshold)
                    if min_horizon is None or val < min_horizon:
                        min_horizon = val
        return min_horizon
