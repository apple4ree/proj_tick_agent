"""
fill_simulator.py
-----------------
Fill simulation logic extracted from PipelineRunner.

Handles matching child orders against the LOB, applying impact/fee models,
and recording fills into the bookkeeper and PnL ledger.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer3_order.order_types import ParentOrder
    from market_simulation.layer5_simulator.bookkeeper import FillEvent

logger = logging.getLogger(__name__)


class FillSimulator:
    """Simulates fill execution for child orders against the LOB."""

    def __init__(
        self,
        matching_engine,
        order_book,
        latency_model,
        fee_model,
        impact_model,
        bookkeeper,
        pnl_ledger,
    ) -> None:
        self._matching_engine = matching_engine
        self._order_book = order_book
        self._latency_model = latency_model
        self._fee_model = fee_model
        self._impact_model = impact_model
        self._bookkeeper = bookkeeper
        self._pnl_ledger = pnl_ledger

    def simulate_fills(
        self,
        parent: "ParentOrder",
        child_orders: list,
        state: "MarketState",
    ) -> list["FillEvent"]:
        """Simulate fill execution for child orders against the current LOB."""
        from execution_planning.layer3_order.order_types import OrderStatus
        from market_simulation.layer5_simulator.bookkeeper import FillEvent

        fills: list[FillEvent] = []
        mid = state.lob.mid_price
        if mid is None:
            return fills

        self._order_book.update(state.lob)
        adv_proxy = max(1.0, float(state.lob.total_bid_depth + state.lob.total_ask_depth))

        for child in child_orders:
            # Parent-level overfill guard: stop filling once parent is complete
            if parent.remaining_qty <= 0:
                child.status = OrderStatus.CANCELLED
                continue

            remaining_qty = child.remaining_qty
            if remaining_qty <= 0:
                child.status = OrderStatus.FILLED
                continue

            # Cap child fill to parent remaining to prevent overfill
            remaining_qty = min(remaining_qty, parent.remaining_qty)

            latency_ms = self._latency_model.total_round_trip_ms()
            filled_qty, matched_price = self._matching_engine.match(
                child=replace(child, qty=remaining_qty, filled_qty=0),
                book=self._order_book,
                state=state,
                latency_ms=latency_ms,
            )

            if filled_qty <= 0:
                child.status = OrderStatus.CANCELLED if child.tif.name == "IOC" else OrderStatus.OPEN
                continue

            # Final guard: clamp to parent remaining
            filled_qty = min(filled_qty, parent.remaining_qty)

            impacted_price = self._impact_model.adjust_price(
                base_price=matched_price,
                qty=filled_qty,
                adv=adv_proxy,
                mid=mid,
                side=child.side,
            )
            impact_bps = abs((impacted_price - matched_price) / mid) * 10_000.0 if mid else 0.0
            slippage_bps = self._compute_slippage_bps(child.arrival_mid or mid, impacted_price, child.side)
            fee = self._fee_model.compute(
                qty=filled_qty,
                price=impacted_price,
                side=child.side,
                is_maker=self._is_maker_fill(child, state),
            )

            fill = FillEvent(
                timestamp=state.timestamp,
                order_id=child.child_id,
                parent_id=child.parent_id,
                symbol=child.symbol,
                side=child.side,
                filled_qty=filled_qty,
                fill_price=impacted_price,
                fee=fee,
                is_maker=self._is_maker_fill(child, state),
                slippage_bps=slippage_bps,
                market_impact_bps=impact_bps,
                latency_ms=latency_ms,
            )
            fills.append(fill)

            existing_child_qty = child.filled_qty
            child.filled_qty += filled_qty
            child.avg_fill_price = self._weighted_avg_price(
                child.avg_fill_price, existing_child_qty, impacted_price, filled_qty,
            )
            child.fill_time = state.timestamp
            child.status = OrderStatus.FILLED if child.is_complete else OrderStatus.PARTIAL

            existing_parent_qty = parent.filled_qty
            parent.filled_qty += filled_qty
            parent.avg_fill_price = self._weighted_avg_price(
                parent.avg_fill_price, existing_parent_qty, impacted_price, filled_qty,
            )
            parent.status = OrderStatus.FILLED if parent.is_complete else OrderStatus.PARTIAL

        return fills

    def record_fills(
        self,
        fills: list["FillEvent"],
        mid: float | None,
        all_fills: list["FillEvent"],
    ) -> None:
        """Record fills into bookkeeper and PnL ledger."""
        if mid is None:
            return
        for fill in fills:
            cost_basis = self._bookkeeper.get_average_cost(fill.symbol)
            self._bookkeeper.record_fill(fill)
            self._pnl_ledger.record_fill(fill, cost_basis=cost_basis, mark_price=mid)
            all_fills.append(fill)

    @staticmethod
    def _compute_slippage_bps(arrival_mid: float, fill_price: float, side) -> float:
        from execution_planning.layer3_order.order_types import OrderSide
        if arrival_mid <= 0.0:
            return 0.0
        raw_bps = ((fill_price - arrival_mid) / arrival_mid) * 10_000.0
        return raw_bps if side == OrderSide.BUY else -raw_bps

    @staticmethod
    def _is_maker_fill(child, state: "MarketState") -> bool:
        from execution_planning.layer3_order.order_types import OrderSide, OrderType
        if child.order_type != OrderType.LIMIT or child.price is None:
            return False
        if child.side == OrderSide.BUY:
            best_bid = state.lob.best_bid
            return best_bid is not None and child.price <= best_bid
        best_ask = state.lob.best_ask
        return best_ask is not None and child.price >= best_ask

    @staticmethod
    def _weighted_avg_price(
        existing_price: float | None,
        existing_qty: int,
        new_price: float,
        new_qty: int,
    ) -> float:
        if existing_qty <= 0 or existing_price is None:
            return new_price
        total_qty = existing_qty + new_qty
        if total_qty <= 0:
            return new_price
        return ((existing_price * existing_qty) + (new_price * new_qty)) / total_qty
