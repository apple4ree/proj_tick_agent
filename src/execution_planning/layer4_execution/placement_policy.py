"""
placement_policy.py
-------------------
Order placement policies for Layer 4.

Given a parent order and a desired child quantity, each policy decides the
order type, price, and TIF for the child order sent to the exchange.

Policies:
  - AggressivePlacement  : market orders or limit orders crossing the spread
  - PassivePlacement     : post at best bid/ask to minimise spread cost
  - SpreadAdaptivePlacement : dynamically choose aggressiveness based on
                              current spread width and LOB imbalance
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ParentOrder, ChildOrder
    from data.layer0_data.market_state import MarketState

from execution_planning.layer3_order.order_types import (
    ChildOrder,
    OrderSide,
    OrderType,
    OrderTIF,
    OrderStatus,
)


class PlacementPolicy(ABC):
    """추상 기반 class for all placement policies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the placement policy."""
        ...

    @abstractmethod
    def place(
        self,
        parent: ParentOrder,
        qty: int,
        state: MarketState,
    ) -> ChildOrder:
        """
        Create a child order for `qty` shares.

        매개변수
        ----------
        parent : ParentOrder
            The originating parent order (provides symbol, side, etc.).
        qty : int
            Number of shares for this child order.
        state : MarketState
            Current market state (LOB snapshot, mid, spread, …).

        반환값
        -------
        ChildOrder
            A fully-populated child order ready to be submitted.
        """
        ...

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    @staticmethod
    def _make_child(
        parent: ParentOrder,
        qty: int,
        order_type: OrderType,
        price: float | None,
        tif: OrderTIF,
        state: MarketState,
    ) -> ChildOrder:
        return ChildOrder(
            parent_id=parent.order_id,
            symbol=parent.symbol,
            side=parent.side,
            order_type=order_type,
            qty=qty,
            price=price,
            tif=tif,
            arrival_mid=state.mid,
        )


# ---------------------------------------------------------------------------
# 공격형
# ---------------------------------------------------------------------------

class AggressivePlacement(PlacementPolicy):
    """
    공격형 placement: cross the spread for immediate execution.

    For BUY  → LIMIT at best_ask  (or MARKET if use_market_orders=True)
    For SELL → LIMIT at best_bid  (or MARKET)

    Using LIMIT at the opposing best ensures the order fills at or better
    than the current spread while still benefiting from exchange price
    improvement if available.
    """

    def __init__(self, use_market_orders: bool = False) -> None:
        self.use_market_orders = use_market_orders

    @property
    def name(self) -> str:
        return "AggressivePlacement"

    def place(
        self,
        parent: ParentOrder,
        qty: int,
        state: MarketState,
    ) -> ChildOrder:
        lob = state.lob
        if self.use_market_orders:
            return self._make_child(
                parent, qty, OrderType.MARKET, None, OrderTIF.IOC, state
            )
        # 즉시 체결되도록 가격을 건 LIMIT 주문
        if parent.side == OrderSide.BUY:
            price = lob.best_ask if lob.best_ask is not None else lob.mid_price
        else:
            price = lob.best_bid if lob.best_bid is not None else lob.mid_price

        return self._make_child(
            parent, qty, OrderType.LIMIT, price, OrderTIF.IOC, state
        )


# ---------------------------------------------------------------------------
# 수동형
# ---------------------------------------------------------------------------

class PassivePlacement(PlacementPolicy):
    """
    수동형 placement: post at best bid/ask to earn the spread.

    The optional `offset_ticks` shifts the limit price further inside the
    book (positive value = more passive, negative = slightly aggressive).

    For BUY  → LIMIT at best_bid + offset_ticks * tick_size  (DAY)
    For SELL → LIMIT at best_ask - offset_ticks * tick_size  (DAY)
    """

    def __init__(
        self,
        offset_ticks: int = 0,
        tick_size: float = 1.0,
    ) -> None:
        self.offset_ticks = offset_ticks
        self.tick_size = tick_size

    @property
    def name(self) -> str:
        return "PassivePlacement"

    def place(
        self,
        parent: ParentOrder,
        qty: int,
        state: MarketState,
    ) -> ChildOrder:
        lob = state.lob
        offset = self.offset_ticks * self.tick_size

        if parent.side == OrderSide.BUY:
            best = lob.best_bid
            if best is None:
                best = (lob.mid_price or 0.0) - self.tick_size
            price = best + offset
        else:
            best = lob.best_ask
            if best is None:
                best = (lob.mid_price or 0.0) + self.tick_size
            price = best - offset

        return self._make_child(
            parent, qty, OrderType.LIMIT, price, OrderTIF.DAY, state
        )


# ---------------------------------------------------------------------------
# 스프레드 적응형
# ---------------------------------------------------------------------------

class SpreadAdaptivePlacement(PlacementPolicy):
    """
    Spread-adaptive placement: mix aggressive and passive based on market
    conditions.

    Decision logic:
        aggression = _compute_aggression(state)
        if aggression >= 0.5 → AggressivePlacement
        else                 → PassivePlacement

    Aggression is high when:
      - Spread is narrow (cost of crossing is low)
      - LOB imbalance is favourable (momentum in our direction)
    """

    def __init__(
        self,
        aggression_spread_threshold_bps: float = 5.0,
        imbalance_threshold: float = 0.3,
    ) -> None:
        self.aggression_spread_threshold_bps = aggression_spread_threshold_bps
        self.imbalance_threshold = imbalance_threshold
        self._aggressive = AggressivePlacement(use_market_orders=False)
        self._passive = PassivePlacement()

    @property
    def name(self) -> str:
        return "SpreadAdaptivePlacement"

    def _compute_aggression(self, state: MarketState) -> float:
        """
        Return a score in [0, 1].  0 = fully passive, 1 = fully aggressive.

        Components:
          - spread_score  : 1.0 if spread <= threshold, scales down linearly
          - imbalance_score : 1.0 if imbalance favourable and above threshold
        """
        # 스프레드 구성요소
        spread_bps = state.lob.spread_bps
        if spread_bps is None:
            spread_score = 0.5
        elif spread_bps <= self.aggression_spread_threshold_bps:
            spread_score = 1.0
        else:
            # 선형으로 감소시키며 임계값의 3배에서 공격성은 0이 된다
            decay_max = self.aggression_spread_threshold_bps * 3.0
            spread_score = max(
                0.0,
                1.0 - (spread_bps - self.aggression_spread_threshold_bps) / decay_max,
            )

        # 불균형 구성요소
        imbalance = state.lob.order_imbalance  # [-1, 1], positive = more bids
        if imbalance is None:
            imbalance_score = 0.5
        else:
            # 호가창이 우리 방향에 유리할 때 더 공격적으로 집행한다:
            # BUY → high positive imbalance means strong bid pressure (risky to be passive)
            # SELL → high negative imbalance means strong ask pressure
            # 여기서는 일반적인 불균형 신호를 반환하며 호출자가 재정의할 수 있다.
            imbalance_score = 1.0 if abs(imbalance) >= self.imbalance_threshold else 0.0

        return (spread_score + imbalance_score) / 2.0

    def place(
        self,
        parent: ParentOrder,
        qty: int,
        state: MarketState,
    ) -> ChildOrder:
        aggression = self._compute_aggression(state)

        # Check directional imbalance for BUY vs SELL
        imbalance = state.lob.order_imbalance or 0.0
        if parent.side == OrderSide.BUY and imbalance < -self.imbalance_threshold:
            # Strong ask pressure → seller has momentum, be passive as buyer
            aggression *= 0.5
        elif parent.side == OrderSide.SELL and imbalance > self.imbalance_threshold:
            # Strong bid pressure → buyer has momentum, be passive as seller
            aggression *= 0.5

        if aggression >= 0.5:
            return self._aggressive.place(parent, qty, state)
        else:
            return self._passive.place(parent, qty, state)
