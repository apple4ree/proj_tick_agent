"""Expression AST nodes for StrategySpec v2.

All expressions serialize to/from plain dicts with a ``type`` discriminator
field, keeping the spec format JSON-friendly and declarative.

Supported node types:
- ``const``       — literal numeric value
- ``feature``     — runtime feature lookup
- ``comparison``  — binary comparison (feature op threshold)
- ``all``         — logical AND over children
- ``any``         — logical OR over children
- ``not``         — logical negation
- ``cross``       — cross_above / cross_below (requires previous tick state)
- ``lag``         — value of a feature N ticks ago (Phase 2)
- ``rolling``     — rolling aggregation over a window (Phase 2)
- ``persist``     — boolean condition held true for min_true out of window ticks (Phase 2)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Allowed comparison operators
VALID_COMPARISON_OPS: frozenset[str] = frozenset({
    ">", "<", ">=", "<=", "==",
})

# Allowed cross directions
VALID_CROSS_DIRECTIONS: frozenset[str] = frozenset({
    "above", "below",
})

# Allowed rolling methods
VALID_ROLLING_METHODS: frozenset[str] = frozenset({
    "mean", "min", "max",
})

# Known node type tags
VALID_NODE_TYPES: frozenset[str] = frozenset({
    "const", "feature", "comparison", "all", "any", "not", "cross",
    "lag", "rolling", "persist",
})


# ── Base ──────────────────────────────────────────────────────────────

@dataclass
class ExprNode:
    """Abstract base for all AST nodes."""
    type: str

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def collect_features(self) -> set[str]:
        """Return all feature names referenced by this expression tree."""
        return set()


# ── Value nodes ───────────────────────────────────────────────────────

@dataclass
class ConstExpr(ExprNode):
    """Literal constant value."""
    value: float = 0.0

    def __init__(self, value: float = 0.0):
        super().__init__(type="const")
        self.value = value

    def to_dict(self) -> dict[str, Any]:
        return {"type": "const", "value": self.value}

    def collect_features(self) -> set[str]:
        return set()


@dataclass
class FeatureExpr(ExprNode):
    """Runtime feature lookup."""
    name: str = ""

    def __init__(self, name: str = ""):
        super().__init__(type="feature")
        self.name = name

    def to_dict(self) -> dict[str, Any]:
        return {"type": "feature", "name": self.name}

    def collect_features(self) -> set[str]:
        return {self.name}


# ── Condition nodes ───────────────────────────────────────────────────

@dataclass
class ComparisonExpr(ExprNode):
    """Binary comparison: ``feature op threshold``."""
    feature: str = ""
    op: str = ">"
    threshold: float = 0.0

    def __init__(self, feature: str = "", op: str = ">", threshold: float = 0.0):
        super().__init__(type="comparison")
        self.feature = feature
        self.op = op
        self.threshold = threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "comparison",
            "feature": self.feature,
            "op": self.op,
            "threshold": self.threshold,
        }

    def collect_features(self) -> set[str]:
        return {self.feature}


@dataclass
class AllExpr(ExprNode):
    """Logical AND: all children must be true."""
    children: list[ExprNode] = field(default_factory=list)

    def __init__(self, children: list[ExprNode] | None = None):
        super().__init__(type="all")
        self.children = children or []

    def to_dict(self) -> dict[str, Any]:
        return {"type": "all", "children": [c.to_dict() for c in self.children]}

    def collect_features(self) -> set[str]:
        result: set[str] = set()
        for c in self.children:
            result |= c.collect_features()
        return result


@dataclass
class AnyExpr(ExprNode):
    """Logical OR: at least one child must be true."""
    children: list[ExprNode] = field(default_factory=list)

    def __init__(self, children: list[ExprNode] | None = None):
        super().__init__(type="any")
        self.children = children or []

    def to_dict(self) -> dict[str, Any]:
        return {"type": "any", "children": [c.to_dict() for c in self.children]}

    def collect_features(self) -> set[str]:
        result: set[str] = set()
        for c in self.children:
            result |= c.collect_features()
        return result


@dataclass
class NotExpr(ExprNode):
    """Logical negation."""
    child: ExprNode = field(default_factory=lambda: ConstExpr(0.0))

    def __init__(self, child: ExprNode | None = None):
        super().__init__(type="not")
        self.child = child or ConstExpr(0.0)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "not", "child": self.child.to_dict()}

    def collect_features(self) -> set[str]:
        return self.child.collect_features()


@dataclass
class CrossExpr(ExprNode):
    """Cross above/below: feature crosses a threshold between ticks.

    ``direction="above"`` means prev <= threshold < current.
    ``direction="below"`` means prev >= threshold > current.
    """
    feature: str = ""
    threshold: float = 0.0
    direction: str = "above"  # "above" | "below"

    def __init__(self, feature: str = "", threshold: float = 0.0,
                 direction: str = "above"):
        super().__init__(type="cross")
        self.feature = feature
        self.threshold = threshold
        self.direction = direction

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "cross",
            "feature": self.feature,
            "threshold": self.threshold,
            "direction": self.direction,
        }

    def collect_features(self) -> set[str]:
        return {self.feature}


# ── Phase 2 nodes ─────────────────────────────────────────────────────

@dataclass
class LagExpr(ExprNode):
    """Value of a feature N ticks ago.

    Evaluates as a float — typically used inside ComparisonExpr via
    nesting or for signal strength computation.
    """
    feature: str = ""
    steps: int = 1

    def __init__(self, feature: str = "", steps: int = 1):
        super().__init__(type="lag")
        self.feature = feature
        self.steps = steps

    def to_dict(self) -> dict[str, Any]:
        return {"type": "lag", "feature": self.feature, "steps": self.steps}

    def collect_features(self) -> set[str]:
        return {self.feature}


@dataclass
class RollingExpr(ExprNode):
    """Rolling aggregation over a feature window.

    Supported methods: mean, min, max.
    Evaluates as a float.
    """
    feature: str = ""
    method: str = "mean"
    window: int = 5

    def __init__(self, feature: str = "", method: str = "mean", window: int = 5):
        super().__init__(type="rolling")
        self.feature = feature
        self.method = method
        self.window = window

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "rolling",
            "feature": self.feature,
            "method": self.method,
            "window": self.window,
        }

    def collect_features(self) -> set[str]:
        return {self.feature}


@dataclass
class PersistExpr(ExprNode):
    """Boolean condition held true for min_true out of the last window ticks.

    ``expr`` is a child boolean expression that is evaluated each tick
    and tracked in a history buffer.  PersistExpr evaluates to True
    when at least ``min_true`` of the last ``window`` evaluations were True.
    """
    expr: ExprNode = field(default_factory=lambda: ConstExpr(1.0))
    window: int = 5
    min_true: int = 3

    def __init__(self, expr: ExprNode | None = None,
                 window: int = 5, min_true: int = 3):
        super().__init__(type="persist")
        self.expr = expr or ConstExpr(1.0)
        self.window = window
        self.min_true = min_true

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "persist",
            "expr": self.expr.to_dict(),
            "window": self.window,
            "min_true": self.min_true,
        }

    def collect_features(self) -> set[str]:
        return self.expr.collect_features()


# ── Deserialization ───────────────────────────────────────────────────

def expr_from_dict(d: dict[str, Any]) -> ExprNode:
    """Reconstruct an ExprNode tree from a plain dict."""
    node_type = d.get("type", "")
    if node_type == "const":
        return ConstExpr(value=d["value"])
    elif node_type == "feature":
        return FeatureExpr(name=d["name"])
    elif node_type == "comparison":
        return ComparisonExpr(feature=d["feature"], op=d["op"],
                              threshold=d["threshold"])
    elif node_type == "all":
        return AllExpr(children=[expr_from_dict(c) for c in d["children"]])
    elif node_type == "any":
        return AnyExpr(children=[expr_from_dict(c) for c in d["children"]])
    elif node_type == "not":
        return NotExpr(child=expr_from_dict(d["child"]))
    elif node_type == "cross":
        return CrossExpr(feature=d["feature"], threshold=d["threshold"],
                         direction=d["direction"])
    elif node_type == "lag":
        return LagExpr(feature=d["feature"], steps=d["steps"])
    elif node_type == "rolling":
        return RollingExpr(feature=d["feature"], method=d["method"],
                           window=d["window"])
    elif node_type == "persist":
        return PersistExpr(expr=expr_from_dict(d["expr"]),
                           window=d["window"], min_true=d["min_true"])
    else:
        raise ValueError(f"Unknown expression node type: {node_type!r}")
