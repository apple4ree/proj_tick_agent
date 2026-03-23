"""
matching_engine.py
------------------
Core fill simulation engine for Layer 5.

MatchingEngine simulates how a child order interacts with the LOB to produce
a fill. It borrows the main ideas from `hftbacktest`:
  - exchange model switch for no-partial vs partial-fill behavior
  - queue-position models for passive fills at the touch
  - maker-only protection for post-only orders
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ChildOrder
    from data.layer0_data.market_state import MarketState
    from market_simulation.layer5_simulator.order_book import OrderBookSimulator

from execution_planning.layer3_order.order_types import OrderSide, OrderTIF, OrderType


class QueueModel(Enum):
    PRICE_TIME = "PRICE_TIME"
    RISK_ADVERSE = "RISK_ADVERSE"
    PROB_QUEUE = "PROB_QUEUE"
    PRO_RATA = "PRO_RATA"
    RANDOM = "RANDOM"


class ExchangeModel(Enum):
    NO_PARTIAL_FILL = "NO_PARTIAL_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"


class MatchingEngine:
    """
    Simulates exchange matching of child orders against the order book.
    """

    def __init__(
        self,
        exchange_model: ExchangeModel = ExchangeModel.PARTIAL_FILL,
        queue_model: QueueModel = QueueModel.PROB_QUEUE,
        queue_position_assumption: float = 0.5,
        partial_fill_allowed: bool = True,
        rng_seed: int | None = None,
    ) -> None:
        self.exchange_model = exchange_model
        self.queue_model = queue_model
        self.queue_position_assumption = float(np.clip(queue_position_assumption, 0.0, 1.0))
        self.partial_fill_allowed = partial_fill_allowed
        self._rng = np.random.default_rng(rng_seed)

    def match(
        self,
        child: "ChildOrder",
        book: "OrderBookSimulator",
        state: "MarketState",
        latency_ms: float = 0.0,
    ) -> tuple[int, float]:
        if child.tif == OrderTIF.GTX and self._is_marketable(child, book):
            return 0, 0.0

        if child.order_type == OrderType.MARKET:
            filled_qty, avg_price = self._market_fill(child.side, child.qty, book)
        elif child.order_type in {OrderType.LIMIT, OrderType.LIMIT_IOC, OrderType.LIMIT_FOK}:
            filled_qty, avg_price = self._limit_fill(child, book, state)
        else:
            return 0, 0.0

        if child.tif == OrderTIF.FOK and filled_qty < child.qty:
            return 0, 0.0
        if not self.partial_fill_allowed and filled_qty < child.qty:
            return 0, 0.0
        return filled_qty, avg_price

    def _market_fill(
        self,
        side: OrderSide,
        qty: int,
        book: "OrderBookSimulator",
    ) -> tuple[int, float]:
        if self.exchange_model == ExchangeModel.NO_PARTIAL_FILL:
            best_price = book.get_best_ask() if side == OrderSide.BUY else book.get_best_bid()
            return qty, best_price
        avg_price, filled_qty = book.walk_book(side, qty)
        return filled_qty, avg_price

    def _limit_fill(
        self,
        child: "ChildOrder",
        book: "OrderBookSimulator",
        state: "MarketState",
    ) -> tuple[int, float]:
        if self._is_marketable(child, book):
            return self._crossing_limit_fill(child, book)
        return self._resting_limit_fill(child, book, state)

    def _crossing_limit_fill(
        self,
        child: "ChildOrder",
        book: "OrderBookSimulator",
    ) -> tuple[int, float]:
        if self.exchange_model == ExchangeModel.NO_PARTIAL_FILL:
            best_price = book.get_best_ask() if child.side == OrderSide.BUY else book.get_best_bid()
            return child.qty, best_price

        eligible_levels = book.eligible_levels(child.side, child.price)
        if not eligible_levels:
            return 0, 0.0

        remaining = child.qty
        total_cost = 0.0
        total_filled = 0
        for level in eligible_levels:
            if remaining <= 0:
                break
            fill_here = min(level.volume, remaining)
            total_cost += fill_here * level.price
            total_filled += fill_here
            remaining -= fill_here

        if total_filled == 0:
            return 0, 0.0
        return total_filled, total_cost / total_filled

    def _resting_limit_fill(
        self,
        child: "ChildOrder",
        book: "OrderBookSimulator",
        state: "MarketState",
    ) -> tuple[int, float]:
        trade_through_qty, trade_touch_qty = self._trade_volume_against_order(child, state)

        if trade_through_qty > 0:
            if self.exchange_model == ExchangeModel.NO_PARTIAL_FILL:
                return child.qty, child.price or 0.0
            return min(child.qty, trade_through_qty), child.price or 0.0

        if trade_touch_qty <= 0:
            return 0, 0.0

        accessible = self._queue_accessible_qty(child, book, trade_touch_qty)
        fillable = min(child.qty, accessible)
        if self.exchange_model == ExchangeModel.NO_PARTIAL_FILL and fillable < child.qty:
            return 0, 0.0
        if fillable <= 0:
            return 0, 0.0
        return fillable, child.price or 0.0

    def _is_marketable(
        self,
        child: "ChildOrder",
        book: "OrderBookSimulator",
    ) -> bool:
        if child.price is None:
            return child.order_type == OrderType.MARKET
        if child.side == OrderSide.BUY:
            best_ask = book.snapshot.best_ask
            return best_ask is not None and child.price >= best_ask
        best_bid = book.snapshot.best_bid
        return best_bid is not None and child.price <= best_bid

    def _trade_volume_against_order(
        self,
        child: "ChildOrder",
        state: "MarketState",
    ) -> tuple[int, int]:
        if child.price is None:
            return 0, 0

        if state.trades is not None and not state.trades.empty and "price" in state.trades.columns:
            trades = state.trades
            prices = trades["price"].astype(float)
            if "volume" in trades.columns:
                volumes = trades["volume"].astype(float)
            else:
                volumes = np.ones(len(trades), dtype=float)

            if child.side == OrderSide.BUY:
                return int(volumes[prices < float(child.price)].sum()), int(volumes[prices == float(child.price)].sum())
            return int(volumes[prices > float(child.price)].sum()), int(volumes[prices == float(child.price)].sum())

        last_price = state.lob.last_trade_price
        last_volume = int(state.lob.last_trade_volume or 0)
        if last_price is None:
            return 0, 0

        if child.side == OrderSide.BUY:
            if last_price < child.price:
                return last_volume, 0
            if last_price == child.price:
                return 0, last_volume
            return 0, 0

        if last_price > child.price:
            return last_volume, 0
        if last_price == child.price:
            return 0, last_volume
        return 0, 0

    def _queue_accessible_qty(
        self,
        child: "ChildOrder",
        book: "OrderBookSimulator",
        trade_qty: int,
    ) -> int:
        if trade_qty <= 0:
            return 0

        resting_volume = book.level_volume_at_price(child.side, child.price)

        if self.queue_model == QueueModel.PRO_RATA:
            return self._pro_rata_fill(child.qty, resting_volume, trade_qty)
        if self.queue_model == QueueModel.RANDOM:
            return int(self._rng.integers(0, trade_qty + 1))

        queue_ahead = self._queue_ahead_qty(resting_volume, self.queue_position_assumption)
        accessible = max(0, int(trade_qty - queue_ahead))
        if self.queue_model == QueueModel.PROB_QUEUE:
            optimistic_access = int(trade_qty * (1.0 - self.queue_position_assumption**2))
            accessible = min(trade_qty, max(accessible, optimistic_access))
        return accessible

    def _queue_ahead_qty(
        self,
        resting_volume: int,
        queue_pos: float,
    ) -> int:
        if resting_volume <= 0:
            return 0
        if self.queue_model in {QueueModel.PRICE_TIME, QueueModel.RISK_ADVERSE}:
            fraction_ahead = queue_pos
        elif self.queue_model == QueueModel.PROB_QUEUE:
            fraction_ahead = queue_pos**2
        else:
            fraction_ahead = queue_pos
        return max(0, int(resting_volume * fraction_ahead))

    def _pro_rata_fill(
        self,
        order_qty: int,
        resting_volume: int,
        trade_qty: int,
    ) -> int:
        total_available = max(1, resting_volume + order_qty)
        fill_ratio = min(1.0, order_qty / total_available)
        return int(fill_ratio * trade_qty)
