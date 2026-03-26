"""
matching_engine.py
------------------
Core fill simulation engine for Layer 5.

MatchingEngine simulates how a child order interacts with the LOB to produce
a fill.  It handles:
  - exchange model switch for no-partial vs partial-fill behavior
  - marketable / crossing limit fill with level walking
  - resting limit fill based on observed trade volume
  - maker-only (GTX) protection for post-only orders

Queue-position semantics (queue initialization, queue advancement, queue gate)
are NOT handled here.  They are the sole responsibility of FillSimulator
(layer7_validation).  By the time an order reaches MatchingEngine, any
queue gate has already been resolved upstream.
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
    NONE = "NONE"
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

    Pure matching logic only: price/qty/exchange-model.  Queue-position
    semantics are handled upstream by FillSimulator.

    Parameters ``queue_model`` and ``queue_position_assumption`` are accepted
    for backward-compatible construction but are **not used** in matching
    decisions.  Queue filtering is the sole responsibility of FillSimulator.
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
        # Retained for introspection / config round-tripping; not used in
        # matching decisions (queue gate lives in FillSimulator).
        self.queue_model = queue_model
        self.queue_position_assumption = float(np.clip(queue_position_assumption, 0.0, 1.0))
        self.partial_fill_allowed = partial_fill_allowed

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
        """Fill a non-marketable resting limit order.

        Queue-position filtering is handled upstream by FillSimulator.
        Here we only check whether observed trade activity at or through the
        order's price level justifies a fill, and apply exchange-model rules.
        """
        trade_through_qty, trade_touch_qty = self._trade_volume_against_order(child, state)

        if trade_through_qty > 0:
            if self.exchange_model == ExchangeModel.NO_PARTIAL_FILL:
                return child.qty, child.price or 0.0
            return min(child.qty, trade_through_qty), child.price or 0.0

        if trade_touch_qty <= 0:
            return 0, 0.0

        # Queue gate already resolved upstream — fill up to observed volume.
        fillable = min(child.qty, trade_touch_qty)
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

