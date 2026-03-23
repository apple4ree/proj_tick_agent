"""
order_scheduler.py
------------------
스케줄링 힌트s and order scheduler for Layer 3.

클래스
-------
SchedulingHint   - Metadata bundle guiding execution layer decisions
OrderScheduler   - Produces SchedulingHint and TWAP-style time slices
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState

from .order_types import ParentOrder


# ---------------------------------------------------------------------------
# 레거시 긴급도 enum(하위 호환성을 위해 유지)
# ---------------------------------------------------------------------------

class SchedulingUrgency(Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# 스케줄링 힌트
# ---------------------------------------------------------------------------

@dataclass
class SchedulingHint:
    """
    Metadata hint passed from the order scheduler to the execution layer.

    Guides slicing, timing, and placement decisions.

    속성
    ----------
    participation_rate : float
        Target fraction of market volume to participate at (POV rate).
    urgency : float
        Numeric urgency in [0, 1] (0 = patient, 1 = aggressive).
    max_slippage_bps : float
        Maximum acceptable slippage in basis points.
    preferred_windows : list[tuple[pd.Timestamp, pd.Timestamp]]
        Preferred execution time windows.
    avoid_windows : list[tuple[pd.Timestamp, pd.Timestamp]]
        Time windows to avoid (e.g. near open/close).
    algo : str
        Suggested execution algorithm: 'TWAP', 'VWAP', 'POV', 'IS'.
    """

    participation_rate: float = 0.10
    urgency: float = 0.5
    max_slippage_bps: float = 50.0
    preferred_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)
    avoid_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)
    algo: str = "TWAP"

    # Legacy fields preserved for backwards compatibility
    max_participation_rate: float = 0.10
    preferred_n_slices: Optional[int] = None
    preferred_interval_seconds: float = 30.0
    allow_market_orders: bool = False
    deadline: Optional[pd.Timestamp] = None
    notes: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def is_urgent(self) -> bool:
        """True when urgency exceeds the high-urgency threshold (0.7)."""
        return self.urgency >= 0.7

    def __repr__(self) -> str:
        return (
            f"SchedulingHint(algo={self.algo!r}, urgency={self.urgency:.2f}, "
            f"participation={self.participation_rate:.2%}, "
            f"max_slippage={self.max_slippage_bps:.1f}bps)"
        )


# ---------------------------------------------------------------------------
# Order scheduler
# ---------------------------------------------------------------------------

class OrderScheduler:
    """
    Produces SchedulingHint objects and TWAP-style time slices for a ParentOrder.

    매개변수
    ----------
    avoid_open_min : int
        Minutes after market open to avoid (default 5).
    avoid_close_min : int
        Minutes before market close to avoid (default 10).
    default_algo : str
        Default execution algorithm suggestion (default 'TWAP').
    open_hour : int
        Session open hour (default 9, KRX).
    open_minute : int
        Session open minute (default 0).
    close_hour : int
        Session close hour (default 15, KRX).
    close_minute : int
        Session close minute (default 30).
    """

    _HIGH_URGENCY_THRESHOLD: float = 0.7
    _POV_BASE: float = 0.05          # base participation rate for patient orders
    _POV_MAX: float = 0.25           # max participation rate for aggressive orders

    def __init__(
        self,
        avoid_open_min: int = 5,
        avoid_close_min: int = 10,
        default_algo: str = "TWAP",
        open_hour: int = 9,
        open_minute: int = 0,
        close_hour: int = 15,
        close_minute: int = 30,
    ) -> None:
        self._avoid_open_min = avoid_open_min
        self._avoid_close_min = avoid_close_min
        self._default_algo = default_algo
        self._open_hour = open_hour
        self._open_minute = open_minute
        self._close_hour = close_hour
        self._close_minute = close_minute

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    def create_hint(
        self,
        parent: ParentOrder,
        state: MarketState,
    ) -> SchedulingHint:
        """
        Build a SchedulingHint for the given parent order and market state.

        매개변수
        ----------
        parent : ParentOrder
        state : MarketState

        반환값
        -------
        SchedulingHint
        """
        urgency = parent.urgency
        participation_rate = self._compute_participation_rate(parent, state)
        algo = self._select_algo(parent, state)
        avoid_windows = self._build_avoid_windows(state)

        # Preferred windows = complement of avoid windows within session
        preferred_windows = self._build_preferred_windows(state, avoid_windows)

        # Deadline carried from parent
        deadline = parent.end_time

        return SchedulingHint(
            participation_rate=participation_rate,
            urgency=urgency,
            max_slippage_bps=parent.max_slippage_bps,
            preferred_windows=preferred_windows,
            avoid_windows=avoid_windows,
            algo=algo,
            max_participation_rate=parent.max_participation_rate,
            allow_market_orders=(urgency > self._HIGH_URGENCY_THRESHOLD),
            deadline=deadline,
            notes=f"order_id={parent.order_id}",
        )

    def split_schedule(
        self,
        parent: ParentOrder,
        hint: SchedulingHint,
        n_slices: int,
    ) -> list[tuple[pd.Timestamp, int]]:
        """
        Split a parent order into TWAP-style time/quantity slices.

        매개변수
        ----------
        parent : ParentOrder
        hint : SchedulingHint
        n_slices : int
            Number of equal slices to create.

        반환값
        -------
        list[tuple[pd.Timestamp, int]]
            List of (scheduled_time, qty) tuples.
            Remaining shares from integer rounding are appended to the last slice.
        """
        if n_slices <= 0:
            raise ValueError(f"n_slices must be positive, got {n_slices}")

        remaining_qty = parent.remaining_qty
        if remaining_qty <= 0:
            return []

        start = parent.start_time or pd.Timestamp.utcnow()
        end = parent.end_time

        if end is None or end <= start:
            # 마감 시각을 모르면 지금 전량 스케줄링한다
            return [(start, remaining_qty)]

        total_seconds = (end - start).total_seconds()
        interval = pd.Timedelta(seconds=total_seconds / n_slices)

        base_qty = remaining_qty // n_slices
        remainder = remaining_qty % n_slices

        slices: list[tuple[pd.Timestamp, int]] = []
        for i in range(n_slices):
            slice_time = start + i * interval
            slice_qty = base_qty + (1 if i < remainder else 0)

            # 회피 구간에 걸리는 슬라이스는 건너뛴다
            if self._in_avoid_window(slice_time, hint.avoid_windows):
                # 수량을 다음 유효 슬라이스로 넘겨 누적한다
                if slices:
                    t, q = slices[-1]
                    slices[-1] = (t, q + slice_qty)
                else:
                    # 이전 슬라이스가 없으면 첫 번째 선호 시간대를 사용한다
                    first_preferred = (
                        hint.preferred_windows[0][0]
                        if hint.preferred_windows
                        else slice_time
                    )
                    slices.append((first_preferred, slice_qty))
                continue

            if slice_qty > 0:
                slices.append((slice_time, slice_qty))

        return slices

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    def _compute_participation_rate(
        self,
        parent: ParentOrder,
        state: MarketState,
    ) -> float:
        """Scale participation rate linearly with urgency."""
        rate = self._POV_BASE + parent.urgency * (self._POV_MAX - self._POV_BASE)
        # Respect parent's own cap
        return min(rate, parent.max_participation_rate)

    def _select_algo(self, parent: ParentOrder, state: MarketState) -> str:
        """Select execution algorithm based on urgency and order size."""
        urgency = parent.urgency
        if urgency > self._HIGH_URGENCY_THRESHOLD:
            return "IS"  # Implementation Shortfall: minimize delay cost
        # Large orders → VWAP for volume-adaptive slicing
        mid = state.lob.mid_price or 1.0
        notional = parent.total_qty * mid
        if notional > 1e7:
            return "VWAP"
        return self._default_algo

    def _build_avoid_windows(
        self,
        state: MarketState,
    ) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        """Build avoid windows around session open and close."""
        ts = state.timestamp
        avoid: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        try:
            tz = ts.tzinfo
            session_open = pd.Timestamp(
                year=ts.year, month=ts.month, day=ts.day,
                hour=self._open_hour, minute=self._open_minute,
                tzinfo=tz,
            )
            session_close = pd.Timestamp(
                year=ts.year, month=ts.month, day=ts.day,
                hour=self._close_hour, minute=self._close_minute,
                tzinfo=tz,
            )
            open_end = session_open + pd.Timedelta(minutes=self._avoid_open_min)
            close_start = session_close - pd.Timedelta(minutes=self._avoid_close_min)

            avoid.append((session_open, open_end))
            avoid.append((close_start, session_close))
        except (AttributeError, TypeError, ValueError):
            pass
        return avoid

    def _build_preferred_windows(
        self,
        state: MarketState,
        avoid_windows: list[tuple[pd.Timestamp, pd.Timestamp]],
    ) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        """Return preferred windows as session span minus avoid windows."""
        try:
            ts = state.timestamp
            tz = ts.tzinfo
            session_open = pd.Timestamp(
                year=ts.year, month=ts.month, day=ts.day,
                hour=self._open_hour, minute=self._open_minute,
                tzinfo=tz,
            )
            session_close = pd.Timestamp(
                year=ts.year, month=ts.month, day=ts.day,
                hour=self._close_hour, minute=self._close_minute,
                tzinfo=tz,
            )
            if not avoid_windows:
                return [(session_open, session_close)]

            # Build preferred as complement: session minus avoid windows
            avoid_sorted = sorted(avoid_windows, key=lambda w: w[0])
            preferred: list[tuple[pd.Timestamp, pd.Timestamp]] = []
            cursor = session_open
            for avoid_start, avoid_end in avoid_sorted:
                if cursor < avoid_start:
                    preferred.append((cursor, avoid_start))
                cursor = max(cursor, avoid_end)
            if cursor < session_close:
                preferred.append((cursor, session_close))
            return preferred
        except (AttributeError, TypeError, ValueError):
            return []

    @staticmethod
    def _in_avoid_window(
        ts: pd.Timestamp,
        avoid_windows: list[tuple[pd.Timestamp, pd.Timestamp]],
    ) -> bool:
        """Return True when ts falls inside any avoid window."""
        for start, end in avoid_windows:
            if start <= ts <= end:
                return True
        return False
