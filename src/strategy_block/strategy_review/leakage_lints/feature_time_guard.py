from __future__ import annotations

from typing import Any

from strategy_block.strategy_review.leakage_lints.models import LeakageLintIssue
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ConstExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2


class FeatureTimeGuard:
    """Detect overly reactive or liveness-risky time structures."""

    _SHORT_HORIZON_TICKS = 30
    _ULTRA_SHORT_HORIZON_TICKS = 1
    _MICROSTRUCTURE_SENSITIVE_FEATURES: frozenset[str] = frozenset(
        {
            "order_imbalance",
            "depth_imbalance",
            "spread_bps",
            "trade_imbalance",
            "book_pressure",
            "queue_imbalance",
            "microprice_deviation_bps",
            "top_level_imbalance",
        }
    )

    def run(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None = None,
    ) -> list[LeakageLintIssue]:
        issues: list[LeakageLintIssue] = []

        horizon_ticks = self._infer_holding_horizon(spec)
        min_cooldown = self._min_entry_cooldown(spec)
        micro_sensitive = self._is_microstructure_sensitive(spec)
        has_robust_exit = self._has_robust_close_all(spec)
        tick_ms = self._tick_ms(backtest_environment)

        if horizon_ticks is not None and horizon_ticks <= 0:
            issues.append(
                LeakageLintIssue(
                    code="FEATURE_TIME_NEAR_ZERO_HORIZON",
                    severity="error",
                    message="holding horizon is configured as 0 ticks; this is effectively a same-tick strategy and is unsafe.",
                    details={"holding_horizon_ticks": horizon_ticks},
                )
            )

        if (
            horizon_ticks is not None
            and 0 < horizon_ticks <= self._ULTRA_SHORT_HORIZON_TICKS
            and min_cooldown == 0
        ):
            issues.append(
                LeakageLintIssue(
                    code="FEATURE_TIME_ZERO_COOLDOWN_ULTRA_SHORT",
                    severity="warning",
                    message="ultra-short holding horizon with zero cooldown can fire repeatedly without stabilization.",
                    details={
                        "holding_horizon_ticks": horizon_ticks,
                        "min_cooldown_ticks": min_cooldown,
                    },
                )
            )

        if (
            micro_sensitive
            and horizon_ticks is not None
            and horizon_ticks <= self._SHORT_HORIZON_TICKS
            and min_cooldown == 0
            and not has_robust_exit
        ):
            issues.append(
                LeakageLintIssue(
                    code="FEATURE_TIME_MICROSTRUCTURE_NO_LIVENESS_GUARD",
                    severity="error",
                    message="short-horizon microstructure strategy has no cooldown and no robust close_all fail-safe; liveness risk is high.",
                    details={
                        "holding_horizon_ticks": horizon_ticks,
                        "min_cooldown_ticks": min_cooldown,
                    },
                )
            )

        if (
            micro_sensitive
            and horizon_ticks is None
            and min_cooldown == 0
            and not has_robust_exit
        ):
            issues.append(
                LeakageLintIssue(
                    code="FEATURE_TIME_MICROSTRUCTURE_LIVENESS_UNCLEAR",
                    severity="warning",
                    message="microstructure strategy has no explicit holding horizon and no robust close_all fail-safe.",
                    details={"min_cooldown_ticks": min_cooldown},
                )
            )

        if micro_sensitive and spec.execution_policy is None and min_cooldown == 0:
            issues.append(
                LeakageLintIssue(
                    code="FEATURE_TIME_UNGATED_MICROSTRUCTURE",
                    severity="warning",
                    message="microstructure-sensitive strategy has no explicit execution policy and no entry cooldown.",
                    details={"min_cooldown_ticks": min_cooldown},
                )
            )

        if tick_ms is not None and horizon_ticks is not None and horizon_ticks <= 2:
            horizon_ms = horizon_ticks * tick_ms
            if horizon_ms <= 2 * tick_ms:
                issues.append(
                    LeakageLintIssue(
                        code="FEATURE_TIME_CADENCE_REACTIVE_PATTERN",
                        severity="warning",
                        message="holding horizon is within ~2 ticks of cadence; this pattern is highly reactive and fragile.",
                        details={
                            "holding_horizon_ticks": horizon_ticks,
                            "holding_horizon_ms": horizon_ms,
                            "canonical_tick_interval_ms": tick_ms,
                        },
                    )
                )

        return issues

    def _tick_ms(self, backtest_environment: dict[str, Any] | None) -> float | None:
        if not backtest_environment:
            return None
        raw = backtest_environment.get("canonical_tick_interval_ms")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _infer_holding_horizon(self, spec: StrategySpecV2) -> int | None:
        inferred: int | None = None
        for xp in spec.exit_policies:
            for rule in xp.rules:
                cond = rule.condition
                if (
                    isinstance(cond, ComparisonExpr)
                    and isinstance(cond.left, PositionAttrExpr)
                    and cond.left.name == "holding_ticks"
                    and cond.op in {">=", ">"}
                ):
                    ticks = int(cond.threshold)
                    if inferred is None or ticks < inferred:
                        inferred = ticks
        return inferred

    def _min_entry_cooldown(self, spec: StrategySpecV2) -> int:
        if not spec.entry_policies:
            return 0
        return min(int(ep.constraints.cooldown_ticks) for ep in spec.entry_policies)

    def _is_microstructure_sensitive(self, spec: StrategySpecV2) -> bool:
        return bool(spec.collect_all_features() & self._MICROSTRUCTURE_SENSITIVE_FEATURES)

    def _has_robust_close_all(self, spec: StrategySpecV2) -> bool:
        for xp in spec.exit_policies:
            for rule in xp.rules:
                if rule.action.type != "close_all":
                    continue
                if isinstance(rule.condition, ConstExpr) and rule.condition.value != 0.0:
                    return True
                cond = rule.condition
                if isinstance(cond, ComparisonExpr) and isinstance(cond.left, PositionAttrExpr):
                    if cond.left.name in {"holding_ticks", "unrealized_pnl_bps"}:
                        return True
        return False
