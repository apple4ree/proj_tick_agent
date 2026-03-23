"""Deterministic, rule-based strategy spec reviewer.

Replaces the archived LLM ReviewerAgent with static validation rules
that check strategy specs for common issues before deployment.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from strategy_block.strategy_specs.schema import StrategySpec

logger = logging.getLogger(__name__)

# Features the StrategyCompiler can always resolve from LOB / trades
KNOWN_FEATURES: set[str] = {
    "mid_price", "spread_bps", "order_imbalance",
    "best_bid", "best_ask",
    "bid_depth_5", "ask_depth_5", "depth_imbalance",
    "trade_count", "recent_volume", "trade_flow_imbalance",
    "price_impact_buy", "price_impact_sell",
    "price_impact_buy_bps", "price_impact_sell_bps",
    "volume_surprise", "micro_price", "trade_flow",
    "depth_imbalance_l1",
    "log_bid_depth", "log_ask_depth",
    "bid_depth", "ask_depth",
}


@dataclass
class ReviewIssue:
    severity: str       # "error", "warning", "info"
    category: str       # e.g. "schema", "risk", "redundancy"
    description: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


@dataclass
class ReviewResult:
    passed: bool
    issues: list[ReviewIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
        }


class StrategyReviewer:
    """Rule-based strategy spec reviewer.

    Runs a battery of static checks and returns a ReviewResult with
    pass/fail status and detailed issues.
    """

    def review(self, spec: StrategySpec) -> ReviewResult:
        issues: list[ReviewIssue] = []

        self._check_schema(spec, issues)
        self._check_signal_rules(spec, issues)
        self._check_filters(spec, issues)
        self._check_exit_rules(spec, issues)
        self._check_position_rule(spec, issues)
        self._check_duplicates(spec, issues)
        self._check_features(spec, issues)

        has_error = any(i.severity == "error" for i in issues)
        return ReviewResult(passed=not has_error, issues=issues)

    # ── Individual checks ──────────────────────────────────────────

    def _check_schema(self, spec: StrategySpec, issues: list[ReviewIssue]) -> None:
        errors = spec.validate()
        for err in errors:
            issues.append(ReviewIssue(
                severity="error",
                category="schema",
                description=err,
            ))

    def _check_signal_rules(self, spec: StrategySpec, issues: list[ReviewIssue]) -> None:
        if not spec.signal_rules:
            issues.append(ReviewIssue(
                severity="error",
                category="signal",
                description="No signal rules defined",
                suggestion="Add at least one signal rule with a feature, operator, and threshold",
            ))
            return

        if len(spec.signal_rules) > 10:
            issues.append(ReviewIssue(
                severity="warning",
                category="complexity",
                description=f"Too many signal rules ({len(spec.signal_rules)})",
                suggestion="Consider reducing to 5-6 rules to avoid overfitting",
            ))

        total_positive = sum(
            r.score_contribution for r in spec.signal_rules if r.score_contribution > 0
        )
        total_negative = sum(
            r.score_contribution for r in spec.signal_rules if r.score_contribution < 0
        )

        if total_positive == 0 and total_negative == 0:
            issues.append(ReviewIssue(
                severity="warning",
                category="signal",
                description="All score contributions are zero — strategy will never generate signals",
                suggestion="Set non-zero score_contribution on at least one rule",
            ))

        if total_positive > 0 and total_negative == 0:
            issues.append(ReviewIssue(
                severity="info",
                category="signal",
                description="Strategy only generates buy signals (no negative contributions)",
                suggestion="Consider adding sell rules for balanced coverage",
            ))
        elif total_negative < 0 and total_positive == 0:
            issues.append(ReviewIssue(
                severity="info",
                category="signal",
                description="Strategy only generates sell signals (no positive contributions)",
                suggestion="Consider adding buy rules for balanced coverage",
            ))

    def _check_filters(self, spec: StrategySpec, issues: list[ReviewIssue]) -> None:
        if len(spec.filters) > 5:
            issues.append(ReviewIssue(
                severity="warning",
                category="complexity",
                description=f"Too many filters ({len(spec.filters)})",
                suggestion="Many filters may block most signals — review necessity",
            ))

        for filt in spec.filters:
            if filt.feature == "spread_bps" and filt.operator == ">" and filt.threshold < 1.0:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="filter",
                    description=f"Spread filter threshold {filt.threshold} bps is very tight",
                    suggestion="KRX tick spread is typically 1-10 bps for large caps; threshold < 1 may block all signals",
                ))

    def _check_exit_rules(self, spec: StrategySpec, issues: list[ReviewIssue]) -> None:
        exit_types = {r.exit_type for r in spec.exit_rules}

        if not spec.exit_rules:
            issues.append(ReviewIssue(
                severity="warning",
                category="risk",
                description="No exit rules defined — positions may be held indefinitely",
                suggestion="Add at least stop_loss and time_exit rules",
            ))
            return

        if "stop_loss" not in exit_types:
            issues.append(ReviewIssue(
                severity="warning",
                category="risk",
                description="No stop_loss rule — unbounded downside risk",
                suggestion="Add a stop_loss rule (e.g., 15-30 bps for tick strategies)",
            ))

        if "time_exit" not in exit_types:
            issues.append(ReviewIssue(
                severity="info",
                category="risk",
                description="No time_exit rule — stale positions may persist",
                suggestion="Add a time_exit rule to force close after N ticks",
            ))

        for rule in spec.exit_rules:
            if rule.exit_type == "stop_loss" and rule.threshold_bps > 100:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="risk",
                    description=f"Stop loss threshold ({rule.threshold_bps} bps) is very wide for tick-level trading",
                    suggestion="Typical tick-level stop loss is 10-30 bps",
                ))
            if rule.exit_type == "take_profit" and rule.threshold_bps > 200:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="risk",
                    description=f"Take profit threshold ({rule.threshold_bps} bps) may rarely trigger at tick level",
                    suggestion="Typical tick-level take profit is 10-50 bps",
                ))
            if rule.exit_type == "time_exit" and rule.timeout_ticks < 5:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="risk",
                    description=f"Time exit after {rule.timeout_ticks} ticks is extremely short",
                    suggestion="Consider at least 30-100 ticks to allow mean reversion",
                ))

    def _check_position_rule(self, spec: StrategySpec, issues: list[ReviewIssue]) -> None:
        pr = spec.position_rule
        if pr.max_position <= 0:
            issues.append(ReviewIssue(
                severity="error",
                category="position",
                description=f"max_position={pr.max_position} is non-positive",
                suggestion="Set max_position to a positive integer (e.g., 100-1000)",
            ))
        if pr.inventory_cap <= 0:
            issues.append(ReviewIssue(
                severity="error",
                category="position",
                description=f"inventory_cap={pr.inventory_cap} is non-positive",
                suggestion="Set inventory_cap to a positive integer",
            ))
        if pr.inventory_cap < pr.max_position:
            issues.append(ReviewIssue(
                severity="warning",
                category="position",
                description=(
                    f"inventory_cap ({pr.inventory_cap}) < max_position ({pr.max_position}) "
                    f"— inventory cap will bind before max_position"
                ),
                suggestion="Set inventory_cap >= max_position for consistency",
            ))
        if pr.sizing_mode == "fixed" and pr.fixed_size > pr.max_position:
            issues.append(ReviewIssue(
                severity="warning",
                category="position",
                description=f"fixed_size ({pr.fixed_size}) > max_position ({pr.max_position})",
                suggestion="Reduce fixed_size or increase max_position",
            ))

    def _check_duplicates(self, spec: StrategySpec, issues: list[ReviewIssue]) -> None:
        seen: set[tuple[str, str, float]] = set()
        for rule in spec.signal_rules:
            key = (rule.feature, rule.operator, rule.threshold)
            if key in seen:
                issues.append(ReviewIssue(
                    severity="warning",
                    category="redundancy",
                    description=(
                        f"Duplicate signal rule: {rule.feature} {rule.operator} {rule.threshold}"
                    ),
                    suggestion="Remove or merge duplicate rules",
                ))
            seen.add(key)

    def _check_features(self, spec: StrategySpec, issues: list[ReviewIssue]) -> None:
        all_features: set[str] = set()
        for rule in spec.signal_rules:
            all_features.add(rule.feature)
        for filt in spec.filters:
            all_features.add(filt.feature)

        unknown = all_features - KNOWN_FEATURES
        if unknown:
            issues.append(ReviewIssue(
                severity="info",
                category="feature",
                description=f"Unknown features: {', '.join(sorted(unknown))}",
                suggestion=(
                    "These features must be provided via state.features dict at runtime. "
                    "Ensure your feature pipeline populates them."
                ),
            ))
