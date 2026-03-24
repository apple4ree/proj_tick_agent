"""V2 strategy templates.

Each template is a plain dict describing a strategy in an intermediate
representation that ``lowering.py`` converts into a full StrategySpecV2.

Phase 1 templates:
- imbalance_persist_momentum
- spread_absorption_reversal
- cross_momentum

Phase 2 templates:
- regime_filtered_persist_momentum
- rolling_mean_reversion
- adaptive_execution_imbalance

Phase 3 templates:
- stateful_cooldown_momentum
- loss_streak_degraded_reversion
- latency_adaptive_passive_entry
- position_aware_time_exit_momentum
- pnl_stop_degraded_scalper
- regime_adaptive_passive_reentry_block
"""
from __future__ import annotations

from typing import Any


def _imbalance_persist_momentum() -> dict[str, Any]:
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
                    },
                    {
                        "name": "time_exit",
                        "priority": 3,
                        "condition": {"feature": "spread_bps", "op": ">", "threshold": 25.0},
                        "action": "close_all",
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
                    {
                        "type": "rolling_comparison",
                        "rolling_feature": "order_imbalance",
                        "rolling_method": "mean",
                        "rolling_window": 10,
                        "op": ">",
                        "threshold": -0.1,
                    },
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
                    {
                        "type": "rolling_comparison",
                        "rolling_feature": "order_imbalance",
                        "rolling_method": "mean",
                        "rolling_window": 10,
                        "op": "<",
                        "threshold": 0.1,
                    },
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


# ── Phase 3 templates ────────────────────────────────────────────────

def _stateful_cooldown_momentum() -> dict[str, Any]:
    return {
        "name": "stateful_cooldown_momentum",
        "description": (
            "Momentum with explicit state_policy guards/events. "
            "Blocks entry after repeated losses until loss_streak resets."
        ),
        "preconditions": [
            {"name": "spread_ok", "feature": "spread_bps", "op": "<", "threshold": 25.0},
        ],
        "state_policy": {
            "vars": {
                "loss_streak": 0,
                "reentry_block": 0,
                "last_exit_was_loss": 0,
            },
            "guards": [
                {
                    "name": "loss_streak_guard",
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "loss_streak"},
                        "op": ">=",
                        "threshold": 3.0,
                    },
                    "effect": "block_entry",
                },
            ],
            "events": [
                {
                    "name": "on_entry_mark",
                    "on": "on_entry",
                    "updates": [
                        {"var": "reentry_block", "op": "set", "value": 1},
                    ],
                },
                {
                    "name": "on_loss",
                    "on": "on_exit_loss",
                    "updates": [
                        {"var": "loss_streak", "op": "increment", "value": 1},
                        {"var": "last_exit_was_loss", "op": "set", "value": 1},
                    ],
                },
                {
                    "name": "on_profit",
                    "on": "on_exit_profit",
                    "updates": [
                        {"var": "loss_streak", "op": "reset"},
                        {"var": "last_exit_was_loss", "op": "set", "value": 0},
                    ],
                },
                {
                    "name": "on_flat",
                    "on": "on_flatten",
                    "updates": [
                        {"var": "reentry_block", "op": "reset"},
                    ],
                },
            ],
        },
        "entries": [
            {
                "name": "state_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.32},
                    {"feature": "depth_imbalance", "op": ">", "threshold": 0.1},
                ],
                "strength_value": 0.55,
                "cooldown_ticks": 40,
            },
            {
                "name": "state_short",
                "side": "short",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": "<", "threshold": -0.32},
                    {"feature": "depth_imbalance", "op": "<", "threshold": -0.1},
                ],
                "strength_value": 0.55,
                "cooldown_ticks": 40,
            },
        ],
        "exits": [
            {
                "name": "state_exits",
                "rules": [
                    {
                        "name": "momentum_break",
                        "priority": 1,
                        "condition": {"feature": "order_imbalance", "op": "<", "threshold": -0.1},
                        "action": "close_all",
                    },
                    {
                        "name": "wide_spread_exit",
                        "priority": 2,
                        "condition": {"feature": "spread_bps", "op": ">", "threshold": 22.0},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 350,
            "inventory_cap": 700,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 350,
        },
    }


def _loss_streak_degraded_reversion() -> dict[str, Any]:
    return {
        "name": "loss_streak_degraded_reversion",
        "description": (
            "Mean-reversion with risk degradation driven by state_var loss_streak. "
            "Scales strength/size after losses and blocks new entries in deep drawdown."
        ),
        "preconditions": [
            {"name": "spread_ok", "feature": "spread_bps", "op": "<", "threshold": 30.0},
        ],
        "state_policy": {
            "vars": {
                "loss_streak": 0,
            },
            "guards": [],
            "events": [
                {
                    "name": "loss_counter",
                    "on": "on_exit_loss",
                    "updates": [
                        {"var": "loss_streak", "op": "increment", "value": 1},
                    ],
                },
                {
                    "name": "profit_reset",
                    "on": "on_exit_profit",
                    "updates": [
                        {"var": "loss_streak", "op": "reset"},
                    ],
                },
            ],
        },
        "entries": [
            {
                "name": "revert_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": "<", "threshold": -0.35},
                    {"feature": "spread_bps", "op": ">", "threshold": 5.0},
                ],
                "strength_value": 0.65,
                "cooldown_ticks": 30,
            },
        ],
        "exits": [
            {
                "name": "degraded_exits",
                "rules": [
                    {
                        "name": "reversion_done",
                        "priority": 1,
                        "condition": {"feature": "order_imbalance", "op": ">", "threshold": -0.05},
                        "action": "close_all",
                    },
                    {
                        "name": "vol_spike_stop",
                        "priority": 2,
                        "condition": {"feature": "spread_bps", "op": ">", "threshold": 35.0},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 450,
            "inventory_cap": 900,
            "sizing_mode": "signal_proportional",
            "base_size": 100,
            "max_size": 450,
            "degradation_rules": [
                {
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "loss_streak"},
                        "op": ">=",
                        "threshold": 2.0,
                    },
                    "action": {
                        "type": "scale_strength",
                        "factor": 0.6,
                    },
                },
                {
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "loss_streak"},
                        "op": ">=",
                        "threshold": 3.0,
                    },
                    "action": {
                        "type": "scale_max_position",
                        "factor": 0.5,
                    },
                },
                {
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "loss_streak"},
                        "op": ">=",
                        "threshold": 4.0,
                    },
                    "action": {
                        "type": "block_new_entries",
                    },
                },
            ],
        },
    }


def _latency_adaptive_passive_entry() -> dict[str, Any]:
    return {
        "name": "latency_adaptive_passive_entry",
        "description": (
            "Execution-adaptive entry hints: passive under wider spreads, "
            "aggressive when imbalance is extreme."
        ),
        "preconditions": [
            {"name": "spread_valid", "feature": "spread_bps", "op": ">", "threshold": 0.0},
        ],
        "entries": [
            {
                "name": "adaptive_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.28},
                    {"feature": "depth_imbalance", "op": ">", "threshold": 0.08},
                ],
                "strength_value": 0.58,
                "cooldown_ticks": 20,
            },
        ],
        "exits": [
            {
                "name": "adaptive_passive_exits",
                "rules": [
                    {
                        "name": "imbalance_reverse",
                        "priority": 1,
                        "condition": {"feature": "order_imbalance", "op": "<", "threshold": -0.08},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "execution_policy": {
            "placement_mode": "adaptive",
            "cancel_after_ticks": 12,
            "max_reprices": 2,
            "adaptation_rules": [
                {
                    "condition": {"feature": "spread_bps", "op": ">", "threshold": 18.0},
                    "override": {
                        "placement_mode": "passive_only",
                        "cancel_after_ticks": 5,
                        "max_reprices": 1,
                    },
                },
                {
                    "condition": {"feature": "order_imbalance", "op": ">", "threshold": 0.6},
                    "override": {
                        "placement_mode": "aggressive_cross",
                        "cancel_after_ticks": 1,
                        "max_reprices": 0,
                    },
                },
            ],
        },
        "risk": {
            "max_position": 380,
            "inventory_cap": 760,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 380,
        },
    }



def _position_aware_time_exit_momentum() -> dict[str, Any]:
    return {
        "name": "position_aware_time_exit_momentum",
        "description": (
            "Momentum entry with position_attr exits using holding_ticks and unrealized_pnl_bps."
        ),
        "preconditions": [
            {"name": "spread_ok", "feature": "spread_bps", "op": "<", "threshold": 25.0},
        ],
        "entries": [
            {
                "name": "mom_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.3},
                    {"feature": "depth_imbalance", "op": ">", "threshold": 0.1},
                ],
                "strength_value": 0.55,
                "cooldown_ticks": 30,
            }
        ],
        "exits": [
            {
                "name": "position_attr_exits",
                "rules": [
                    {
                        "name": "time_exit",
                        "priority": 1,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "holding_ticks"},
                            "op": ">=",
                            "threshold": 20.0,
                        },
                        "action": "close_all",
                    },
                    {
                        "name": "pnl_stop",
                        "priority": 2,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "unrealized_pnl_bps"},
                            "op": "<=",
                            "threshold": -30.0,
                        },
                        "action": "close_all",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 350,
            "inventory_cap": 700,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 350,
        },
    }


def _pnl_stop_degraded_scalper() -> dict[str, Any]:
    return {
        "name": "pnl_stop_degraded_scalper",
        "description": (
            "Scalper with state driven degradation and position_attr pnl stop exits."
        ),
        "state_policy": {
            "vars": {"loss_streak": 0},
            "guards": [
                {
                    "name": "loss_block",
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "loss_streak"},
                        "op": ">=",
                        "threshold": 4.0,
                    },
                    "effect": "block_entry",
                },
            ],
            "events": [
                {
                    "name": "track_loss",
                    "on": "on_exit_loss",
                    "updates": [{"var": "loss_streak", "op": "increment", "value": 1.0}],
                },
                {
                    "name": "reset_win",
                    "on": "on_exit_profit",
                    "updates": [{"var": "loss_streak", "op": "reset"}],
                },
            ],
        },
        "entries": [
            {
                "name": "scalp_revert_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": "<", "threshold": -0.32},
                    {"feature": "spread_bps", "op": ">", "threshold": 4.0},
                ],
                "strength_value": 0.62,
                "cooldown_ticks": 20,
            },
        ],
        "exits": [
            {
                "name": "scalper_exits",
                "rules": [
                    {
                        "name": "hard_stop",
                        "priority": 1,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "unrealized_pnl_bps"},
                            "op": "<=",
                            "threshold": -25.0,
                        },
                        "action": "close_all",
                    },
                    {
                        "name": "hold_limit",
                        "priority": 2,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "holding_ticks"},
                            "op": ">=",
                            "threshold": 15.0,
                        },
                        "action": "close_all",
                    },
                ],
            },
        ],
        "risk": {
            "max_position": 300,
            "inventory_cap": 600,
            "sizing_mode": "signal_proportional",
            "base_size": 100,
            "max_size": 300,
            "degradation_rules": [
                {
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "loss_streak"},
                        "op": ">=",
                        "threshold": 2.0,
                    },
                    "action": {"type": "scale_strength", "factor": 0.7},
                },
                {
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "loss_streak"},
                        "op": ">=",
                        "threshold": 3.0,
                    },
                    "action": {"type": "scale_max_position", "factor": 0.6},
                },
            ],
        },
    }


def _regime_adaptive_passive_reentry_block() -> dict[str, Any]:
    return {
        "name": "regime_adaptive_passive_reentry_block",
        "description": (
            "Regime routed entries with state reentry block and execution adaptation overrides."
        ),
        "state_policy": {
            "vars": {"reentry_block": 0},
            "guards": [
                {
                    "name": "reentry_guard",
                    "condition": {
                        "type": "comparison",
                        "left": {"type": "state_var", "name": "reentry_block"},
                        "op": ">",
                        "threshold": 0.0,
                    },
                    "effect": "block_entry",
                },
            ],
            "events": [
                {
                    "name": "mark_entry",
                    "on": "on_entry",
                    "updates": [{"var": "reentry_block", "op": "set", "value": 1.0}],
                },
                {
                    "name": "clear_flat",
                    "on": "on_flatten",
                    "updates": [{"var": "reentry_block", "op": "reset"}],
                },
            ],
        },
        "entries": [
            {
                "name": "trend_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": ">", "threshold": 0.28},
                    {"feature": "depth_imbalance", "op": ">", "threshold": 0.08},
                ],
                "strength_value": 0.56,
                "cooldown_ticks": 25,
            },
            {
                "name": "chop_long",
                "side": "long",
                "trigger_type": "all",
                "conditions": [
                    {"feature": "order_imbalance", "op": "<", "threshold": -0.25},
                    {"feature": "spread_bps", "op": ">", "threshold": 6.0},
                ],
                "strength_value": 0.45,
                "cooldown_ticks": 25,
            },
        ],
        "exits": [
            {
                "name": "regime_exits",
                "rules": [
                    {
                        "name": "time_exit",
                        "priority": 1,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "holding_ticks"},
                            "op": ">=",
                            "threshold": 18.0,
                        },
                        "action": "close_all",
                    },
                    {
                        "name": "reversal",
                        "priority": 2,
                        "condition": {"feature": "order_imbalance", "op": "<", "threshold": -0.1},
                        "action": "close_all",
                    },
                ],
            },
        ],
        "regimes": [
            {
                "name": "tight_spread_trend",
                "priority": 1,
                "when": {"feature": "spread_bps", "op": "<", "threshold": 12.0},
                "entry_policy_refs": ["trend_long"],
                "exit_policy_ref": "regime_exits",
            },
            {
                "name": "wide_spread_chop",
                "priority": 2,
                "when": {"feature": "spread_bps", "op": ">=", "threshold": 12.0},
                "entry_policy_refs": ["chop_long"],
                "exit_policy_ref": "regime_exits",
            },
        ],
        "execution_policy": {
            "placement_mode": "adaptive",
            "cancel_after_ticks": 10,
            "max_reprices": 2,
            "adaptation_rules": [
                {
                    "condition": {"feature": "spread_bps", "op": ">", "threshold": 18.0},
                    "override": {
                        "placement_mode": "passive_only",
                        "cancel_after_ticks": 4,
                        "max_reprices": 1,
                    },
                },
            ],
        },
        "risk": {
            "max_position": 320,
            "inventory_cap": 640,
            "sizing_mode": "fixed",
            "base_size": 100,
            "max_size": 320,
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
    "stateful_cooldown_momentum": _stateful_cooldown_momentum,
    "loss_streak_degraded_reversion": _loss_streak_degraded_reversion,
    "latency_adaptive_passive_entry": _latency_adaptive_passive_entry,
    "position_aware_time_exit_momentum": _position_aware_time_exit_momentum,
    "pnl_stop_degraded_scalper": _pnl_stop_degraded_scalper,
    "regime_adaptive_passive_reentry_block": _regime_adaptive_passive_reentry_block,
}


def get_v2_template(name: str) -> dict[str, Any]:
    factory = V2_TEMPLATES.get(name)
    if factory is None:
        raise KeyError(
            f"Unknown v2 template: {name!r}. "
            f"Available: {sorted(V2_TEMPLATES.keys())}"
        )
    return factory()
