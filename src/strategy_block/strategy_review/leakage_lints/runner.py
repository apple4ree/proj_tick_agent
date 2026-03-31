from __future__ import annotations

from typing import Any

from strategy_block.strategy_review.leakage_lints.feature_time_guard import FeatureTimeGuard
from strategy_block.strategy_review.leakage_lints.fill_alignment_guard import FillAlignmentGuard
from strategy_block.strategy_review.leakage_lints.latency_feasibility_guard import LatencyFeasibilityGuard
from strategy_block.strategy_review.leakage_lints.lookahead_guard import LookaheadGuard
from strategy_block.strategy_review.leakage_lints.models import LeakageLintIssue, LeakageLintResult
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2


class LeakageLintRunner:
    """Run all leakage/liveness guards in deterministic order."""

    def __init__(self, guards: list[Any] | None = None) -> None:
        self._guards = guards or [
            FeatureTimeGuard(),
            LookaheadGuard(),
            FillAlignmentGuard(),
            LatencyFeasibilityGuard(),
        ]

    def run(
        self,
        spec: StrategySpecV2,
        backtest_environment: dict[str, Any] | None = None,
    ) -> LeakageLintResult:
        merged: list[LeakageLintIssue] = []
        for guard in self._guards:
            merged.extend(guard.run(spec, backtest_environment=backtest_environment))
        return LeakageLintResult(issues=merged)
