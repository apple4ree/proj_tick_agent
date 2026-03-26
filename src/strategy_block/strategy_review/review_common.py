"""Shared review datatypes/constants for v2 strategy review."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KNOWN_FEATURES: set[str] = {
    "mid_price", "spread_bps", "order_imbalance",
    "best_bid", "best_ask",
    "bid_depth_5", "ask_depth_5", "depth_imbalance",
    "trade_count", "recent_volume", "trade_flow_imbalance",
    "price_impact_buy", "price_impact_sell",
    "price_impact_buy_bps", "price_impact_sell_bps",
    "volume_surprise", "micro_price", "trade_flow",
    "depth_imbalance_l1", "log_bid_depth", "log_ask_depth",
    "bid_depth", "ask_depth",
}

# Names that may ONLY appear as position_attr, never as feature.
# Using these as feature causes silent runtime failure (features.get returns 0.0).
POSITION_ATTR_ONLY: set[str] = {
    "holding_ticks",
    "unrealized_pnl_bps",
    "entry_price",
    "position_size",
    "position_side",
}


@dataclass
class ReviewIssue:
    severity: str
    category: str
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
