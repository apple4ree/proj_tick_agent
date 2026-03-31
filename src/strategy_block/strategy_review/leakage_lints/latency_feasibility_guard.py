from __future__ import annotations

from typing import Any

from strategy_block.strategy_review.leakage_lints.models import LeakageLintIssue
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import ExecutionPolicyV2, StrategySpecV2


class LatencyFeasibilityGuard:
    """Check whether short-horizon specs are feasible under configured cadence/latency."""

    _SHORT_HORIZON_TICKS = 30
    _SHORT_HORIZON_MS = 30_000.0
    _PASSIVE_MODES: frozenset[str] = frozenset({"passive_join", "passive_only", "passive_aggressive"})
    _HIGH_SUBMIT_RATIO = 0.50
    _HIGH_CANCEL_RATIO = 0.40

    def run(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None = None,
    ) -> list[LeakageLintIssue]:
        env = dict(backtest_environment or {})
        latency = dict(env.get("latency") or {})

        tick_ms = self._to_float(env.get("canonical_tick_interval_ms"))
        submit_ms = self._to_float(latency.get("order_submit_ms"))
        cancel_ms = self._to_float(latency.get("cancel_ms"))

        horizon_ticks = self._infer_holding_horizon(spec)
        horizon_ms = (horizon_ticks * tick_ms) if (horizon_ticks is not None and tick_ms is not None) else None
        short_horizon = (
            (horizon_ticks is not None and horizon_ticks <= self._SHORT_HORIZON_TICKS)
            or (horizon_ms is not None and horizon_ms <= self._SHORT_HORIZON_MS)
        )

        xp = spec.execution_policy
        issues: list[LeakageLintIssue] = []

        if xp is None:
            if short_horizon:
                issues.append(
                    LeakageLintIssue(
                        code="LATENCY_FEASIBILITY_MISSING_EP_SHORT_HORIZON",
                        severity="error",
                        message="short-horizon strategy has no execution policy; latency-aware execution feasibility is unclear.",
                        details={
                            "holding_horizon_ticks": horizon_ticks,
                            "holding_horizon_ms": horizon_ms,
                        },
                    )
                )
            elif horizon_ticks is not None:
                issues.append(
                    LeakageLintIssue(
                        code="LATENCY_FEASIBILITY_MISSING_EP",
                        severity="warning",
                        message="time-bounded strategy has no execution policy; feasibility under latency is ambiguous.",
                        details={"holding_horizon_ticks": horizon_ticks},
                    )
                )
            return issues

        is_passive = xp.placement_mode in self._PASSIVE_MODES
        if short_horizon and is_passive and xp.cancel_after_ticks <= 1:
            issues.append(
                LeakageLintIssue(
                    code="LATENCY_FEASIBILITY_TINY_CANCEL_HORIZON",
                    severity="error",
                    message="short-horizon passive strategy uses cancel_after_ticks<=1; order lifecycle is likely infeasible.",
                    details={"cancel_after_ticks": xp.cancel_after_ticks},
                )
            )

        if short_horizon and is_passive and xp.max_reprices >= 4:
            issues.append(
                LeakageLintIssue(
                    code="LATENCY_FEASIBILITY_PASSIVE_REPRICE_BURST",
                    severity="warning",
                    message="short-horizon passive strategy has high repricing budget; churn risk may dominate realizable edge.",
                    details={"max_reprices": xp.max_reprices},
                )
            )

        if tick_ms is not None and tick_ms > 0:
            submit_ratio = (submit_ms / tick_ms) if submit_ms is not None else None
            cancel_ratio = (cancel_ms / tick_ms) if cancel_ms is not None else None
            cancel_horizon_ms = xp.cancel_after_ticks * tick_ms

            if (
                is_passive
                and submit_ms is not None
                and cancel_ms is not None
                and cancel_horizon_ms < (submit_ms + cancel_ms)
            ):
                issues.append(
                    LeakageLintIssue(
                        code="LATENCY_FEASIBILITY_CANCEL_BELOW_ROUNDTRIP",
                        severity="error",
                        message="cancel horizon is shorter than submit+cancel roundtrip latency; passive lifecycle is not realistic.",
                        details={
                            "cancel_horizon_ms": cancel_horizon_ms,
                            "submit_ms": submit_ms,
                            "cancel_ms": cancel_ms,
                        },
                    )
                )

            if (
                short_horizon
                and is_passive
                and (
                    (submit_ratio is not None and submit_ratio >= self._HIGH_SUBMIT_RATIO)
                    or (cancel_ratio is not None and cancel_ratio >= self._HIGH_CANCEL_RATIO)
                )
                and xp.max_reprices >= 2
            ):
                issues.append(
                    LeakageLintIssue(
                        code="LATENCY_FEASIBILITY_LATENCY_TICK_RATIO_MISMATCH",
                        severity="error",
                        message="latency/tick ratio is high for short-horizon passive repricing strategy.",
                        details={
                            "submit_to_tick_ratio": submit_ratio,
                            "cancel_to_tick_ratio": cancel_ratio,
                            "max_reprices": xp.max_reprices,
                        },
                    )
                )

        return issues

    def _to_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            result = float(value)
            return result
        except (TypeError, ValueError):
            return None

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
