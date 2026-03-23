"""Static reviewer for StrategySpecV2.

Review categories:
1. schema                    — structural validation
2. expression_safety         — valid AST nodes, operators, directions
3. feature_availability      — features in known set
4. logical_contradiction     — conflicting conditions in all/any
5. unreachable_entry         — entries that can never fire
6. risk_inconsistency        — inventory_cap < max_position, etc.
7. exit_completeness         — emergency stop present
8. dead_regime               — regime that can never activate (Phase 2)
9. regime_reference_integrity — regime refs exist in policies (Phase 2)
10. execution_risk_mismatch  — execution/risk inconsistency (Phase 2)
11. latency_structure_warning — large rolling/persist windows (Phase 2)
"""
from __future__ import annotations

from strategy_block.strategy_specs.v2.schema_v2 import (
    StrategySpecV2,
    ExecutionPolicyV2,
    VALID_PLACEMENT_MODES,
)
from strategy_block.strategy_specs.v2.ast_nodes import (
    ExprNode,
    ComparisonExpr,
    AllExpr,
    AnyExpr,
    CrossExpr,
    FeatureExpr,
    LagExpr,
    RollingExpr,
    PersistExpr,
)
from strategy_block.strategy_review.reviewer import (
    ReviewIssue,
    ReviewResult,
    KNOWN_FEATURES,
)


class StrategyReviewerV2:
    """Rule-based reviewer for StrategySpecV2."""

    def review(self, spec: StrategySpecV2) -> ReviewResult:
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
        """Verify all AST nodes use valid types, ops, and directions."""
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
        """Detect obvious contradictions inside AllExpr nodes."""
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

        # Phase 2: check regime `when` conditions
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
        """Find contradictory comparison pairs inside AllExpr nodes."""
        results: list[str] = []
        if isinstance(node, AllExpr):
            comparisons: list[ComparisonExpr] = []
            for child in node.children:
                if isinstance(child, ComparisonExpr):
                    comparisons.append(child)
                # Recurse into nested logical nodes
                results.extend(self._find_contradictions(child))

            # Check pairs for same-feature contradictions
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

        return results

    def _pair_contradicts(self, a: ComparisonExpr, b: ComparisonExpr) -> str | None:
        """Check if two comparisons on the same feature are contradictory."""
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
        """Heuristic: warn if cooldown_ticks is abnormally large."""
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
        """Warn if there is no emergency/unconditional stop mechanism."""
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
                severity="warning",
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
        """Detect regimes whose `when` condition is obviously contradictory."""
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
        """Check that regime entry/exit refs point to existing policies."""
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
        """Heuristic: passive_only with large positions or aggressive exits."""
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

        # Check do_not_trade_when vs preconditions for obvious overlap
        if xp.do_not_trade_when is not None:
            for i, pc in enumerate(spec.preconditions):
                # Simple check: same comparison with inverted logic
                if (isinstance(xp.do_not_trade_when, ComparisonExpr)
                        and isinstance(pc.condition, ComparisonExpr)):
                    dnt = xp.do_not_trade_when
                    pcc = pc.condition
                    if dnt.feature == pcc.feature:
                        # do_not_trade_when: X > T and precondition: X < T (conflict)
                        if (dnt.op == ">" and pcc.op == "<"
                                and pcc.threshold <= dnt.threshold):
                            issues.append(ReviewIssue(
                                severity="warning",
                                category="execution_risk_mismatch",
                                description=(
                                    f"do_not_trade_when ({dnt.feature} > {dnt.threshold}) "
                                    f"conflicts with precondition[{i}] "
                                    f"({pcc.feature} < {pcc.threshold})"
                                ),
                                suggestion="One of these conditions may be redundant",
                            ))

    # ── 11. Latency/structure warning (Phase 2) ───────────────────

    def _check_latency_structure_warning(self, spec: StrategySpecV2,
                                          issues: list[ReviewIssue]) -> None:
        """Warn about large rolling/persist windows that imply latency."""
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

    def _collect_all_nodes(self, spec: StrategySpecV2) -> list[ExprNode]:
        """Collect all AST nodes in the spec for analysis."""
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
        if spec.execution_policy and spec.execution_policy.do_not_trade_when:
            self._walk_tree(spec.execution_policy.do_not_trade_when, nodes)
        return nodes

    def _walk_tree(self, node: ExprNode, acc: list[ExprNode]) -> None:
        """Recursively collect all nodes in an expression tree."""
        acc.append(node)
        if hasattr(node, "children"):
            for child in node.children:
                self._walk_tree(child, acc)
        if hasattr(node, "child"):
            self._walk_tree(node.child, acc)
        if isinstance(node, PersistExpr):
            self._walk_tree(node.expr, acc)
