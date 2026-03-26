"""
order_types.py
--------------
Core order data types for Layer 3.
Defines ParentOrder, ChildOrder, and all associated enumerations.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    LIMIT_IOC = "LIMIT_IOC"
    LIMIT_FOK = "LIMIT_FOK"
    PEG_MID = "PEG_MID"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderTIF(Enum):
    """Time-In-Force instructions."""
    DAY = "DAY"
    GTC = "GTC"       # Good Till Cancel
    IOC = "IOC"       # Immediate Or Cancel
    FOK = "FOK"       # Fill Or Kill
    GTX = "GTX"       # Good Till Crossing (post-only)


class OrderStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"   # alias kept for backwards compat
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class ChildOrder:
    """
    A single child (slice) order sent to the exchange.
    Derived from a ParentOrder by an execution policy.
    """
    parent_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: int
    price: Optional[float] = None          # None for MARKET orders
    tif: OrderTIF = OrderTIF.DAY
    child_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Backwards-compat alias; child_id is the canonical field
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: Optional[float] = None
    submitted_time: Optional[pd.Timestamp] = None
    # Legacy alias
    submit_time: Optional[pd.Timestamp] = None
    fill_time: Optional[pd.Timestamp] = None
    cancel_time: Optional[pd.Timestamp] = None
    arrival_mid: Optional[float] = None    # mid price at time of submission
    # Queue-position approximation metadata (passive orders only)
    queue_ahead_qty: float = 0.0
    queue_enter_ts: Optional[pd.Timestamp] = None
    queue_price: Optional[float] = None
    queue_side: Optional[str] = None
    queue_initialized: bool = False
    queue_model: Optional[str] = None
    initial_level_qty: float = 0.0
    queue_last_level_qty: float = 0.0
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure child_id and order_id are consistent when not supplied
        if self.child_id == self.order_id:
            pass  # both auto-generated, just different UUIDs — that is fine
        # Keep submitted_time / submit_time in sync
        if self.submitted_time is not None and self.submit_time is None:
            self.submit_time = self.submitted_time
        elif self.submit_time is not None and self.submitted_time is None:
            self.submitted_time = self.submit_time

        if self.queue_price is None and self.price is not None:
            self.queue_price = self.price
        if self.queue_side is None:
            self.queue_side = self.side.value

    @property
    def remaining_qty(self) -> int:
        return self.qty - self.filled_qty

    @property
    def is_active(self) -> bool:
        return self.status in (
            OrderStatus.OPEN,
            OrderStatus.PARTIAL,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PENDING,
        )

    @property
    def notional(self) -> float:
        p = self.price or 0.0
        return self.qty * p

    @property
    def is_complete(self) -> bool:
        return self.filled_qty >= self.qty

    @classmethod
    def create(
        cls,
        parent: ParentOrder,
        order_type: OrderType,
        qty: int,
        price: Optional[float] = None,
        tif: OrderTIF = OrderTIF.DAY,
        submitted_time: Optional[pd.Timestamp] = None,
        **kwargs,
    ) -> ChildOrder:
        """
        Factory classmethod for creating a ChildOrder from a ParentOrder.

        매개변수
        ----------
        parent : ParentOrder
            The parent order this child belongs to.
        order_type : OrderType
            Type of the child order.
        qty : int
            Quantity for this child slice.
        price : float | None
            Limit price (None for MARKET orders).
        tif : OrderTIF
            Time-in-force for the child.
        submitted_time : pd.Timestamp | None
            Submission time.

        반환값
        -------
        ChildOrder
        """
        child_id = str(uuid.uuid4())
        return cls(
            child_id=child_id,
            order_id=child_id,
            parent_id=parent.order_id,
            symbol=parent.symbol,
            side=parent.side,
            order_type=order_type,
            qty=qty,
            price=price,
            tif=tif,
            submitted_time=submitted_time,
            submit_time=submitted_time,
            status=OrderStatus.PENDING,
            **kwargs,
        )


@dataclass
class ParentOrder:
    """
    A high-level parent order representing trading intent.
    Sliced into multiple ChildOrders by the execution policy.
    """
    symbol: str
    side: OrderSide
    total_qty: int
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    limit_price: Optional[float] = None    # None = no price limit on parent
    start_time: Optional[pd.Timestamp] = None
    end_time: Optional[pd.Timestamp] = None    # execution deadline
    urgency: float = 0.5                   # 0 = patient, 1 = aggressive
    max_participation_rate: float = 0.10   # max % of market volume
    max_slippage_bps: float = 50.0         # max acceptable slippage in bps
    constraints: dict = field(default_factory=dict)
    arrival_mid: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: Optional[float] = None
    child_orders: list[ChildOrder] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def remaining_qty(self) -> int:
        return self.total_qty - self.filled_qty

    @property
    def fill_rate(self) -> float:
        if self.total_qty == 0:
            return 0.0
        return self.filled_qty / self.total_qty

    @property
    def is_complete(self) -> bool:
        return self.filled_qty >= self.total_qty

    @property
    def notional(self) -> float:
        if self.avg_fill_price is not None:
            return self.filled_qty * self.avg_fill_price
        if self.limit_price is not None:
            return self.total_qty * self.limit_price
        return 0.0

    @classmethod
    def create(
        cls,
        symbol: str,
        side: OrderSide,
        qty: int,
        urgency: float = 0.5,
        start_time: Optional[pd.Timestamp] = None,
        end_time: Optional[pd.Timestamp] = None,
        max_participation_rate: float = 0.10,
        max_slippage_bps: float = 50.0,
        constraints: Optional[dict] = None,
        arrival_mid: Optional[float] = None,
        **meta,
    ) -> ParentOrder:
        """
        Factory classmethod with auto-generated UUID order_id.

        매개변수
        ----------
        symbol : str
        side : OrderSide
        qty : int
        urgency : float
        start_time : pd.Timestamp | None
        end_time : pd.Timestamp | None
        max_participation_rate : float
        max_slippage_bps : float
        constraints : dict | None
        arrival_mid : float | None
        **meta
            Extra keyword arguments stored in ParentOrder.meta.

        반환값
        -------
        ParentOrder
        """
        return cls(
            order_id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            total_qty=qty,
            urgency=urgency,
            start_time=start_time or pd.Timestamp.utcnow(),
            end_time=end_time,
            max_participation_rate=max_participation_rate,
            max_slippage_bps=max_slippage_bps,
            constraints=constraints or {},
            arrival_mid=arrival_mid,
            meta=dict(meta),
        )
