"""V2 runtime state and AST evaluator.

Provides the evaluation engine that the compiled v2 strategy uses
to evaluate expression trees against live market state features.

Phase 2 adds:
- Feature history buffer for lag / rolling evaluation
- Boolean condition history for persist evaluation
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from strategy_block.strategy_specs.v2.ast_nodes import (
    ExprNode,
    ConstExpr,
    FeatureExpr,
    ComparisonExpr,
    AllExpr,
    AnyExpr,
    NotExpr,
    CrossExpr,
    LagExpr,
    RollingExpr,
    PersistExpr,
)

# Maximum history depth to prevent unbounded growth
MAX_HISTORY_DEPTH = 500


@dataclass
class RuntimeStateV2:
    """Per-symbol runtime state for v2 strategy execution."""
    tick_count: int = 0
    position_side: str = ""  # "" | "long" | "short"
    position_size: float = 0.0
    entry_tick: int = -1
    entry_price: float = 0.0
    cooldown_until: int = 0  # tick number when cooldown expires
    prev_features: dict[str, float] = field(default_factory=dict)
    trailing_high: float = 0.0
    trailing_low: float = float("inf")

    # Phase 2: feature history for lag/rolling
    feature_history: deque[dict[str, float]] = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY_DEPTH)
    )

    # Phase 2: persist condition tracking
    # Key is a unique persist node ID, value is deque of booleans
    persist_history: dict[str, deque[bool]] = field(default_factory=dict)

    def record_features(self, features: dict[str, float]) -> None:
        """Append current features to history buffer."""
        self.feature_history.append(dict(features))

    def get_lag_value(self, feature: str, steps: int) -> float:
        """Get the value of a feature N ticks ago.

        The last entry in feature_history is the *current* tick
        (recorded before evaluation), so "1 tick ago" is at index -2.
        """
        idx = len(self.feature_history) - 1 - steps
        if idx < 0 or idx >= len(self.feature_history):
            return 0.0
        return self.feature_history[idx].get(feature, 0.0)

    def get_rolling(self, feature: str, window: int, method: str) -> float:
        """Compute rolling aggregation over the last *window* ticks."""
        n = len(self.feature_history)
        start = max(0, n - window)
        values = [self.feature_history[i].get(feature, 0.0) for i in range(start, n)]
        if not values:
            return 0.0
        if method == "mean":
            return sum(values) / len(values)
        elif method == "min":
            return min(values)
        elif method == "max":
            return max(values)
        return 0.0

    def record_persist(self, node_id: str, value: bool, window: int) -> None:
        """Record a persist condition evaluation result."""
        if node_id not in self.persist_history:
            self.persist_history[node_id] = deque(maxlen=window)
        self.persist_history[node_id].append(value)

    def get_persist_count(self, node_id: str) -> int:
        """Count how many True values are in the persist history."""
        hist = self.persist_history.get(node_id)
        if hist is None:
            return 0
        return sum(1 for v in hist if v)


def _persist_node_id(node: PersistExpr) -> str:
    """Generate a stable identifier for a PersistExpr node."""
    # Use the serialized form as a stable key
    return f"persist_{id(node)}"


def evaluate_bool(node: ExprNode, features: dict[str, float],
                  prev_features: dict[str, float],
                  runtime: RuntimeStateV2 | None = None) -> bool:
    """Evaluate an expression node as a boolean condition."""
    if isinstance(node, ConstExpr):
        return node.value != 0.0

    elif isinstance(node, FeatureExpr):
        val = features.get(node.name, 0.0)
        return val != 0.0

    elif isinstance(node, ComparisonExpr):
        val = features.get(node.feature, 0.0)
        thr = node.threshold
        if node.op == ">":
            return val > thr
        elif node.op == "<":
            return val < thr
        elif node.op == ">=":
            return val >= thr
        elif node.op == "<=":
            return val <= thr
        elif node.op == "==":
            return abs(val - thr) < 1e-9
        return False

    elif isinstance(node, AllExpr):
        return all(evaluate_bool(c, features, prev_features, runtime) for c in node.children)

    elif isinstance(node, AnyExpr):
        return any(evaluate_bool(c, features, prev_features, runtime) for c in node.children)

    elif isinstance(node, NotExpr):
        return not evaluate_bool(node.child, features, prev_features, runtime)

    elif isinstance(node, CrossExpr):
        cur = features.get(node.feature, 0.0)
        prev = prev_features.get(node.feature, cur)
        if node.direction == "above":
            return prev <= node.threshold < cur
        elif node.direction == "below":
            return prev >= node.threshold > cur
        return False

    elif isinstance(node, LagExpr):
        if runtime is None:
            return False
        val = runtime.get_lag_value(node.feature, node.steps)
        return val != 0.0

    elif isinstance(node, RollingExpr):
        if runtime is None:
            return False
        val = runtime.get_rolling(node.feature, node.window, node.method)
        return val != 0.0

    elif isinstance(node, PersistExpr):
        if runtime is None:
            return False
        # Evaluate the inner expression and record it
        inner_result = evaluate_bool(node.expr, features, prev_features, runtime)
        node_id = _persist_node_id(node)
        runtime.record_persist(node_id, inner_result, node.window)
        true_count = runtime.get_persist_count(node_id)
        return true_count >= node.min_true

    return False


def evaluate_float(node: ExprNode, features: dict[str, float],
                   runtime: RuntimeStateV2 | None = None) -> float:
    """Evaluate an expression node as a float value (for strength)."""
    if isinstance(node, ConstExpr):
        return node.value
    elif isinstance(node, FeatureExpr):
        return features.get(node.name, 0.0)
    elif isinstance(node, LagExpr):
        if runtime is None:
            return 0.0
        return runtime.get_lag_value(node.feature, node.steps)
    elif isinstance(node, RollingExpr):
        if runtime is None:
            return 0.0
        return runtime.get_rolling(node.feature, node.window, node.method)
    return 0.0
