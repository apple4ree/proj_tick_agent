"""Shared built-in feature extraction for StrategySpec v2 runtime."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState


BUILTIN_FEATURES: frozenset[str] = frozenset({
    "mid_price", "spread_bps", "order_imbalance",
    "best_bid", "best_ask",
    "bid_depth_5", "ask_depth_5", "depth_imbalance",
    "trade_count", "recent_volume", "trade_flow_imbalance",
    "price_impact_buy", "price_impact_sell",
    "price_impact_buy_bps", "price_impact_sell_bps",
    "volume_surprise", "micro_price", "trade_flow",
    "depth_imbalance_l1", "log_bid_depth", "log_ask_depth",
    "bid_depth", "ask_depth",
    # derived temporal features
    "order_imbalance_ema", "order_imbalance_delta",
    "trade_flow_imbalance_ema", "depth_imbalance_ema",
    "spread_bps_ema",
})


def extract_builtin_features(state: "MarketState") -> dict[str, float]:
    """Extract named features from MarketState for declarative rule evaluation."""
    features: dict[str, float] = {}

    lob = state.lob
    features["mid_price"] = lob.mid_price or 0.0
    features["spread_bps"] = state.spread_bps or 0.0
    features["order_imbalance"] = lob.order_imbalance or 0.0

    best_bid = lob.best_bid or 0.0
    best_ask = lob.best_ask or 0.0
    features["best_bid"] = best_bid
    features["best_ask"] = best_ask

    bid_depth = sum(lv.volume for lv in lob.bid_levels[:5]) if lob.bid_levels else 0
    ask_depth = sum(lv.volume for lv in lob.ask_levels[:5]) if lob.ask_levels else 0
    total_depth = bid_depth + ask_depth
    features["bid_depth_5"] = float(bid_depth)
    features["ask_depth_5"] = float(ask_depth)
    features["depth_imbalance"] = (bid_depth - ask_depth) / max(total_depth, 1)

    if state.features:
        for key in (
            "spread_bps", "order_imbalance", "bid_depth", "ask_depth",
            "price_impact_buy", "price_impact_sell", "trade_flow_imbalance",
            "volume_surprise", "micro_price", "price_impact_buy_bps",
            "price_impact_sell_bps", "depth_imbalance_l1",
            "log_bid_depth", "log_ask_depth", "trade_flow",
            "order_imbalance_ema", "order_imbalance_delta",
            "trade_flow_imbalance_ema", "depth_imbalance_ema",
            "spread_bps_ema",
        ):
            val = state.features.get(key)
            if val is not None:
                features[key] = float(val)

    if state.trades is not None and hasattr(state.trades, "empty") and not state.trades.empty:
        trades = state.trades
        features["trade_count"] = float(len(trades))
        vol_col = "volume" if "volume" in trades.columns else None
        if vol_col is not None:
            features["recent_volume"] = float(trades[vol_col].sum())
        if "trade_flow_imbalance" not in features and "side" in trades.columns:
            sides = trades["side"].apply(
                lambda s: 1.0 if str(s).lower() in ("buy", "b", "1") else -1.0
            )
            n = len(sides)
            if n > 0:
                features["trade_flow_imbalance"] = float(sides.sum() / n)

    return features
