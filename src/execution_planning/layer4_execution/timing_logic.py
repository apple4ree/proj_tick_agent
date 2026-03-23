"""
timing_logic.py
---------------
Timing-based execution trigger logic for Layer 4.

TimingLogic determines *when* to send the next child order by evaluating
multiple market-condition triggers in priority order:

  1. Deadline approaching   – urgency override (always send if time is running out)
  2. Spread narrowed        – good time to cross the spread cheaply
  3. Volume spike           – liquidity event, easier to fill
  4. Imbalance favourable   – order-flow momentum in our direction
  5. Time interval elapsed  – fallback periodic trigger
"""
from __future__ import annotations

from collections import deque
from enum import Enum
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ParentOrder
    from data.layer0_data.market_state import MarketState


class TimingTrigger(Enum):
    TIME_BASED = "TIME_BASED"
    SPREAD_NARROW = "SPREAD_NARROW"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    IMBALANCE_FAVORABLE = "IMBALANCE_FAVORABLE"
    DEADLINE_APPROACHING = "DEADLINE_APPROACHING"


class TimingLogic:
    """
    Multi-condition timing controller for child order dispatch.

    매개변수
    ----------
    interval_seconds : float
        Minimum seconds between consecutive child submissions (fallback timer).
    spread_trigger_bps : float | None
        Send when spread_bps drops below this value.  None = disabled.
    volume_trigger_ratio : float | None
        Send when current LOB depth exceeds baseline by this multiple.
        None = disabled.
    imbalance_trigger : float | None
        Absolute imbalance threshold in [0, 1].  None = disabled.
    deadline_urgency_seconds : float
        If less than this many seconds remain before parent.end_time, always
        trigger (deadline override).
    baseline_window : int
        Rolling window size (number of states) for volume baseline.
    """

    def __init__(
        self,
        interval_seconds: float = 10.0,
        spread_trigger_bps: Optional[float] = None,
        volume_trigger_ratio: Optional[float] = None,
        imbalance_trigger: Optional[float] = None,
        deadline_urgency_seconds: float = 60.0,
        baseline_window: int = 20,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.spread_trigger_bps = spread_trigger_bps
        self.volume_trigger_ratio = volume_trigger_ratio
        self.imbalance_trigger = imbalance_trigger
        self.deadline_urgency_seconds = deadline_urgency_seconds

        # Rolling baseline for volume spike detection
        self._baseline_window = baseline_window
        self._depth_history: deque[float] = deque(maxlen=baseline_window)
        self._baseline_depth: float = 0.0

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def should_send(
        self,
        parent: ParentOrder,
        state: MarketState,
        current_time: pd.Timestamp,
        last_sent: Optional[pd.Timestamp],
    ) -> tuple[bool, Optional[TimingTrigger]]:
        """
        Decide whether to send the next child order right now.

        매개변수
        ----------
        parent : ParentOrder
        state : MarketState
        current_time : pd.Timestamp
        last_sent : pd.Timestamp | None
            Timestamp of the most recent child submission.  None if no child
            has been sent yet.

        반환값
        -------
        (should_send, trigger)
            trigger is None when should_send is False.
        """
        # Priority 1: Deadline approaching
        if parent.end_time is not None:
            seconds_remaining = (parent.end_time - current_time).total_seconds()
            if seconds_remaining <= self.deadline_urgency_seconds:
                return True, TimingTrigger.DEADLINE_APPROACHING

        # Priority 2: Spread narrowed
        if self.spread_trigger_bps is not None and self._is_spread_narrow(state):
            # Respect a minimal cooldown of 1 second to avoid burst firing
            if self._cooldown_ok(last_sent, current_time, min_seconds=1.0):
                return True, TimingTrigger.SPREAD_NARROW

        # Priority 3: Volume spike
        if self.volume_trigger_ratio is not None and self._is_volume_spike(state):
            if self._cooldown_ok(last_sent, current_time, min_seconds=1.0):
                return True, TimingTrigger.VOLUME_SPIKE

        # Priority 4: Imbalance favourable
        if self.imbalance_trigger is not None and self._is_imbalance_favorable(parent, state):
            if self._cooldown_ok(last_sent, current_time, min_seconds=1.0):
                return True, TimingTrigger.IMBALANCE_FAVORABLE

        # Priority 5: Time interval elapsed (fallback)
        if self._cooldown_ok(last_sent, current_time, min_seconds=self.interval_seconds):
            return True, TimingTrigger.TIME_BASED

        return False, None

    def update_baseline(self, state: MarketState) -> None:
        """
        Update the rolling volume baseline using the current state's LOB depth.
        Should be called once per market-state update.
        """
        depth = float(state.lob.total_bid_depth + state.lob.total_ask_depth)
        self._depth_history.append(depth)
        if self._depth_history:
            self._baseline_depth = sum(self._depth_history) / len(self._depth_history)

    # ------------------------------------------------------------------
    # Trigger evaluators
    # ------------------------------------------------------------------

    def _is_spread_narrow(self, state: MarketState) -> bool:
        """Return True if current spread_bps is below the configured threshold."""
        spread_bps = state.lob.spread_bps
        if spread_bps is None or self.spread_trigger_bps is None:
            return False
        return spread_bps <= self.spread_trigger_bps

    def _is_volume_spike(self, state: MarketState) -> bool:
        """
        Return True if current LOB depth exceeds baseline by volume_trigger_ratio.
        Requires at least a few observations to form a baseline.
        """
        if self.volume_trigger_ratio is None:
            return False
        if len(self._depth_history) < max(3, self._baseline_window // 4):
            return False  # not enough history
        current_depth = float(state.lob.total_bid_depth + state.lob.total_ask_depth)
        if self._baseline_depth == 0.0:
            return False
        return current_depth >= self.volume_trigger_ratio * self._baseline_depth

    def _is_imbalance_favorable(
        self,
        parent: ParentOrder,
        state: MarketState,
    ) -> bool:
        """
        Return True when order-flow imbalance is in the favourable direction.

        Buy  orders benefit from positive imbalance (strong bid pressure
             signals upward momentum → send quickly before price rises).
        Sell orders benefit from negative imbalance (strong ask pressure
             signals downward momentum → send quickly before price falls).
        """
        if self.imbalance_trigger is None:
            return False
        imbalance = state.lob.order_imbalance
        if imbalance is None:
            return False

        from execution_planning.layer3_order.order_types import OrderSide
        if parent.side == OrderSide.BUY:
            return imbalance >= self.imbalance_trigger
        else:
            return imbalance <= -self.imbalance_trigger

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _cooldown_ok(
        last_sent: Optional[pd.Timestamp],
        current_time: pd.Timestamp,
        min_seconds: float,
    ) -> bool:
        """Return True if enough time has elapsed since the last send."""
        if last_sent is None:
            return True
        elapsed = (current_time - last_sent).total_seconds()
        return elapsed >= min_seconds
