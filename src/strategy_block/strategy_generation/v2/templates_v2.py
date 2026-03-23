"""V2 strategy templates.

Each template is a plain dict describing a strategy in an intermediate
representation that ``lowering.py`` converts into a full StrategySpecV2.

Phase 1 templates:
- imbalance_persist_momentum
- spread_absorption_reversal
- cross_momentum

Phase 2 templates:
- regime_filtered_persist_momentum (uses regimes + persist)
- rolling_mean_reversion (uses rolling)
- adaptive_execution_imbalance (uses execution_policy)
"""
from __future__ import annotations

from typing import Any


def _imbalance_persist_momentum() -> dict[str, Any]:
    """Order-book imbalance persistence momentum strategy.

    Entry long when order imbalance is strongly positive AND depth imbalance
    confirms. Entry short on the mirror condition. Exit on stop loss,
    take profit (spread crossing), or time-based exit.
    """
    return {
        "name": "imbalance_persist_momentum",
        "description": (
            "Enter when order imbalance persists above a threshold, "
            "confirmed by depth imbalance. Exit on reversal or time."
        ),
        "preconditions": [
            {"name": "spread_ok", "feature": "spread_bps", "op": "<", "threshold": 30.0},
        ],
        "entries": [
            {
                "name": "long_imbalance",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.3},
                    {"feature": "depth_imbalance", "op": ">", "threshold": 0.15},
                ],
                "strength_value": 0.6,
                "cooldown_ticks": 50,
                "no_reentry_until_flat": True,
            },
            {
                "name": "short_imbalance",
                "side": "short",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": "<", "threshold": -0.3},
                    {"feature": "depth_imbalance", "op": "<", "threshold": -0.15},
                ],
                "strength_value": 0.6,
                "cooldown_ticks": 50,
                "no_reentry_until_flat": True,
            },
        ],
        "exits": [
            {
                "name": "risk_management",
                "rules": [
                    {
                        "name": "stop_loss",
                        "priority": 1,
                        "condition": {"feature": "order_imbalance", "op": "<", "threshold": -0.2},
                        "action": "close_all",
                        "description": "Close on imbalance reversal (long stop)",
                    },
                    {
                        "name": "time_exit",
                        "priority": 3,
                        "condition": {"feature": "spread_bps", "op": ">", "threshold": 25.0},
                        "action": "close_all",
                        "description": "Close when spread widens (liquidity drying up)",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 500,
            "inventory_cap": 1000,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 500,
        },
    }


def _spread_absorption_reversal() -> dict[str, Any]:
    """Spread absorption mean-reversion strategy.

    Enter contrarian when spread is wide (liquidity shock) and order
    imbalance is extreme. Exit on spread normalization or time.
    """
    return {
        "name": "spread_absorption_reversal",
        "description": (
            "Fade extreme imbalance when spread is wide — mean reversion "
            "on the assumption that the spread will compress."
        ),
        "preconditions": [],
        "entries": [
            {
                "name": "fade_sell_pressure",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "spread_bps", "op": ">", "threshold": 10.0},
                    {"feature": "order_imbalance", "op": "<", "threshold": -0.4},
                ],
                "strength_value": 0.5,
                "cooldown_ticks": 100,
            },
            {
                "name": "fade_buy_pressure",
                "side": "short",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "spread_bps", "op": ">", "threshold": 10.0},
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.4},
                ],
                "strength_value": 0.5,
                "cooldown_ticks": 100,
            },
        ],
        "exits": [
            {
                "name": "spread_normalized",
                "rules": [
                    {
                        "name": "spread_compress",
                        "priority": 1,
                        "condition": {"feature": "spread_bps", "op": "<", "threshold": 3.0},
                        "action": "close_all",
                    },
                    {
                        "name": "imbalance_flip",
                        "priority": 2,
                        "condition": {"feature": "order_imbalance", "op": ">", "threshold": 0.3},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 300,
            "inventory_cap": 600,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 300,
        },
    }


def _cross_momentum() -> dict[str, Any]:
    """Cross-based momentum strategy using cross_above/cross_below."""
    return {
        "name": "cross_momentum",
        "description": (
            "Enter when trade flow imbalance crosses above/below zero, "
            "indicating a shift in aggressive order flow direction."
        ),
        "preconditions": [
            {"name": "spread_ok", "feature": "spread_bps", "op": "<", "threshold": 20.0},
        ],
        "entries": [
            {
                "name": "flow_cross_up",
                "side": "long",
                "trigger_type": "cross",
                "cross_feature": "trade_flow_imbalance",
                "cross_threshold": 0.0,
                "cross_direction": "above",
                "strength_value": 0.7,
                "cooldown_ticks": 30,
            },
            {
                "name": "flow_cross_down",
                "side": "short",
                "trigger_type": "cross",
                "cross_feature": "trade_flow_imbalance",
                "cross_threshold": 0.0,
                "cross_direction": "below",
                "strength_value": 0.7,
                "cooldown_ticks": 30,
            },
        ],
        "exits": [
            {
                "name": "risk_exits",
                "rules": [
                    {
                        "name": "flow_reversal",
                        "priority": 1,
                        "condition": {"feature": "trade_flow_imbalance", "op": "<", "threshold": -0.2},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 400,
            "inventory_cap": 800,
            "sizing_mode": "signal_proportional",
            "base_size": 100,
            "max_size": 400,
        },
    }


# ── Phase 2 templates ────────────────────────────────────────────────

def _regime_filtered_persist_momentum() -> dict[str, Any]:
    """Regime-based momentum with persist confirmation.

    Two regimes:
    - trending: high imbalance persists for 3/5 ticks → aggressive entry
    - choppy: spread is wide → no entry (exits only)
    """
    return {
        "name": "regime_filtered_persist_momentum",
        "description": (
            "Regime-filtered momentum: enter only when trending regime is active "
            "and imbalance persists above threshold for 3 of 5 ticks."
        ),
        "preconditions": [],
        "entries": [
            {
                "name": "persist_long",
                "side": "long",
                "trigger_type": "persist",
                "persist_expr": {"feature": "order_imbalance", "op": ">", "threshold": 0.25},
                "persist_window": 5,
                "persist_min_true": 3,
                "strength_value": 0.7,
                "cooldown_ticks": 40,
                "no_reentry_until_flat": True,
            },
            {
                "name": "persist_short",
                "side": "short",
                "trigger_type": "persist",
                "persist_expr": {"feature": "order_imbalance", "op": "<", "threshold": -0.25},
                "persist_window": 5,
                "persist_min_true": 3,
                "strength_value": 0.7,
                "cooldown_ticks": 40,
                "no_reentry_until_flat": True,
            },
        ],
        "exits": [
            {
                "name": "momentum_exits",
                "rules": [
                    {
                        "name": "reversal_stop",
                        "priority": 1,
                        "condition": {"feature": "order_imbalance", "op": "<", "threshold": -0.15},
                        "action": "close_all",
                    },
                    {
                        "name": "spread_stop",
                        "priority": 2,
                        "condition": {"feature": "spread_bps", "op": ">", "threshold": 20.0},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "regimes": [
            {
                "name": "trending",
                "priority": 1,
                "when": {"feature": "spread_bps", "op": "<", "threshold": 15.0},
                "entry_policy_refs": ["persist_long", "persist_short"],
                "exit_policy_ref": "momentum_exits",
            },
            {
                "name": "choppy",
                "priority": 2,
                "when": {"feature": "spread_bps", "op": ">=", "threshold": 15.0},
                "entry_policy_refs": [],
                "exit_policy_ref": "momentum_exits",
            },
        ],
        "risk": {
            "max_position": 400,
            "inventory_cap": 800,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 400,
        },
    }


def _rolling_mean_reversion() -> dict[str, Any]:
    """Mean-reversion using rolling mean comparison.

    Enter long when current imbalance is significantly below its rolling mean.
    Uses rolling(mean, 10) internally.
    """
    return {
        "name": "rolling_mean_reversion",
        "description": (
            "Mean-revert when order imbalance deviates from its rolling average. "
            "Uses rolling mean over 10 ticks as baseline."
        ),
        "preconditions": [
            {"name": "spread_ok", "feature": "spread_bps", "op": "<", "threshold": 20.0},
        ],
        "entries": [
            {
                "name": "revert_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": "<", "threshold": -0.3},
                ],
                "extra_conditions": [
                    {"type": "rolling_comparison",
                     "rolling_feature": "order_imbalance",
                     "rolling_method": "mean",
                     "rolling_window": 10,
                     "op": ">",
                     "threshold": -0.1},
                ],
                "strength_value": 0.5,
                "cooldown_ticks": 60,
            },
            {
                "name": "revert_short",
                "side": "short",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.3},
                ],
                "extra_conditions": [
                    {"type": "rolling_comparison",
                     "rolling_feature": "order_imbalance",
                     "rolling_method": "mean",
                     "rolling_window": 10,
                     "op": "<",
                     "threshold": 0.1},
                ],
                "strength_value": 0.5,
                "cooldown_ticks": 60,
            },
        ],
        "exits": [
            {
                "name": "reversion_exits",
                "rules": [
                    {
                        "name": "revert_complete",
                        "priority": 1,
                        "condition": {"feature": "order_imbalance", "op": ">", "threshold": 0.0},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 300,
            "inventory_cap": 600,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 300,
        },
    }


def _adaptive_execution_imbalance() -> dict[str, Any]:
    """Imbalance strategy with adaptive execution policy.

    Uses execution_policy to signal downstream placement preferences.
    """
    return {
        "name": "adaptive_execution_imbalance",
        "description": (
            "Imbalance momentum with adaptive execution: passive when spread "
            "is tight, aggressive when imbalance is very strong."
        ),
        "preconditions": [
            {"name": "has_spread", "feature": "spread_bps", "op": ">", "threshold": 0.0},
        ],
        "entries": [
            {
                "name": "strong_imbalance_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.35},
                    {"feature": "depth_imbalance", "op": ">", "threshold": 0.1},
                ],
                "strength_value": 0.6,
                "cooldown_ticks": 30,
            },
        ],
        "exits": [
            {
                "name": "adaptive_exits",
                "rules": [
                    {
                        "name": "imbalance_stop",
                        "priority": 1,
                        "condition": {"feature": "order_imbalance", "op": "<", "threshold": -0.1},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "execution_policy": {
            "placement_mode": "adaptive",
            "cancel_after_ticks": 20,
            "max_reprices": 3,
            "do_not_trade_when": {"feature": "spread_bps", "op": ">", "threshold": 50.0},
        },
        "risk": {
            "max_position": 400,
            "inventory_cap": 800,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 400,
        },
    }


# Template registry
V2_TEMPLATES: dict[str, callable] = {
    "imbalance_persist_momentum": _imbalance_persist_momentum,
    "spread_absorption_reversal": _spread_absorption_reversal,
    "cross_momentum": _cross_momentum,
    "regime_filtered_persist_momentum": _regime_filtered_persist_momentum,
    "rolling_mean_reversion": _rolling_mean_reversion,
    "adaptive_execution_imbalance": _adaptive_execution_imbalance,
}


def get_v2_template(name: str) -> dict[str, Any]:
    """Get a v2 template by name."""
    factory = V2_TEMPLATES.get(name)
    if factory is None:
        raise KeyError(
            f"Unknown v2 template: {name!r}. "
            f"Available: {sorted(V2_TEMPLATES.keys())}"
        )
    return factory()
