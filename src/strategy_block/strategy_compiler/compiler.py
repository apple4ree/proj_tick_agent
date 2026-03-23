"""
strategy_compiler/compiler.py
-----------------------------
Converts a StrategySpec (JSON/DSL) into an executable Strategy object
that plugs into the existing PipelineRunner backtest engine.

The CompiledStrategy implements the Strategy ABC and evaluates
signal rules, filters, position rules, and exit rules at each tick.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from strategy_block.strategy.base import Strategy
from strategy_block.strategy_specs.schema import StrategySpec, SignalRule, FilterRule, ExitRule

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer1_signal import Signal

logger = logging.getLogger(__name__)


class CompiledStrategy(Strategy):
    """Executable strategy compiled from a StrategySpec.

    Evaluates signal rules against market state features to produce
    directional signals compatible with the Layer 1 Signal interface.

    Parameters
    ----------
    spec : StrategySpec
        The strategy specification to compile.
    """

    def __init__(self, spec: StrategySpec) -> None:
        self._spec = spec
        self._entry_price: dict[str, float] = {}
        self._entry_tick: dict[str, int] = {}
        self._tick_count: dict[str, int] = {}
        self._position: dict[str, float] = {}
        self._trailing_high: dict[str, float] = {}
        self._trailing_low: dict[str, float] = {}
        self._prev_features: dict[str, dict[str, float]] = {}

    @property
    def name(self) -> str:
        return f"Compiled:{self._spec.name}"

    def reset(self) -> None:
        self._entry_price.clear()
        self._entry_tick.clear()
        self._tick_count.clear()
        self._position.clear()
        self._trailing_high.clear()
        self._trailing_low.clear()
        self._prev_features.clear()

    def generate_signal(self, state: "MarketState") -> "Signal | None":
        from execution_planning.layer1_signal import Signal

        if state.lob.mid_price is None:
            return None

        symbol = state.symbol
        mid = state.lob.mid_price
        tick = self._tick_count.get(symbol, 0)
        self._tick_count[symbol] = tick + 1

        features = self._extract_features(state)

        # Check exit rules first (if we have a position)
        current_pos = self._position.get(symbol, 0.0)
        if current_pos != 0.0:
            exit_signal = self._check_exit_rules(symbol, mid, tick, current_pos)
            if exit_signal is not None:
                # Generate reversal signal to close position
                score = -1.0 if current_pos > 0 else 1.0
                self._position[symbol] = 0.0
                self._entry_price.pop(symbol, None)
                self._entry_tick.pop(symbol, None)
                self._trailing_high.pop(symbol, None)
                self._trailing_low.pop(symbol, None)
                return Signal(
                    timestamp=state.timestamp,
                    symbol=symbol,
                    score=score,
                    expected_return=score * 5.0,
                    confidence=0.8,
                    horizon_steps=1,
                    tags={"strategy": self.name, "exit_type": exit_signal},
                    is_valid=True,
                )

            # Update trailing prices
            high = self._trailing_high.get(symbol, mid)
            low = self._trailing_low.get(symbol, mid)
            self._trailing_high[symbol] = max(high, mid)
            self._trailing_low[symbol] = min(low, mid)

        # Check holding period
        if current_pos != 0.0:
            entry_tick = self._entry_tick.get(symbol, tick)
            min_hold = self._spec.position_rule.holding_period_ticks
            if min_hold > 0 and (tick - entry_tick) < min_hold:
                self._prev_features[symbol] = features
                return None

        # Check filters
        if not self._check_filters(features):
            self._prev_features[symbol] = features
            return None

        # Evaluate signal rules
        score = self._evaluate_signal_rules(features, symbol)
        if score == 0.0:
            self._prev_features[symbol] = features
            return None

        # Check inventory cap
        inv_cap = self._spec.position_rule.inventory_cap
        if inv_cap > 0:
            if (current_pos > 0 and score > 0) or (current_pos < 0 and score < 0):
                if abs(current_pos) >= inv_cap:
                    self._prev_features[symbol] = features
                    return None

        # Clamp score to [-1, 1]
        score = max(-1.0, min(1.0, score))

        # Update position tracking
        if score != 0.0:
            if current_pos == 0.0 or (score > 0) != (current_pos > 0):
                self._entry_price[symbol] = mid
                self._entry_tick[symbol] = tick
                self._trailing_high[symbol] = mid
                self._trailing_low[symbol] = mid
            self._position[symbol] = score

        self._prev_features[symbol] = features

        return Signal(
            timestamp=state.timestamp,
            symbol=symbol,
            score=score,
            expected_return=score * 10.0,
            confidence=min(1.0, abs(score)),
            horizon_steps=1,
            tags={"strategy": self.name, "spec_version": self._spec.version},
            is_valid=True,
        )

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _extract_features(self, state: "MarketState") -> dict[str, float]:
        """Extract named features from MarketState for rule evaluation.

        Reads from LOBSnapshot (bid_levels/ask_levels), state.features dict,
        and state.trades DataFrame — matching the current Layer 0 data contract.
        """
        features: dict[str, float] = {}

        lob = state.lob
        features["mid_price"] = lob.mid_price or 0.0
        features["spread_bps"] = state.spread_bps or 0.0
        features["order_imbalance"] = lob.order_imbalance or 0.0

        best_bid = lob.best_bid or 0.0
        best_ask = lob.best_ask or 0.0
        features["best_bid"] = best_bid
        features["best_ask"] = best_ask

        # Depth features (bid_levels / ask_levels — Layer 0 contract)
        bid_depth = sum(lv.volume for lv in lob.bid_levels[:5]) if lob.bid_levels else 0
        ask_depth = sum(lv.volume for lv in lob.ask_levels[:5]) if lob.ask_levels else 0
        total_depth = bid_depth + ask_depth
        features["bid_depth_5"] = float(bid_depth)
        features["ask_depth_5"] = float(ask_depth)
        features["depth_imbalance"] = (bid_depth - ask_depth) / max(total_depth, 1)

        # Microstructure features from state.features dict
        if state.features:
            for key in ("spread_bps", "order_imbalance", "bid_depth", "ask_depth",
                        "price_impact_buy", "price_impact_sell", "trade_flow_imbalance",
                        "volume_surprise", "micro_price", "price_impact_buy_bps",
                        "price_impact_sell_bps", "depth_imbalance_l1",
                        "log_bid_depth", "log_ask_depth", "trade_flow"):
                val = state.features.get(key)
                if val is not None:
                    features[key] = float(val)

        # Trade features from state.trades DataFrame
        if state.trades is not None and hasattr(state.trades, "empty") and not state.trades.empty:
            trades = state.trades
            features["trade_count"] = float(len(trades))
            vol_col = "volume" if "volume" in trades.columns else None
            if vol_col is not None:
                features["recent_volume"] = float(trades[vol_col].sum())
            # Derive trade_flow_imbalance from side if not already present
            if "trade_flow_imbalance" not in features and "side" in trades.columns:
                sides = trades["side"].apply(
                    lambda s: 1.0 if str(s).lower() in ("buy", "b", "1") else -1.0
                )
                n = len(sides)
                if n > 0:
                    features["trade_flow_imbalance"] = float(sides.sum() / n)

        return features

    # ------------------------------------------------------------------
    # Rule evaluation
    # ------------------------------------------------------------------

    def _evaluate_condition(self, feature_val: float, operator: str,
                            threshold: float, symbol: str = "",
                            feature_name: str = "") -> bool:
        """Evaluate a single condition (feature op threshold)."""
        if operator == ">":
            return feature_val > threshold
        elif operator == "<":
            return feature_val < threshold
        elif operator == ">=":
            return feature_val >= threshold
        elif operator == "<=":
            return feature_val <= threshold
        elif operator == "==":
            return abs(feature_val - threshold) < 1e-9
        elif operator == "cross_above":
            prev = self._prev_features.get(symbol, {}).get(feature_name, feature_val)
            return prev <= threshold < feature_val
        elif operator == "cross_below":
            prev = self._prev_features.get(symbol, {}).get(feature_name, feature_val)
            return prev >= threshold > feature_val
        return False

    def _evaluate_signal_rules(self, features: dict[str, float], symbol: str) -> float:
        """Evaluate all signal rules and return aggregate score."""
        total_score = 0.0
        for rule in self._spec.signal_rules:
            val = features.get(rule.feature)
            if val is None:
                continue
            if self._evaluate_condition(val, rule.operator, rule.threshold,
                                        symbol, rule.feature):
                total_score += rule.score_contribution
        return total_score

    def _check_filters(self, features: dict[str, float]) -> bool:
        """Check all filter rules. Returns False if any 'block' filter triggers."""
        for f in self._spec.filters:
            val = features.get(f.feature)
            if val is None:
                continue
            triggered = self._evaluate_condition(val, f.operator, f.threshold)
            if triggered and f.action == "block":
                return False
        return True

    def _check_exit_rules(self, symbol: str, mid: float, tick: int,
                          position: float) -> str | None:
        """Check exit rules. Returns exit_type string if exit triggered, else None."""
        entry_price = self._entry_price.get(symbol)
        if entry_price is None or entry_price == 0.0:
            return None

        pnl_bps = ((mid - entry_price) / entry_price) * 10000.0
        if position < 0:
            pnl_bps = -pnl_bps

        for rule in self._spec.exit_rules:
            if rule.exit_type == "stop_loss":
                if pnl_bps < -abs(rule.threshold_bps):
                    return "stop_loss"

            elif rule.exit_type == "take_profit":
                if pnl_bps > abs(rule.threshold_bps):
                    return "take_profit"

            elif rule.exit_type == "trailing_stop":
                if position > 0:
                    high = self._trailing_high.get(symbol, mid)
                    drawdown_bps = ((high - mid) / high) * 10000.0
                    if drawdown_bps > abs(rule.threshold_bps):
                        return "trailing_stop"
                else:
                    low = self._trailing_low.get(symbol, mid)
                    if low > 0:
                        drawup_bps = ((mid - low) / low) * 10000.0
                        if drawup_bps > abs(rule.threshold_bps):
                            return "trailing_stop"

            elif rule.exit_type == "time_exit":
                if rule.timeout_ticks > 0:
                    entry_tick = self._entry_tick.get(symbol, tick)
                    if (tick - entry_tick) >= rule.timeout_ticks:
                        return "time_exit"

            elif rule.exit_type == "signal_reversal":
                pass  # Handled implicitly by new signal score

        return None


    # Features always available from LOBSnapshot + MarketState
    BUILTIN_FEATURES: frozenset[str] = frozenset({
        "mid_price", "spread_bps", "order_imbalance",
        "best_bid", "best_ask",
        "bid_depth_5", "ask_depth_5", "depth_imbalance",
        # From state.trades when available
        "trade_count", "recent_volume", "trade_flow_imbalance",
        # From state.features dict (FeaturePipeline output)
        "price_impact_buy", "price_impact_sell",
        "price_impact_buy_bps", "price_impact_sell_bps",
        "volume_surprise", "micro_price", "trade_flow",
        "depth_imbalance_l1", "log_bid_depth", "log_ask_depth",
        "bid_depth", "ask_depth",
    })


class StrategyCompiler:
    """Compiles StrategySpec objects into executable CompiledStrategy instances."""

    @staticmethod
    def compile(spec: StrategySpec) -> CompiledStrategy:
        """Compile a strategy specification into an executable strategy.

        Parameters
        ----------
        spec : StrategySpec
            Validated strategy specification.

        Returns
        -------
        CompiledStrategy
            Executable strategy for use with PipelineRunner.

        Raises
        ------
        ValueError
            If the spec fails validation.
        """
        errors = spec.validate()
        if errors:
            raise ValueError(
                f"Invalid strategy spec '{spec.name}':\n  - " + "\n  - ".join(errors)
            )

        # Warn about feature names not in the known set
        required = set()
        for rule in spec.signal_rules:
            required.add(rule.feature)
        for f in spec.filters:
            required.add(f.feature)
        unknown = required - CompiledStrategy.BUILTIN_FEATURES
        if unknown:
            logger.warning(
                "Strategy '%s' references unknown features %s — they will only "
                "be available if state.features dict contains them at runtime.",
                spec.name, sorted(unknown),
            )

        logger.info("Compiled strategy '%s' (v%s) with %d signal rules, %d filters, %d exit rules",
                     spec.name, spec.version,
                     len(spec.signal_rules), len(spec.filters), len(spec.exit_rules))
        return CompiledStrategy(spec)

    @staticmethod
    def from_json_file(path: str) -> CompiledStrategy:
        """Load a spec from JSON and compile it."""
        spec = StrategySpec.load(path)
        return StrategyCompiler.compile(spec)
