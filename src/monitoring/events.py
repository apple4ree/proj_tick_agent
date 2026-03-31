"""
monitoring/events.py
--------------------
Frozen dataclasses for all monitoring events emitted during a backtest run.

All events share four common fields:
  event_id   : str           unique UUID for this event
  run_id     : str           identifies the backtest run
  tick_index : int           0-based tick counter within the run
  timestamp  : pd.Timestamp  wall-clock time of the market tick
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _BaseEvent:
    event_id: str
    run_id: str
    tick_index: int
    timestamp: pd.Timestamp


# ---------------------------------------------------------------------------
# Market tick
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TickStartEvent(_BaseEvent):
    """Emitted once per tick, before any order processing."""
    symbol: str
    true_mid: Optional[float]
    observed_mid: Optional[float]
    staleness_ms: float
    lob_best_bid: Optional[float]
    lob_best_ask: Optional[float]
    lob_total_bid_depth: float
    lob_total_ask_depth: float
    last_trade_price: Optional[float]
    last_trade_volume: Optional[int]


# ---------------------------------------------------------------------------
# Queue lifecycle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueueInitEvent(_BaseEvent):
    """Emitted when a child order enters the queue for the first time."""
    child_id: str
    parent_id: str
    symbol: str
    side: str                          # "BUY" | "SELL"
    order_price: Optional[float]
    queue_model: str
    queue_position_assumption: float
    initial_level_qty: float
    queue_ahead_qty_init: float


@dataclass(frozen=True)
class QueueTickEvent(_BaseEvent):
    """Emitted every tick for each queued passive order."""
    child_id: str
    parent_id: str
    symbol: str
    order_price: Optional[float]
    queue_ahead_before: float
    same_level_trade_qty: float
    prev_level_qty: float
    curr_level_qty: float
    depth_drop: float
    unexplained_depth_drop: float
    trade_advancement: float
    depth_advancement: float
    queue_ahead_after: float
    gate_passed: bool
    queue_model: str


# ---------------------------------------------------------------------------
# Order lifecycle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderSubmitEvent(_BaseEvent):
    """Emitted when register_submit_request is called for a child order."""
    child_id: str
    parent_id: str
    symbol: str
    side: str
    order_type: str
    tif: str
    qty: int
    price: Optional[float]
    submit_request_time: pd.Timestamp
    venue_arrival_time: pd.Timestamp
    ack_time: pd.Timestamp
    submit_latency_ms: float
    ack_latency_ms: float
    placement_policy: Optional[str]
    is_passive_candidate: bool


@dataclass(frozen=True)
class CancelRequestEvent(_BaseEvent):
    """Emitted when register_cancel_request is called for a child order."""
    child_id: str
    parent_id: str
    symbol: str
    cancel_requested_time: pd.Timestamp
    cancel_effective_time: pd.Timestamp
    cancel_latency_ms: float
    reason: str


# ---------------------------------------------------------------------------
# Fill lifecycle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FillAttemptEvent(_BaseEvent):
    """Emitted for every fill attempt (gated or not) on a child order."""
    child_id: str
    parent_id: str
    symbol: str
    side: str
    order_price: Optional[float]
    qty_attempted: int
    gate_passed: bool
    outcome: str   # filled | partial | no_fill_queue_blocked | no_fill_no_trade
                   # | ioc_expired | pending_arrival | cancelled


@dataclass(frozen=True)
class FillEvent(_BaseEvent):
    """Emitted for every successful (full or partial) fill."""
    child_id: str
    parent_id: str
    symbol: str
    side: str
    filled_qty: int
    matched_price_raw: float
    impacted_price: float
    arrival_mid: Optional[float]
    mid_at_fill: Optional[float]
    fee: float
    is_maker: bool
    slippage_bps: float
    impact_bps: float
    latency_ms: float
    expected_fee: float
    expected_slippage_bps: float
    fee_error_bps: float
    slippage_error_bps: float
    queue_wait_ticks: float
    queue_wait_ms: float
