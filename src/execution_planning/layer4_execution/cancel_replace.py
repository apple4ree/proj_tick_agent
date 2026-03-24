"""
cancel_replace.py
-----------------
Cancel and replace logic for Layer 4.

CancelReplaceLogic monitors open child orders and decides whether each
should be cancelled, repriced (replaced), or left alone.

Cancellation triggers:
  1. Timeout    – order has been open longer than timeout_seconds
  2. Stale price – limit price is stale_levels levels away from best
  3. Adverse selection – mid has moved against us by > threshold since submission

Replacement triggers:
  - Order is stale but not yet adverse-selected → repeg to current best
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ChildOrder
    from data.layer0_data.market_state import MarketState

from execution_planning.layer3_order.order_types import OrderSide, OrderStatus


class CancelReplaceLogic:
    """
    Monitors open child orders and produces cancel / replace / keep decisions.

    매개변수
    ----------
    timeout_seconds : float
        Maximum age (seconds) of an open order before it is cancelled.
    stale_levels : int
        Number of price levels away from the current best before an order
        is considered stale and subject to replacement.
    adverse_selection_threshold_bps : float
        If the mid price has moved by this many bps against the order's
        direction since submission, the order is cancelled (adverse fill risk).
    """

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        stale_levels: int = 2,
        adverse_selection_threshold_bps: float = 5.0,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.stale_levels = stale_levels
        self.adverse_selection_threshold_bps = adverse_selection_threshold_bps

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def should_cancel(
        self,
        child: ChildOrder,
        state: MarketState,
        time_since_submit: float,
        cancel_after_ticks: int | None = None,
    ) -> tuple[bool, str]:
        """
        Determine whether `child` should be cancelled.

        반환값
        -------
        (should_cancel, reason)
        """
        # 1. 타임아웃 (optional signal override)
        timeout_seconds = self.timeout_seconds
        if cancel_after_ticks is not None and cancel_after_ticks > 0:
            timeout_seconds = float(cancel_after_ticks)

        if time_since_submit >= timeout_seconds:
            return True, f"timeout ({time_since_submit:.1f}s >= {timeout_seconds}s)"

        # 2. 역선택
        if self.detect_adverse_selection(child, state):
            return True, "adverse_selection"

        # 3. 가격이 너무 멀어짐(가격 노후화, 다만 교체가 우선)
        #    - 재호가가 불가능할 때 취소를 대안으로 사용한다)
        levels_away = self._levels_away_from_best(child, state)
        if levels_away > self.stale_levels * 2:
            # 매우 오래된 가격이면 바로 취소한다
            return True, f"price_very_stale ({levels_away} levels away)"

        return False, ""

    def should_replace(
        self,
        child: ChildOrder,
        state: MarketState,
    ) -> tuple[bool, float | None]:
        """
        Determine whether `child` should be repriced.

        반환값
        -------
        (should_replace, new_price)
            new_price is None when should_replace is False.
        """
        # 역선택이 발생한 상태에서는 교체하지 않고 취소한다
        if self.detect_adverse_selection(child, state):
            return False, None

        levels_away = self._levels_away_from_best(child, state)
        if levels_away > self.stale_levels:
            new_p = self.new_price(child, state)
            return True, new_p

        return False, None

    def new_price(self, child: ChildOrder, state: MarketState) -> float:
        """
        Compute the replacement limit price: repeg to current best bid/ask.
        """
        lob = state.lob
        if child.side == OrderSide.BUY:
            return lob.best_bid if lob.best_bid is not None else (lob.mid_price or child.price or 0.0)
        else:
            return lob.best_ask if lob.best_ask is not None else (lob.mid_price or child.price or 0.0)

    def detect_adverse_selection(
        self,
        child: ChildOrder,
        state: MarketState,
    ) -> bool:
        """
        Return True if the mid price has moved adversely by more than
        adverse_selection_threshold_bps since the order was submitted.
        """
        arrival_mid = child.arrival_mid
        current_mid = state.mid
        if arrival_mid is None or current_mid is None or arrival_mid == 0.0:
            return False

        move_bps = ((current_mid - arrival_mid) / arrival_mid) * 10_000.0

        if child.side == OrderSide.BUY:
            # 매수자에게 불리: 중간가가 상승했다(오르는 시장을 쫓았다)
            return move_bps >= self.adverse_selection_threshold_bps
        else:
            # 매도자에게 불리: 중간가가 하락했다(내리는 시장을 쫓았다)
            return move_bps <= -self.adverse_selection_threshold_bps

    def process_open_orders(
        self,
        open_orders: list[ChildOrder],
        state: MarketState,
        current_time: pd.Timestamp,
        cancel_after_ticks: int | None = None,
        max_reprices: int | None = None,
    ) -> list[dict]:
        """
        Evaluate every open order and return a list of action dictionaries.

        Each element:
            {'action': 'cancel' | 'replace' | 'keep',
             'order': <ChildOrder>,
             'new_price': float | None}
        """
        actions: list[dict] = []
        for child in open_orders:
            if not child.is_active:
                continue

            time_since = (
                (current_time - child.submit_time).total_seconds()
                if child.submit_time is not None
                else 0.0
            )

            cancel, reason = self.should_cancel(
                child, state, time_since, cancel_after_ticks=cancel_after_ticks
            )
            if cancel:
                actions.append(
                    {"action": "cancel", "order": child, "new_price": None, "reason": reason}
                )
                continue

            replace, new_p = self.should_replace(child, state)
            if replace:
                reprice_count = int(child.meta.get("reprice_count", 0))
                if max_reprices is not None and max_reprices >= 0 and reprice_count >= max_reprices:
                    actions.append(
                        {"action": "cancel", "order": child, "new_price": None, "reason": "max_reprices_reached"}
                    )
                else:
                    actions.append(
                        {"action": "replace", "order": child, "new_price": new_p, "reason": "stale_price"}
                    )
            else:
                actions.append(
                    {"action": "keep", "order": child, "new_price": None, "reason": ""}
                )
        return actions

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    def _levels_away_from_best(
        self,
        child: ChildOrder,
        state: MarketState,
    ) -> int:
        """
        Estimate how many LOB levels away `child.price` is from the current
        best quote on the relevant side.
        """
        if child.price is None:
            return 0
        lob = state.lob

        if child.side == OrderSide.BUY:
            levels = lob.bid_levels
            if not levels:
                return 0
            best = levels[0].price
            # child.price보다 엄격히 더 좋은 레벨 수를 센다
            return sum(1 for lvl in levels if lvl.price > child.price)
        else:
            levels = lob.ask_levels
            if not levels:
                return 0
            # child.price보다 엄격히 더 좋은(낮은) 레벨 수를 센다
            return sum(1 for lvl in levels if lvl.price < child.price)
