"""
order_typing.py
---------------
Determines the appropriate order type and limit price for each ParentOrder
based on market conditions and execution urgency.

클래스
-----
OrderTyper  - Maps urgency + market state → (OrderType, OrderTIF, price)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState

from .order_types import OrderSide, OrderStatus, OrderTIF, OrderType, ParentOrder


class OrderTyper:
    """
    Decides execution order type and limit price for a ParentOrder.

    Decision logic
    --------------
    - urgency > aggressive_threshold         → LIMIT_IOC (or MARKET if at deadline)
    - time remaining < 10% of window         → MARKET
    - spread > spread_limit_multiplier * avg → widen limit offset
    - otherwise                              → LIMIT DAY

    매개변수
    ----------
    default_type : OrderType
        Fallback order type (default LIMIT).
    aggressive_threshold : float
        Urgency above which IOC/MARKET types are preferred (default 0.7).
    spread_limit_multiplier : float
        Multiple of the current spread used as the maximum limit offset
        for passive orders (default 2.0).
    """

    def __init__(
        self,
        default_type: OrderType = OrderType.LIMIT,
        aggressive_threshold: float = 0.7,
        spread_limit_multiplier: float = 2.0,
    ) -> None:
        self._default_type = default_type
        self._aggressive_threshold = aggressive_threshold
        self._spread_mult = spread_limit_multiplier

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    def determine_type(
        self,
        parent: ParentOrder,
        state: MarketState,
    ) -> tuple[OrderType, OrderTIF]:
        """
        Select the appropriate order type and TIF for a parent order.

        매개변수
        ----------
        parent : ParentOrder
        state : MarketState

        반환값
        -------
        tuple[OrderType, OrderTIF]
        """
        urgency = parent.urgency
        time_to_deadline = self._compute_time_fraction(parent, state)

        # 마감이 임박하면 체결 보장을 위해 MARKET을 사용한다
        if time_to_deadline is not None and time_to_deadline < 0.10:
            return OrderType.MARKET, OrderTIF.DAY

        # 긴급도가 높으면 LIMIT IOC를 사용한다(공격적 체결 시도, 대기 주문 없음)
        if urgency > self._aggressive_threshold:
            return OrderType.LIMIT_IOC, OrderTIF.IOC

        # 기본값: 대기형 LIMIT 주문
        tif = self.set_tif(self._default_type, urgency, time_to_deadline or 0.5)
        return self._default_type, tif

    def determine_limit_price(
        self,
        parent: ParentOrder,
        state: MarketState,
        order_type: OrderType,
    ) -> float | None:
        """
        Compute the limit price for a given order type and urgency.

        For MARKET orders returns None.
        For LIMIT / LIMIT_IOC:
          - BUY  → best_ask + offset   (positive offset = crossing)
          - SELL → best_bid - offset   (positive offset = crossing)
        offset = spread * urgency_factor
          urgency 1.0 → full spread (crosses the market)
          urgency 0.0 → 0 offset (passive, at best bid/ask)

        매개변수
        ----------
        parent : ParentOrder
        state : MarketState
        order_type : OrderType

        반환값
        -------
        float | None
        """
        if order_type == OrderType.MARKET:
            return None

        best_bid = state.lob.best_bid
        best_ask = state.lob.best_ask
        spread = state.lob.spread

        if best_bid is None or best_ask is None:
            return None

        spread = spread or 0.0
        urgency = parent.urgency
        # 긴급도가 높으면 스프레드를 넘기고, 낮으면 수동적으로 대기한다
        offset = spread * urgency

        if parent.side == OrderSide.BUY:
            # Join the ask side for passive; cross into asks for aggressive
            price = best_ask + offset
        else:  # SELL
            price = best_bid - offset

        # 가격이 양수인지 보장한다
        return max(0.0, price)

    def set_tif(
        self,
        order_type: OrderType,
        urgency: float,
        time_to_deadline: float,
    ) -> OrderTIF:
        """
        Determine the appropriate TIF for a given order type and context.

        매개변수
        ----------
        order_type : OrderType
        urgency : float
        time_to_deadline : float
            Fraction of execution window remaining (0 = deadline, 1 = just started).

        반환값
        -------
        OrderTIF
        """
        if order_type == OrderType.LIMIT_IOC:
            return OrderTIF.IOC
        if order_type == OrderType.LIMIT_FOK:
            return OrderTIF.FOK
        if order_type == OrderType.MARKET:
            return OrderTIF.DAY
        # LIMIT orders
        if urgency > self._aggressive_threshold:
            return OrderTIF.IOC
        if time_to_deadline < 0.15:
            return OrderTIF.IOC
        return OrderTIF.DAY

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_time_fraction(
        parent: ParentOrder,
        state: MarketState,
    ) -> float | None:
        """
        Return fraction of execution window remaining, or None if unknown.

        0.0 = at/past deadline, 1.0 = just started.
        """
        if parent.start_time is None or parent.end_time is None:
            return None
        total_window = (parent.end_time - parent.start_time).total_seconds()
        if total_window <= 0:
            return 0.0
        elapsed = (state.timestamp - parent.start_time).total_seconds()
        remaining = max(0.0, total_window - elapsed)
        return remaining / total_window
