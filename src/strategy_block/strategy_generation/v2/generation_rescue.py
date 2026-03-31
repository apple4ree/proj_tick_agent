"""Deterministic one-shot rescue for fixable generation-time static-review failures."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import (
    ExecutionPolicyV2,
    ExitActionV2,
    ExitRuleV2,
    StrategySpecV2,
)


@dataclass
class GenerationRescueResult:
    applied: bool
    operations: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    rescued_spec: object | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _NormalizedIssue:
    severity: str
    category: str
    description: str
    code: str | None

    def reason_key(self) -> str:
        code = self.code or "NO_CODE"
        return f"{self.category}:{code}"


class GenerationRescue:
    """Patch narrowly-fixable execution/horizon failures without extra LLM calls."""

    _SHORT_HORIZON_TICKS: int = 30
    _RESCUE_MIN_HOLDING_TICKS: int = 10
    _FAILSAFE_HOLDING_TICKS: int = 20
    _PASSIVE_MODES: frozenset[str] = frozenset({
        "passive_join",
        "passive_only",
        "passive_aggressive",
    })
    _SHORT_STYLE_HINTS: frozenset[str] = frozenset({
        "momentum",
        "scalping",
        "execution_adaptive",
    })
    _RESCUABLE_CATEGORIES: frozenset[str] = frozenset({
        "missing_execution_policy_for_short_horizon",
        "execution_policy_too_aggressive",
        "churn_risk_high",
        "latency_feasibility_risk",
        "missing_robust_exit_for_short_horizon",
    })
    _RESCUABLE_CODES: frozenset[str] = frozenset({
        "FEATURE_TIME_NEAR_ZERO_HORIZON",
        "LATENCY_FEASIBILITY_MISSING_EP_SHORT_HORIZON",
        "LATENCY_FEASIBILITY_TINY_CANCEL_HORIZON",
        "LATENCY_FEASIBILITY_PASSIVE_REPRICE_BURST",
        "LATENCY_FEASIBILITY_CANCEL_BELOW_ROUNDTRIP",
        "LATENCY_FEASIBILITY_LATENCY_TICK_RATIO_MISMATCH",
    })
    _NON_RESCUABLE_CODES_PREFIXES: tuple[str, ...] = (
        "LOOKAHEAD_",
        "FILL_ALIGNMENT_",
    )
    _NON_RESCUABLE_CATEGORIES: frozenset[str] = frozenset({
        "schema",
        "expression_safety",
        "state_reference_integrity",
        "state_deadlock",
        "degradation_conflict",
        "position_attr_as_feature",
        "dead_exit_path",
        "leakage_lookahead_risk",
        "leakage_fill_alignment_risk",
    })

    _ISSUE_CODE_RE = re.compile(r"\[([A-Z0-9_]+)\]")

    def maybe_rescue(
        self,
        *,
        spec,
        review_result,
        backtest_environment: dict[str, Any] | None = None,
    ) -> GenerationRescueResult:
        normalized = self._normalize_review_issues(review_result)
        error_issues = [i for i in normalized if i.severity == "error"]
        result = GenerationRescueResult(
            applied=False,
            reasons=[i.reason_key() for i in error_issues],
            metadata={
                "eligible": False,
                "error_issue_count": len(error_issues),
                "error_issue_categories": [i.category for i in error_issues],
                "error_issue_codes": [i.code for i in error_issues if i.code],
            },
        )
        if not isinstance(spec, StrategySpecV2) or not error_issues:
            result.metadata["skip_reason"] = "invalid_spec_or_no_error_issue"
            return result

        ineligible = [i for i in error_issues if not self._is_rescuable_issue(i)]
        if ineligible:
            result.metadata["skip_reason"] = "non_rescuable_error_present"
            result.metadata["non_rescuable"] = [i.reason_key() for i in ineligible]
            return result

        rescued = copy.deepcopy(spec)
        operations: list[str] = []

        min_holding_before = self._infer_holding_horizon_ticks(rescued)
        short_horizon = self._is_short_horizon(
            rescued,
            inferred_holding_ticks=min_holding_before,
            force_short_horizon=self._has_missing_execution_policy_issue(error_issues),
        )

        # 1) zero / near-zero holding horizon rescue.
        if self._has_zero_horizon_issue(error_issues) or (min_holding_before is not None and min_holding_before <= 0):
            if self._raise_non_positive_holding_horizon(
                rescued,
                min_ticks=self._RESCUE_MIN_HOLDING_TICKS,
            ):
                operations.append("raise_non_positive_holding_horizon_to_min_10")

        # 2) insert default execution policy for short-horizon missing-policy cases.
        if (
            rescued.execution_policy is None
            and short_horizon
            and self._has_missing_execution_policy_issue(error_issues)
        ):
            rescued.execution_policy = ExecutionPolicyV2(
                placement_mode="passive_join",
                cancel_after_ticks=10,
                max_reprices=2,
            )
            operations.append("insert_default_execution_policy_for_short_horizon")

        # 3) clamp aggressive passive repricing envelope.
        if rescued.execution_policy is not None and self._is_passive(rescued.execution_policy):
            if short_horizon or self._has_aggressive_execution_issue(error_issues):
                if rescued.execution_policy.max_reprices > 2:
                    rescued.execution_policy.max_reprices = 2
                    operations.append("clamp_passive_max_reprices_to_2")
                if rescued.execution_policy.cancel_after_ticks < 10:
                    rescued.execution_policy.cancel_after_ticks = 10
                    operations.append("raise_passive_cancel_after_ticks_to_10")

        # 4) add robust time exit when missing and needed (no duplicate if already exists).
        need_fail_safe_time_exit = self._needs_fail_safe_time_exit(
            rescued,
            error_issues=error_issues,
            short_horizon=short_horizon,
        )
        if need_fail_safe_time_exit and self._add_fail_safe_time_exit_if_missing(
            rescued,
            holding_ticks=self._FAILSAFE_HOLDING_TICKS,
        ):
            operations.append("add_fail_safe_holding_ticks_close_all_exit")

        rescued.metadata = dict(rescued.metadata or {})
        rescue_meta = {
            "attempted": True,
            "eligible": True,
            "applied": bool(operations),
            "operations": list(operations),
            "reasons": list(result.reasons),
            "holding_horizon_before": min_holding_before,
            "holding_horizon_after": self._infer_holding_horizon_ticks(rescued),
            "short_horizon": short_horizon,
        }
        rescued.metadata["generation_rescue"] = rescue_meta

        schema_errors = rescued.validate()
        if schema_errors:
            result.metadata.update({
                "eligible": True,
                "skip_reason": "rescued_spec_schema_invalid",
                "schema_errors": schema_errors,
            })
            return result

        if not operations:
            result.metadata.update({
                "eligible": True,
                "skip_reason": "no_deterministic_operation_applied",
            })
            return result

        result.applied = True
        result.operations = operations
        result.rescued_spec = rescued
        result.metadata.update(rescue_meta)
        return result

    def _normalize_review_issues(self, review_result: Any) -> list[_NormalizedIssue]:
        issues_raw: list[Any] = []
        if review_result is None:
            return []
        if isinstance(review_result, dict):
            issues_raw = list(review_result.get("issues") or [])
        elif hasattr(review_result, "issues"):
            issues_raw = list(getattr(review_result, "issues") or [])

        normalized: list[_NormalizedIssue] = []
        for issue in issues_raw:
            if isinstance(issue, dict):
                severity = str(issue.get("severity", "")).strip().lower()
                category = str(issue.get("category", "")).strip()
                description = str(issue.get("description", "")).strip()
            else:
                severity = str(getattr(issue, "severity", "")).strip().lower()
                category = str(getattr(issue, "category", "")).strip()
                description = str(getattr(issue, "description", "")).strip()
            normalized.append(
                _NormalizedIssue(
                    severity=severity,
                    category=category,
                    description=description,
                    code=self._extract_issue_code(description),
                )
            )
        return normalized

    def _extract_issue_code(self, description: str) -> str | None:
        match = self._ISSUE_CODE_RE.search(description or "")
        if not match:
            return None
        return match.group(1)

    def _is_rescuable_issue(self, issue: _NormalizedIssue) -> bool:
        if issue.code is not None:
            for prefix in self._NON_RESCUABLE_CODES_PREFIXES:
                if issue.code.startswith(prefix):
                    return False

        if issue.category in self._NON_RESCUABLE_CATEGORIES:
            return False

        if issue.code == "FEATURE_TIME_NEAR_ZERO_HORIZON":
            return True

        if issue.code in self._RESCUABLE_CODES:
            return True

        if issue.category == "leakage_feature_time_risk":
            return False

        if issue.category in self._RESCUABLE_CATEGORIES:
            return True

        lowered = issue.description.lower()
        if "short horizon" in lowered and "execution_policy" in lowered:
            return True
        if "max_reprices" in lowered or "cancel_after_ticks" in lowered:
            return True
        return False

    def _has_zero_horizon_issue(self, issues: list[_NormalizedIssue]) -> bool:
        for issue in issues:
            if issue.code == "FEATURE_TIME_NEAR_ZERO_HORIZON":
                return True
        return False

    def _has_missing_execution_policy_issue(self, issues: list[_NormalizedIssue]) -> bool:
        for issue in issues:
            if issue.category == "missing_execution_policy_for_short_horizon":
                return True
            if issue.code == "LATENCY_FEASIBILITY_MISSING_EP_SHORT_HORIZON":
                return True
        return False

    def _has_aggressive_execution_issue(self, issues: list[_NormalizedIssue]) -> bool:
        aggressive_codes = {
            "LATENCY_FEASIBILITY_TINY_CANCEL_HORIZON",
            "LATENCY_FEASIBILITY_PASSIVE_REPRICE_BURST",
            "LATENCY_FEASIBILITY_CANCEL_BELOW_ROUNDTRIP",
            "LATENCY_FEASIBILITY_LATENCY_TICK_RATIO_MISMATCH",
        }
        for issue in issues:
            if issue.category in {
                "execution_policy_too_aggressive",
                "churn_risk_high",
                "latency_feasibility_risk",
            }:
                return True
            if issue.code in aggressive_codes:
                return True
        return False

    def _needs_fail_safe_time_exit(
        self,
        spec: StrategySpecV2,
        *,
        error_issues: list[_NormalizedIssue],
        short_horizon: bool,
    ) -> bool:
        has_missing_robust_exit_issue = any(
            issue.category == "missing_robust_exit_for_short_horizon"
            for issue in error_issues
        )
        has_time_exit = self._has_close_all_holding_ticks_exit(spec)
        if has_missing_robust_exit_issue:
            return not has_time_exit
        if short_horizon and self._infer_holding_horizon_ticks(spec) is None:
            return not has_time_exit
        return False

    def _is_short_horizon(
        self,
        spec: StrategySpecV2,
        *,
        inferred_holding_ticks: int | None,
        force_short_horizon: bool = False,
    ) -> bool:
        if force_short_horizon:
            return True
        if inferred_holding_ticks is not None:
            return inferred_holding_ticks <= self._SHORT_HORIZON_TICKS

        meta = dict(spec.metadata or {})
        inferred_short = meta.get("inferred_short_horizon")
        if isinstance(inferred_short, bool):
            return inferred_short

        style_hint = ""
        for key in ("strategy_style", "plan_style"):
            raw = meta.get(key)
            if isinstance(raw, str) and raw.strip():
                style_hint = raw.strip().lower()
                break
        if style_hint in self._SHORT_STYLE_HINTS:
            return True

        for entry in spec.entry_policies:
            cooldown = int(entry.constraints.cooldown_ticks)
            if 0 < cooldown <= self._SHORT_HORIZON_TICKS:
                return True
        return False

    def _is_passive(self, xp: ExecutionPolicyV2) -> bool:
        return xp.placement_mode in self._PASSIVE_MODES

    def _iter_holding_ticks_rules(self, spec: StrategySpecV2):
        for policy in spec.exit_policies:
            for rule in policy.rules:
                cond = rule.condition
                if (
                    isinstance(cond, ComparisonExpr)
                    and isinstance(cond.left, PositionAttrExpr)
                    and cond.left.name == "holding_ticks"
                    and cond.op in {">=", ">"}
                ):
                    yield policy, rule, cond

    def _infer_holding_horizon_ticks(self, spec: StrategySpecV2) -> int | None:
        inferred: int | None = None
        for _, _, cond in self._iter_holding_ticks_rules(spec):
            threshold = int(cond.threshold)
            if inferred is None or threshold < inferred:
                inferred = threshold
        return inferred

    def _raise_non_positive_holding_horizon(self, spec: StrategySpecV2, *, min_ticks: int) -> bool:
        changed = False
        for _, _, cond in self._iter_holding_ticks_rules(spec):
            if int(cond.threshold) <= 0:
                cond.op = ">="
                cond.threshold = float(min_ticks)
                changed = True
        return changed

    def _has_close_all_holding_ticks_exit(self, spec: StrategySpecV2) -> bool:
        for _, rule, _ in self._iter_holding_ticks_rules(spec):
            if rule.action.type == "close_all":
                return True
        return False

    def _next_exit_priority(self, spec: StrategySpecV2) -> int:
        priorities = [r.priority for policy in spec.exit_policies for r in policy.rules]
        return (max(priorities) + 1) if priorities else 1

    def _add_fail_safe_time_exit_if_missing(self, spec: StrategySpecV2, *, holding_ticks: int) -> bool:
        if not spec.exit_policies:
            return False
        if self._has_close_all_holding_ticks_exit(spec):
            return False
        new_rule = ExitRuleV2(
            name="generation_rescue_time_exit",
            priority=self._next_exit_priority(spec),
            condition=ComparisonExpr(
                left=PositionAttrExpr("holding_ticks"),
                op=">=",
                threshold=float(holding_ticks),
            ),
            action=ExitActionV2(type="close_all"),
        )
        spec.exit_policies[0].rules.append(new_rule)
        return True
