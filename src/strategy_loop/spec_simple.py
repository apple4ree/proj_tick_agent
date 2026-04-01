"""
strategy_loop/spec_simple.py
-----------------------------
Simple JSON-native strategy spec format.

스펙 예시:
{
    "name": "order_imbalance_momentum",
    "entry": {
        "side": "long",
        "condition": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.15},
        "size": 10
    },
    "exit": {
        "condition": {
            "type": "any",
            "conditions": [
                {"type": "comparison", "left": {"type": "position_attr", "name": "holding_ticks"}, "op": ">=", "right": {"type": "const", "value": 5}},
                {"type": "comparison", "feature": "order_imbalance", "op": "<", "threshold": -0.05}
            ]
        }
    },
    "risk": {
        "max_position": 100
    }
}

BoolExpr 타입:
  comparison (shorthand) : {"type": "comparison", "feature": str, "op": str, "threshold": float}
  comparison (full)      : {"type": "comparison", "left": ValueExpr, "op": str, "right": ValueExpr}
  all                    : {"type": "all", "conditions": [BoolExpr, ...]}
  any                    : {"type": "any", "conditions": [BoolExpr, ...]}
  not                    : {"type": "not", "condition": BoolExpr}

ValueExpr 타입:
  feature       : {"type": "feature", "name": str}
  const         : {"type": "const", "value": float}
  position_attr : {"type": "position_attr", "name": str}   -- e.g. holding_ticks
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


VALID_OPS: frozenset[str] = frozenset({">", ">=", "<", "<=", "==", "!="})
VALID_SIDES: frozenset[str] = frozenset({"long", "short"})
VALID_POSITION_ATTRS: frozenset[str] = frozenset({"holding_ticks"})


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load a simple strategy spec from a JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _evaluate_value(v: dict, features: dict[str, float], position: dict[str, float]) -> float:
    t = v["type"]
    if t == "feature":
        return features.get(v["name"], 0.0)
    if t == "const":
        return float(v["value"])
    if t == "position_attr":
        return float(position.get(v["name"], 0.0))
    raise ValueError(f"Unknown ValueExpr type: {t!r}")


def _compare(left: float, op: str, right: float) -> bool:
    if op == ">":  return left > right
    if op == ">=": return left >= right
    if op == "<":  return left < right
    if op == "<=": return left <= right
    if op == "==": return left == right
    if op == "!=": return left != right
    raise ValueError(f"Unknown operator: {op!r}")


def evaluate(cond: dict, features: dict[str, float], position: dict[str, float]) -> bool:
    """Recursively evaluate a BoolExpr against market features and position state."""
    t = cond["type"]
    if t == "comparison":
        if "feature" in cond:
            left = features.get(cond["feature"], 0.0)
        else:
            left = _evaluate_value(cond["left"], features, position)
        if "threshold" in cond:
            right = float(cond["threshold"])
        else:
            right = _evaluate_value(cond["right"], features, position)
        return _compare(left, cond["op"], right)
    if t == "all":
        return all(evaluate(c, features, position) for c in cond["conditions"])
    if t == "any":
        return any(evaluate(c, features, position) for c in cond["conditions"])
    if t == "not":
        return not evaluate(cond["condition"], features, position)
    raise ValueError(f"Unknown BoolExpr type: {t!r}")
