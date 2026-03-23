"""Predefined strategy idea templates.

Each template is a dict that can be passed directly to StrategySpec.from_dict()
after merging with runtime parameters (latency_ms, metadata, etc.).
"""
from __future__ import annotations

from typing import Any

IdeaTemplate = dict[str, Any]

IDEA_TEMPLATES: list[IdeaTemplate] = [
    # ── 0: Imbalance Momentum ───────────────────────────────────────
    {
        "name": "imbalance_momentum",
        "description": (
            "Order-book imbalance predicts short-term price direction. "
            "Buy when bid-heavy, sell when ask-heavy. "
            "Depth imbalance acts as confirming signal."
        ),
        "signal_rules": [
            {
                "feature": "order_imbalance",
                "operator": ">",
                "threshold": 0.3,
                "score_contribution": 0.5,
                "description": "Bid-heavy imbalance → bullish",
            },
            {
                "feature": "order_imbalance",
                "operator": "<",
                "threshold": -0.3,
                "score_contribution": -0.5,
                "description": "Ask-heavy imbalance → bearish",
            },
            {
                "feature": "depth_imbalance",
                "operator": ">",
                "threshold": 0.2,
                "score_contribution": 0.3,
                "description": "Deep bid side confirms bullish bias",
            },
            {
                "feature": "depth_imbalance",
                "operator": "<",
                "threshold": -0.2,
                "score_contribution": -0.3,
                "description": "Deep ask side confirms bearish bias",
            },
        ],
        "filters": [
            {
                "feature": "spread_bps",
                "operator": ">",
                "threshold": 30.0,
                "action": "block",
                "description": "Skip when spread is too wide — execution cost dominates",
            },
        ],
        "position_rule": {
            "max_position": 500,
            "sizing_mode": "signal_proportional",
            "fixed_size": 100,
            "holding_period_ticks": 10,
            "inventory_cap": 500,
        },
        "exit_rules": [
            {"exit_type": "stop_loss", "threshold_bps": 15.0, "description": "Cut losses at 15 bps"},
            {"exit_type": "take_profit", "threshold_bps": 25.0, "description": "Take profit at 25 bps"},
            {"exit_type": "time_exit", "timeout_ticks": 300, "description": "Force exit after 300 ticks"},
        ],
    },
    # ── 1: Spread Mean Reversion ────────────────────────────────────
    {
        "name": "spread_mean_reversion",
        "description": (
            "Capture spread compression: enter when spread widens beyond normal, "
            "fade extreme order imbalance as contrarian signal. "
            "Fixed sizing with trailing stop for tighter risk control."
        ),
        "signal_rules": [
            {
                "feature": "spread_bps",
                "operator": ">",
                "threshold": 10.0,
                "score_contribution": 0.4,
                "description": "Wide spread → expect compression → bullish edge",
            },
            {
                "feature": "order_imbalance",
                "operator": "<",
                "threshold": -0.4,
                "score_contribution": 0.3,
                "description": "Extreme ask-heavy imbalance → contrarian buy",
            },
            {
                "feature": "order_imbalance",
                "operator": ">",
                "threshold": 0.4,
                "score_contribution": -0.3,
                "description": "Extreme bid-heavy imbalance → contrarian sell",
            },
        ],
        "filters": [
            {
                "feature": "spread_bps",
                "operator": ">",
                "threshold": 50.0,
                "action": "block",
                "description": "Very wide spread may indicate halted/illiquid state",
            },
        ],
        "position_rule": {
            "max_position": 300,
            "sizing_mode": "fixed",
            "fixed_size": 100,
            "holding_period_ticks": 20,
            "inventory_cap": 300,
        },
        "exit_rules": [
            {"exit_type": "stop_loss", "threshold_bps": 10.0, "description": "Tight stop at 10 bps"},
            {"exit_type": "take_profit", "threshold_bps": 10.0, "description": "Take profit at 10 bps"},
            {"exit_type": "trailing_stop", "threshold_bps": 8.0, "description": "Trail peak by 8 bps"},
            {"exit_type": "time_exit", "timeout_ticks": 600, "description": "Force exit after 600 ticks"},
        ],
    },
    # ── 2: Trade Flow Pressure ──────────────────────────────────────
    {
        "name": "trade_flow_pressure",
        "description": (
            "Trade-level buy/sell flow imbalance as directional signal. "
            "Heavy buy flow → bullish momentum, heavy sell flow → bearish. "
            "Simple two-rule design for clarity."
        ),
        "signal_rules": [
            {
                "feature": "trade_flow_imbalance",
                "operator": ">",
                "threshold": 0.4,
                "score_contribution": 0.6,
                "description": "Strong buy flow → bullish momentum",
            },
            {
                "feature": "trade_flow_imbalance",
                "operator": "<",
                "threshold": -0.4,
                "score_contribution": -0.6,
                "description": "Strong sell flow → bearish momentum",
            },
        ],
        "filters": [
            {
                "feature": "spread_bps",
                "operator": ">",
                "threshold": 25.0,
                "action": "block",
                "description": "Skip when spread is too wide",
            },
        ],
        "position_rule": {
            "max_position": 500,
            "sizing_mode": "signal_proportional",
            "fixed_size": 100,
            "holding_period_ticks": 5,
            "inventory_cap": 500,
        },
        "exit_rules": [
            {"exit_type": "stop_loss", "threshold_bps": 15.0, "description": "Stop loss at 15 bps"},
            {"exit_type": "take_profit", "threshold_bps": 25.0, "description": "Take profit at 25 bps"},
            {"exit_type": "time_exit", "timeout_ticks": 300, "description": "Force exit after 300 ticks"},
        ],
    },
    # ── 3: Depth Divergence ─────────────────────────────────────────
    {
        "name": "depth_divergence",
        "description": (
            "Exploit divergence between order-book depth imbalance and "
            "trade flow direction. When depth says bullish but flow says "
            "bearish (or vice versa), fade the flow and trust the book."
        ),
        "signal_rules": [
            {
                "feature": "depth_imbalance",
                "operator": ">",
                "threshold": 0.35,
                "score_contribution": 0.5,
                "description": "Book stacked on bid side → structural support",
            },
            {
                "feature": "trade_flow_imbalance",
                "operator": "<",
                "threshold": -0.2,
                "score_contribution": 0.2,
                "description": "Sell flow against book support → contrarian add",
            },
            {
                "feature": "depth_imbalance",
                "operator": "<",
                "threshold": -0.35,
                "score_contribution": -0.5,
                "description": "Book stacked on ask side → structural resistance",
            },
            {
                "feature": "trade_flow_imbalance",
                "operator": ">",
                "threshold": 0.2,
                "score_contribution": -0.2,
                "description": "Buy flow against book resistance → contrarian fade",
            },
        ],
        "filters": [
            {
                "feature": "spread_bps",
                "operator": ">",
                "threshold": 20.0,
                "action": "block",
                "description": "Skip illiquid conditions",
            },
        ],
        "position_rule": {
            "max_position": 400,
            "sizing_mode": "signal_proportional",
            "fixed_size": 100,
            "holding_period_ticks": 15,
            "inventory_cap": 400,
        },
        "exit_rules": [
            {"exit_type": "stop_loss", "threshold_bps": 12.0, "description": "Stop loss at 12 bps"},
            {"exit_type": "take_profit", "threshold_bps": 20.0, "description": "Take profit at 20 bps"},
            {"exit_type": "trailing_stop", "threshold_bps": 10.0, "description": "Trail peak by 10 bps"},
            {"exit_type": "time_exit", "timeout_ticks": 400, "description": "Force exit after 400 ticks"},
        ],
    },
    # ── 4: Micro Price Alpha ────────────────────────────────────────
    {
        "name": "micro_price_alpha",
        "description": (
            "Volume-weighted micro-price deviating from mid-price signals "
            "short-term directional pressure. Combine with order imbalance "
            "for confirmation."
        ),
        "signal_rules": [
            {
                "feature": "order_imbalance",
                "operator": ">",
                "threshold": 0.25,
                "score_contribution": 0.4,
                "description": "Bid-heavy imbalance → micro price above mid",
            },
            {
                "feature": "order_imbalance",
                "operator": "<",
                "threshold": -0.25,
                "score_contribution": -0.4,
                "description": "Ask-heavy imbalance → micro price below mid",
            },
            {
                "feature": "bid_depth_5",
                "operator": ">",
                "threshold": 15000.0,
                "score_contribution": 0.2,
                "description": "Deep bid book → sustained buy pressure",
            },
            {
                "feature": "ask_depth_5",
                "operator": ">",
                "threshold": 15000.0,
                "score_contribution": -0.2,
                "description": "Deep ask book → sustained sell pressure",
            },
        ],
        "filters": [
            {
                "feature": "spread_bps",
                "operator": ">",
                "threshold": 25.0,
                "action": "block",
                "description": "Skip when spread is too wide",
            },
        ],
        "position_rule": {
            "max_position": 500,
            "sizing_mode": "signal_proportional",
            "fixed_size": 100,
            "holding_period_ticks": 8,
            "inventory_cap": 500,
        },
        "exit_rules": [
            {"exit_type": "stop_loss", "threshold_bps": 12.0, "description": "Cut losses at 12 bps"},
            {"exit_type": "take_profit", "threshold_bps": 18.0, "description": "Take profit at 18 bps"},
            {"exit_type": "time_exit", "timeout_ticks": 250, "description": "Force exit after 250 ticks"},
        ],
    },
]

# Keyword-to-template mapping for goal-based selection
_GOAL_KEYWORDS: dict[str, list[int]] = {
    "imbalance": [0, 3, 4],
    "momentum": [0, 2],
    "spread": [1],
    "reversion": [1],
    "mean reversion": [1],
    "flow": [2, 3],
    "pressure": [2],
    "trade": [2],
    "depth": [3, 0],
    "divergence": [3],
    "micro": [4],
    "price": [4, 0],
    "alpha": [4, 0, 2],
}


def select_ideas_for_goal(goal: str, n_ideas: int) -> list[int]:
    """Return up to *n_ideas* template indices best matching *goal*.

    Falls back to sequential [0, 1, 2, ...] when no keyword match.
    """
    goal_lower = goal.lower()
    scored: dict[int, int] = {}
    for keyword, indices in _GOAL_KEYWORDS.items():
        if keyword in goal_lower:
            for idx in indices:
                scored[idx] = scored.get(idx, 0) + 1

    if scored:
        ranked = sorted(scored, key=lambda i: scored[i], reverse=True)
    else:
        ranked = list(range(len(IDEA_TEMPLATES)))

    return ranked[:n_ideas]
