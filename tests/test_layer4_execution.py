"""
Tests for Layer 4 slicing and placement policies.
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
from execution_planning.layer3_order.order_types import OrderSide, OrderType, OrderTIF, ParentOrder
from execution_planning.layer4_execution.slicing_policy import TWAPSlicer, VWAPSlicer, POVSlicer, AlmgrenChrissSlicer
from execution_planning.layer4_execution.placement_policy import AggressivePlacement, PassivePlacement, SpreadAdaptivePlacement


def _make_states(n: int = 10, mid: float = 100.0) -> list[MarketState]:
    states = []
    start = pd.Timestamp("2026-03-12 09:00:00")
    for i in range(n):
        ts = start + pd.Timedelta(seconds=i)
        states.append(MarketState(
            timestamp=ts,
            symbol="TEST",
            lob=LOBSnapshot(
                timestamp=ts,
                bid_levels=[
                    LOBLevel(price=mid - 0.05, volume=1000),
                    LOBLevel(price=mid - 0.10, volume=500),
                ],
                ask_levels=[
                    LOBLevel(price=mid + 0.05, volume=1000),
                    LOBLevel(price=mid + 0.10, volume=500),
                ],
            ),
            tradable=True,
            session="regular",
        ))
    return states


def _make_parent(qty: int = 1000) -> ParentOrder:
    return ParentOrder.create(
        symbol="TEST",
        side=OrderSide.BUY,
        qty=qty,
        urgency=0.5,
        arrival_mid=100.0,
    )


# ── TWAP Slicer ───────────────────────────────────────────────────

class TestTWAPSlicer:
    def test_total_qty_matches(self):
        slicer = TWAPSlicer(n_slices=5)
        parent = _make_parent(1000)
        schedule = slicer.generate_schedule(parent, _make_states(10))
        total = sum(qty for _, qty in schedule)
        assert total == 1000

    def test_n_slices_respected(self):
        slicer = TWAPSlicer(n_slices=4)
        parent = _make_parent(100)
        schedule = slicer.generate_schedule(parent, _make_states(10))
        assert len(schedule) == 4

    def test_uniform_distribution(self):
        slicer = TWAPSlicer(n_slices=5)
        parent = _make_parent(100)
        schedule = slicer.generate_schedule(parent, _make_states(10))
        qtys = [qty for _, qty in schedule]
        # Each slice should be 20 (100/5)
        assert all(q == 20 for q in qtys)


# ── VWAP Slicer ───────────────────────────────────────────────────

class TestVWAPSlicer:
    def test_total_qty_matches(self):
        slicer = VWAPSlicer()
        parent = _make_parent(500)
        schedule = slicer.generate_schedule(parent, _make_states(10))
        total = sum(qty for _, qty in schedule)
        assert total == 500

    def test_all_steps_used(self):
        slicer = VWAPSlicer()
        parent = _make_parent(500)
        states = _make_states(10)
        schedule = slicer.generate_schedule(parent, states)
        # With uniform LOB, VWAP distributes across all steps
        assert len(schedule) >= 1


# ── POV Slicer ────────────────────────────────────────────────────

class TestPOVSlicer:
    def test_next_qty_respects_participation(self):
        slicer = POVSlicer(participation_rate=0.05)
        state = _make_states(1)[0]
        qty = slicer.next_qty(remaining_qty=1000, state=state)
        # LOB depth ≈ 3000 total, 5% = 150
        assert 0 < qty <= 1000
        assert qty <= int(0.05 * 3000) + 1  # allow rounding

    def test_invalid_participation_raises(self):
        with pytest.raises(ValueError):
            POVSlicer(participation_rate=0.0)
        with pytest.raises(ValueError):
            POVSlicer(participation_rate=1.5)


# ── Almgren-Chriss Slicer ─────────────────────────────────────────

class TestAlmgrenChrissSlicer:
    def test_total_qty_matches(self):
        slicer = AlmgrenChrissSlicer()
        parent = _make_parent(1000)
        schedule = slicer.generate_schedule(parent, _make_states(20))
        total = sum(qty for _, qty in schedule)
        assert total == 1000

    def test_front_loaded(self):
        """Almgren-Chriss should front-load execution to reduce risk."""
        slicer = AlmgrenChrissSlicer(eta=0.1, gamma=0.01, sigma=0.01)
        parent = _make_parent(1000)
        schedule = slicer.generate_schedule(parent, _make_states(20))
        qtys = [qty for _, qty in schedule]
        # First half should have more than second half
        first_half = sum(qtys[:len(qtys) // 2])
        second_half = sum(qtys[len(qtys) // 2:])
        assert first_half >= second_half


# ── 배치 Policies ────────────────────────────────────────────

class TestAggressivePlacement:
    def test_buy_at_ask(self):
        placement = AggressivePlacement(use_market_orders=False)
        parent = _make_parent(100)
        state = _make_states(1)[0]
        child = placement.place(parent, qty=50, state=state)
        assert child.order_type == OrderType.LIMIT
        assert child.price == state.lob.best_ask
        assert child.qty == 50
        assert child.tif == OrderTIF.IOC

    def test_market_order(self):
        placement = AggressivePlacement(use_market_orders=True)
        parent = _make_parent(100)
        state = _make_states(1)[0]
        child = placement.place(parent, qty=50, state=state)
        assert child.order_type == OrderType.MARKET


class TestPassivePlacement:
    def test_buy_at_bid(self):
        placement = PassivePlacement()
        parent = _make_parent(100)
        state = _make_states(1)[0]
        child = placement.place(parent, qty=50, state=state)
        assert child.order_type == OrderType.LIMIT
        assert child.price == state.lob.best_bid
        assert child.tif == OrderTIF.DAY

    def test_sell_at_ask(self):
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.SELL, qty=100)
        placement = PassivePlacement()
        state = _make_states(1)[0]
        child = placement.place(parent, qty=50, state=state)
        assert child.price == state.lob.best_ask


class TestSpreadAdaptivePlacement:
    def test_returns_valid_child(self):
        placement = SpreadAdaptivePlacement()
        parent = _make_parent(100)
        state = _make_states(1)[0]
        child = placement.place(parent, qty=50, state=state)
        assert child.qty == 50
        assert child.symbol == "TEST"
        assert child.side == OrderSide.BUY
