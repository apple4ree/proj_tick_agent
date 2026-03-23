"""
delta_compute.py
----------------
Computes position deltas and creates ParentOrders from TargetPosition for Layer 3.

클래스
-----
DeltaComputer  - Translates desired → current position differences into orders
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from .order_types import OrderSide, OrderStatus, ParentOrder

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer2_position.target_builder import TargetPosition


class DeltaComputer:
    """
    Computes share-quantity deltas between a TargetPosition and current holdings,
    then materialises those deltas as ParentOrder objects ready for execution.
    """

    # ------------------------------------------------------------------
    # 델타 계산
    # ------------------------------------------------------------------

    def compute(
        self,
        target: TargetPosition,
        current_positions: dict[str, int],
    ) -> dict[str, int]:
        """
        Compute per-symbol quantity deltas.

        delta[sym] = target[sym] - current[sym]
        Positive → buy, negative → sell, zero → no trade.

        매개변수
        ----------
        target : TargetPosition
            Desired positions from Layer 2.
        current_positions : dict[str, int]
            Symbol → currently held quantity.

        반환값
        -------
        dict[str, int]
            Symbol → delta quantity (non-zero entries only).
        """
        all_syms = set(target.targets.keys()) | set(current_positions.keys())
        deltas: dict[str, int] = {}
        for sym in all_syms:
            tgt = target.targets.get(sym, 0)
            cur = current_positions.get(sym, 0)
            delta = tgt - cur
            if delta != 0:
                deltas[sym] = delta
        return deltas

    def compute_parent_orders(
        self,
        target: TargetPosition,
        current_positions: dict[str, int],
        prices: dict[str, float],
        market_states: dict[str, MarketState],
    ) -> list[ParentOrder]:
        """
        Translate TargetPosition into a list of ParentOrders.

        For each symbol with a non-zero delta a ParentOrder is created with:
        - side and quantity derived from the delta sign/magnitude
        - urgency derived from the signal confidence in target.signal_ref
        - start_time from the market state timestamp
        - end_time set to end-of-session if determinable, else None

        매개변수
        ----------
        target : TargetPosition
            Desired portfolio state.
        current_positions : dict[str, int]
            Current holdings.
        prices : dict[str, float]
            Symbol → current price (used to set arrival_mid).
        market_states : dict[str, MarketState]
            Symbol → latest market state.

        반환값
        -------
        list[ParentOrder]
        """
        deltas = self.compute(target, current_positions)
        orders: list[ParentOrder] = []
        for sym, delta_qty in deltas.items():
            state = market_states.get(sym)
            signal_score = target.signal_ref.get(sym, 0.0)
            # signal_ref에 confidence가 직접 저장되지 않으므로 abs(score)를 대용치로 사용한다
            urgency = min(1.0, abs(signal_score))
            order = self.to_parent_order(sym, delta_qty, urgency, state)
            orders.append(order)
        return orders

    def to_parent_order(
        self,
        symbol: str,
        delta_qty: int,
        urgency: float,
        state: MarketState | None,
    ) -> ParentOrder:
        """
        Create a single ParentOrder from a symbol delta.

        매개변수
        ----------
        symbol : str
            Instrument identifier.
        delta_qty : int
            Signed quantity delta.  Positive = buy, negative = sell.
        urgency : float
            Execution urgency in [0, 1].
        state : MarketState | None
            Current market snapshot for timing and price context.

        반환값
        -------
        ParentOrder
        """
        side = OrderSide.BUY if delta_qty > 0 else OrderSide.SELL
        abs_qty = abs(delta_qty)

        # 타이밍
        start_time: pd.Timestamp
        end_time: pd.Timestamp | None = None
        arrival_mid: float | None = None

        if state is not None:
            start_time = state.timestamp
            arrival_mid = state.lob.mid_price
            # session 필드에서 장 종료 시각을 유추한다
            end_time = self._infer_session_end(state)
        else:
            start_time = pd.Timestamp.utcnow()

        return ParentOrder.create(
            symbol=symbol,
            side=side,
            qty=abs_qty,
            urgency=urgency,
            start_time=start_time,
            end_time=end_time,
            arrival_mid=arrival_mid,
        )

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_session_end(state: MarketState) -> pd.Timestamp | None:
        """
        Infer the end-of-session deadline from the market state.

        For KRX regular sessions the close is at 15:30 KST.
        반환값 None when the session cannot be determined.
        """
        if state.session in ("halted", "closed", "post"):
            return None

        ts = state.timestamp
        try:
            # 타임스탬프와 같은 시간대 기준으로 장 종료를 15:30으로 설정한다
            tz = ts.tzinfo
            close = pd.Timestamp(
                year=ts.year,
                month=ts.month,
                day=ts.day,
                hour=15,
                minute=30,
                second=0,
                tzinfo=tz,
            )
            if close > ts:
                return close
        except (AttributeError, TypeError, ValueError):
            pass
        return None
