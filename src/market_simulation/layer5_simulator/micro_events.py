"""
micro_events.py
---------------
Micro-event detection and handling for Layer 5.

Detects and manages discrete exchange events that interrupt normal trading:
  - Volatility Interruption (VI) trigger / lift
  - Trading halt / resume
  - Session changes
  - Price band changes
  - Circuit breakers

All events are stored in MicroEventHandler.events for audit and replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ChildOrder
    from data.layer0_data.market_state import MarketState


class MicroEventType(Enum):
    VI_TRIGGERED = "VI_TRIGGERED"
    VI_LIFTED = "VI_LIFTED"
    TRADING_HALT = "TRADING_HALT"
    TRADING_RESUME = "TRADING_RESUME"
    SESSION_CHANGE = "SESSION_CHANGE"
    PRICE_BAND_CHANGE = "PRICE_BAND_CHANGE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


@dataclass
class MicroEvent:
    """A single discrete market micro-event."""
    timestamp: pd.Timestamp
    event_type: MicroEventType
    symbol: str
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"MicroEvent({self.event_type.value}, symbol={self.symbol}, "
            f"ts={self.timestamp}, details={self.details})"
        )


class MicroEventHandler:
    """
    Detects and logs discrete exchange micro-events by comparing consecutive
    market states.

    Usage
    -----
    >>> handler = MicroEventHandler()
    >>> events = handler.process(prev_state, curr_state)
    >>> if not handler.is_tradable(curr_state, events):
    ...     orders_to_cancel = handler.cancel_orders_on_halt(open_orders, events)

    임계값
    ----------
    vi_spread_multiplier : float
        If spread increases by this factor between consecutive states, a VI
        is suspected.  Default: 3.0 (spread triples or more).
    vi_depth_fraction : float
        If total LOB depth drops below this fraction of prior depth, VI
        is suspected.  Default: 0.05 (less than 5% of prior depth remains).
    """

    VI_SPREAD_MULTIPLIER: float = 3.0
    VI_DEPTH_FRACTION: float = 0.05

    def __init__(self) -> None:
        self.events: list[MicroEvent] = []
        self._halt_active: bool = False
        self._vi_active: bool = False

    # ------------------------------------------------------------------
    # 감지기
    # ------------------------------------------------------------------

    def detect_vi(
        self,
        prev_state: MarketState,
        curr_state: MarketState,
    ) -> Optional[MicroEvent]:
        """
        Detect a Volatility Interruption (VI) event.

        VI is suspected when:
          (a) Spread triples or more, OR
          (b) Total LOB depth drops to near-zero relative to prior state.

        If a VI was previously active and conditions have normalised, emit
        VI_LIFTED.
        """
        prev_spread = prev_state.lob.spread
        curr_spread = curr_state.lob.spread
        prev_depth = prev_state.lob.total_bid_depth + prev_state.lob.total_ask_depth
        curr_depth = curr_state.lob.total_bid_depth + curr_state.lob.total_ask_depth

        # VI 발생 여부 점검
        spread_spike = (
            prev_spread is not None
            and curr_spread is not None
            and prev_spread > 0.0
            and curr_spread >= self.VI_SPREAD_MULTIPLIER * prev_spread
        )
        depth_collapse = (
            prev_depth > 0
            and curr_depth < self.VI_DEPTH_FRACTION * prev_depth
        )

        if not self._vi_active and (spread_spike or depth_collapse):
            self._vi_active = True
            details = {}
            if spread_spike:
                details["spread_prev"] = prev_spread
                details["spread_curr"] = curr_spread
                details["spread_ratio"] = curr_spread / prev_spread if prev_spread else None
            if depth_collapse:
                details["depth_prev"] = prev_depth
                details["depth_curr"] = curr_depth
            details["trigger"] = "spread_spike" if spread_spike else "depth_collapse"
            event = MicroEvent(
                timestamp=curr_state.timestamp,
                event_type=MicroEventType.VI_TRIGGERED,
                symbol=curr_state.symbol,
                details=details,
            )
            self.events.append(event)
            return event

        # VI 해제 여부 점검
        if self._vi_active and not spread_spike and not depth_collapse:
            # 조건이 정상화됨
            self._vi_active = False
            event = MicroEvent(
                timestamp=curr_state.timestamp,
                event_type=MicroEventType.VI_LIFTED,
                symbol=curr_state.symbol,
                details={"spread_curr": curr_spread, "depth_curr": curr_depth},
            )
            self.events.append(event)
            return event

        return None

    def detect_session_change(
        self,
        prev_state: MarketState,
        curr_state: MarketState,
    ) -> Optional[MicroEvent]:
        """
        Detect a change in trading session (e.g. pre-market → regular).
        """
        if prev_state.session != curr_state.session:
            event = MicroEvent(
                timestamp=curr_state.timestamp,
                event_type=MicroEventType.SESSION_CHANGE,
                symbol=curr_state.symbol,
                details={
                    "from_session": prev_state.session,
                    "to_session": curr_state.session,
                },
            )
            self.events.append(event)
            return event
        return None

    def detect_halt(
        self,
        state: MarketState,
    ) -> Optional[MicroEvent]:
        """
        Detect a transition from tradable to halted.
        Also detects resumption (halt lifted).
        """
        if not self._halt_active and not state.tradable:
            self._halt_active = True
            event = MicroEvent(
                timestamp=state.timestamp,
                event_type=MicroEventType.TRADING_HALT,
                symbol=state.symbol,
                details={"session": state.session},
            )
            self.events.append(event)
            return event

        if self._halt_active and state.tradable:
            self._halt_active = False
            event = MicroEvent(
                timestamp=state.timestamp,
                event_type=MicroEventType.TRADING_RESUME,
                symbol=state.symbol,
                details={"session": state.session},
            )
            self.events.append(event)
            return event

        return None

    # ------------------------------------------------------------------
    # 종합 처리기
    # ------------------------------------------------------------------

    def process(
        self,
        prev_state: MarketState,
        curr_state: MarketState,
    ) -> list[MicroEvent]:
        """
        Run all detectors on the transition from prev_state → curr_state.

        반환값
        -------
        list[MicroEvent]
            Events detected in this transition (may be empty).
        """
        detected: list[MicroEvent] = []

        # 거래정지 감지를 먼저 수행한다(영향이 가장 큼)
        halt_event = self.detect_halt(curr_state)
        if halt_event is not None:
            detected.append(halt_event)

        # 세션 변경
        session_event = self.detect_session_change(prev_state, curr_state)
        if session_event is not None:
            detected.append(session_event)

        # VI(거래정지가 아닐 때만)
        if curr_state.tradable:
            vi_event = self.detect_vi(prev_state, curr_state)
            if vi_event is not None:
                detected.append(vi_event)

        return detected

    # ------------------------------------------------------------------
    # 거래 가능 여부 조회
    # ------------------------------------------------------------------

    def is_tradable(
        self,
        state: MarketState,
        events: list[MicroEvent],
    ) -> bool:
        """
        Return False if any blocking event is currently active.

        차단 조건:
          - state.tradable is False
          - A TRADING_HALT or VI_TRIGGERED event is in `events`
        """
        if not state.tradable:
            return False
        for evt in events:
            if evt.event_type in (
                MicroEventType.TRADING_HALT,
                MicroEventType.VI_TRIGGERED,
                MicroEventType.CIRCUIT_BREAKER,
            ):
                return False
        return True

    # ------------------------------------------------------------------
    # Order management on halt
    # ------------------------------------------------------------------

    def cancel_orders_on_halt(
        self,
        open_orders: list[ChildOrder],
        events: list[MicroEvent],
    ) -> list[ChildOrder]:
        """
        Return the subset of `open_orders` that should be cancelled due to
        a halt or VI event.

        On KRX, unexecuted orders are typically automatically cancelled when
        a halt is declared; this method identifies which orders the strategy
        must cancel (or mark as cancelled locally).

        반환값
        -------
        list[ChildOrder]
            Orders that should be cancelled.
        """
        blocking = any(
            evt.event_type in (
                MicroEventType.TRADING_HALT,
                MicroEventType.CIRCUIT_BREAKER,
            )
            for evt in events
        )
        if not blocking:
            return []

        return [order for order in open_orders if order.is_active]
