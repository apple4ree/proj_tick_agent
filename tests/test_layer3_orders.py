"""
Tests for Layer 3 order types and delta computation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from execution_planning.layer3_order.order_types import (
    ChildOrder, OrderSide, OrderStatus, OrderTIF, OrderType, ParentOrder,
)
from execution_planning.layer3_order.delta_compute import DeltaComputer


def _make_state(mid: float = 100.0) -> MarketState:
    ts = pd.Timestamp("2026-03-12 09:30:00")
    half_spread = 0.05
    return MarketState(
        timestamp=ts,
        symbol="TEST",
        lob=LOBSnapshot(
            timestamp=ts,
            bid_levels=[LOBLevel(price=mid - half_spread, volume=1000)],
            ask_levels=[LOBLevel(price=mid + half_spread, volume=1000)],
        ),
        tradable=True,
        session="regular",
    )


# ── ParentOrder ───────────────────────────────────────────────────

class TestParentOrder:
    def test_create_factory(self):
        p = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        assert p.symbol == "TEST"
        assert p.side == OrderSide.BUY
        assert p.total_qty == 100
        assert p.remaining_qty == 100
        assert p.filled_qty == 0
        assert p.status == OrderStatus.PENDING
        assert len(p.order_id) > 0

    def test_fill_rate_partial(self):
        p = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=200)
        p.filled_qty = 50
        assert p.fill_rate == pytest.approx(0.25)

    def test_fill_rate_zero_qty(self):
        p = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=0)
        assert p.fill_rate == 0.0

    def test_is_complete(self):
        p = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        assert not p.is_complete
        p.filled_qty = 100
        assert p.is_complete

    def test_urgency_passed(self):
        p = ParentOrder.create(symbol="TEST", side=OrderSide.SELL, qty=50, urgency=0.9)
        assert p.urgency == 0.9


# ── ChildOrder ────────────────────────────────────────────────────

class TestChildOrder:
    def test_create_from_parent(self):
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        child = ChildOrder.create(
            parent=parent,
            order_type=OrderType.LIMIT,
            qty=50,
            price=100.0,
            tif=OrderTIF.DAY,
        )
        assert child.parent_id == parent.order_id
        assert child.symbol == "TEST"
        assert child.side == OrderSide.BUY
        assert child.qty == 50
        assert child.price == 100.0
        assert child.remaining_qty == 50
        assert child.is_active  # PENDING is active

    def test_remaining_qty(self):
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        child = ChildOrder.create(parent=parent, order_type=OrderType.LIMIT, qty=100, price=100.0)
        child.filled_qty = 40
        assert child.remaining_qty == 60

    def test_is_complete(self):
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        child = ChildOrder.create(parent=parent, order_type=OrderType.LIMIT, qty=50, price=100.0)
        assert not child.is_complete
        child.filled_qty = 50
        assert child.is_complete

    def test_is_active_statuses(self):
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        child = ChildOrder.create(parent=parent, order_type=OrderType.LIMIT, qty=50, price=100.0)

        for status in [OrderStatus.OPEN, OrderStatus.PARTIAL, OrderStatus.PENDING]:
            child.status = status
            assert child.is_active, f"{status} should be active"

        for status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]:
            child.status = status
            assert not child.is_active, f"{status} should not be active"


# ── DeltaComputer ─────────────────────────────────────────────────

class TestDeltaComputer:
    def test_buy_delta(self):
        from execution_planning.layer2_position.target_builder import TargetPosition
        dc = DeltaComputer()
        ts = pd.Timestamp("2026-03-12 09:30:00")
        target = TargetPosition(timestamp=ts, targets={"TEST": 100}, signal_ref=None)
        deltas = dc.compute(target, current_positions={})
        assert deltas == {"TEST": 100}

    def test_sell_delta(self):
        from execution_planning.layer2_position.target_builder import TargetPosition
        dc = DeltaComputer()
        ts = pd.Timestamp("2026-03-12 09:30:00")
        target = TargetPosition(timestamp=ts, targets={"TEST": 0}, signal_ref=None)
        deltas = dc.compute(target, current_positions={"TEST": 50})
        assert deltas == {"TEST": -50}

    def test_no_change(self):
        from execution_planning.layer2_position.target_builder import TargetPosition
        dc = DeltaComputer()
        ts = pd.Timestamp("2026-03-12 09:30:00")
        target = TargetPosition(timestamp=ts, targets={"TEST": 100}, signal_ref=None)
        deltas = dc.compute(target, current_positions={"TEST": 100})
        assert deltas == {}

    def test_to_parent_order_buy(self):
        dc = DeltaComputer()
        state = _make_state()
        parent = dc.to_parent_order("TEST", delta_qty=50, urgency=0.8, state=state)
        assert parent.side == OrderSide.BUY
        assert parent.total_qty == 50
        assert parent.symbol == "TEST"

    def test_to_parent_order_sell(self):
        dc = DeltaComputer()
        state = _make_state()
        parent = dc.to_parent_order("TEST", delta_qty=-30, urgency=0.5, state=state)
        assert parent.side == OrderSide.SELL
        assert parent.total_qty == 30
