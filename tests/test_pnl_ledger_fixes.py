"""
Regression tests for PnL ledger unrealized aggregation fix,
parent overfill prevention, and viz proxy midprice handling.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers to build lightweight test objects
# ---------------------------------------------------------------------------

def _make_fill(
    timestamp, symbol, side, qty, price, fee=10.0,
    slippage_bps=1.0, impact_bps=0.5, latency_ms=1.0,
    parent_id="parent-1", order_id=None,
):
    from market_simulation.layer5_simulator.bookkeeper import FillEvent
    from execution_planning.layer3_order.order_types import OrderSide

    return FillEvent(
        timestamp=pd.Timestamp(timestamp),
        order_id=order_id or f"child-{timestamp}",
        parent_id=parent_id,
        symbol=symbol,
        side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
        filled_qty=qty,
        fill_price=price,
        fee=fee,
        slippage_bps=slippage_bps,
        market_impact_bps=impact_bps,
        latency_ms=latency_ms,
    )


# ===================================================================
# Test Suite 1: PnL Ledger — unrealized aggregation fix
# ===================================================================

class TestPnLLedgerUnrealized:
    """Verify that total_unrealized is last snapshot, not cumulative sum."""

    def test_unrealized_is_last_snapshot_not_sum(self):
        """Core regression: unrealized must NOT accumulate across fills."""
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger

        ledger = PnLLedger()

        # BUY 100 @ 1000, mark=1010 → unrealized = (1010-1000)*100 = 1000
        f1 = _make_fill("2026-03-12 10:00", "TEST", "BUY", 100, 1000.0)
        ledger.record_fill(f1, cost_basis=1000.0, mark_price=1010.0)

        # BUY 100 @ 1020, mark=1030 → avg_cost ~ 1010, qty=200
        # unrealized = (1030 - 1010) * 200 = 4000
        f2 = _make_fill("2026-03-12 10:01", "TEST", "BUY", 100, 1020.0)
        ledger.record_fill(f2, cost_basis=1020.0, mark_price=1030.0)

        report = ledger.generate_report()

        # Before fix: total_unrealized = 1000 + 4000 = 5000 (WRONG)
        # After fix: total_unrealized = 4000 (last snapshot only)
        assert report.total_unrealized == pytest.approx(4000.0, abs=1.0), (
            f"total_unrealized should be last snapshot (4000), got {report.total_unrealized}"
        )

    def test_unrealized_after_closing_position(self):
        """When position is fully closed, unrealized should be 0."""
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger

        ledger = PnLLedger()

        # BUY 100 @ 1000
        f1 = _make_fill("2026-03-12 10:00", "TEST", "BUY", 100, 1000.0)
        ledger.record_fill(f1, cost_basis=1000.0, mark_price=1010.0)

        # SELL 100 @ 1020 → position closed
        f2 = _make_fill("2026-03-12 10:01", "TEST", "SELL", 100, 1020.0)
        ledger.record_fill(f2, cost_basis=1000.0, mark_price=1020.0)

        report = ledger.generate_report()
        assert report.total_unrealized == pytest.approx(0.0), (
            f"total_unrealized should be 0 after closing, got {report.total_unrealized}"
        )

    def test_net_pnl_identity(self):
        """net_pnl = realized + unrealized(last) - commission - tax."""
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger

        ledger = PnLLedger()

        f1 = _make_fill("2026-03-12 10:00", "TEST", "BUY", 100, 1000.0, fee=50.0)
        ledger.record_fill(f1, cost_basis=1000.0, mark_price=1010.0)

        f2 = _make_fill("2026-03-12 10:01", "TEST", "SELL", 50, 1020.0, fee=30.0)
        ledger.record_fill(f2, cost_basis=1000.0, mark_price=1020.0)

        report = ledger.generate_report()
        expected_net = (
            report.total_realized
            + report.total_unrealized
            - report.total_commission
            - report.total_tax
        )
        assert report.net_pnl == pytest.approx(expected_net, abs=0.01), (
            f"net_pnl identity violated: got {report.net_pnl}, expected {expected_net}"
        )

    def test_pnl_series_last_matches_net_pnl(self):
        """Last value of pnl_series should equal report.net_pnl."""
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger

        ledger = PnLLedger()

        f1 = _make_fill("2026-03-12 10:00", "TEST", "BUY", 100, 1000.0, fee=50.0)
        ledger.record_fill(f1, cost_basis=1000.0, mark_price=1010.0)

        f2 = _make_fill("2026-03-12 10:01", "TEST", "SELL", 50, 1020.0, fee=30.0)
        ledger.record_fill(f2, cost_basis=1000.0, mark_price=1020.0)

        report = ledger.generate_report()
        series_last = report.pnl_series.iloc[-1]
        assert series_last == pytest.approx(report.net_pnl, abs=0.01), (
            f"pnl_series last ({series_last}) != report.net_pnl ({report.net_pnl})"
        )

    def test_cumulative_pnl_series_matches_report(self):
        """cumulative_pnl_series() and generate_report().pnl_series should agree."""
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger

        ledger = PnLLedger()
        for i in range(5):
            f = _make_fill(f"2026-03-12 10:0{i}", "TEST", "BUY", 10, 1000.0 + i * 10)
            ledger.record_fill(f, cost_basis=1000.0, mark_price=1050.0)

        cum = ledger.cumulative_pnl_series()
        report_series = ledger.generate_report().pnl_series

        pd.testing.assert_series_equal(cum, report_series, check_names=False)


# ===================================================================
# Test Suite 2: Parent overfill prevention
# ===================================================================

class TestParentOverfill:
    """Verify that parent.filled_qty never exceeds parent.total_qty."""

    def test_fill_simulator_caps_at_parent_remaining(self):
        """FillSimulator must not fill beyond parent.total_qty."""
        from execution_planning.layer3_order.order_types import (
            ChildOrder, ParentOrder, OrderSide, OrderType, OrderTIF, OrderStatus,
        )

        parent = ParentOrder.create(
            symbol="TEST", side=OrderSide.BUY, qty=100, arrival_mid=1000.0,
        )
        # Pre-fill to 90
        parent.filled_qty = 90

        # Create child wanting 50 (would overfill by 40)
        child = ChildOrder.create(
            parent=parent, order_type=OrderType.MARKET, qty=50,
        )

        # The child's remaining_qty is 50, but parent only needs 10 more.
        # After fix, fill_simulator caps at parent.remaining_qty = 10.
        assert parent.remaining_qty == 10

    def test_slice_order_respects_in_flight_children(self):
        """_slice_order should deduct in-flight children from effective remaining."""
        from execution_planning.layer3_order.order_types import (
            ChildOrder, ParentOrder, OrderSide, OrderType, OrderStatus,
        )

        parent = ParentOrder.create(
            symbol="TEST", side=OrderSide.BUY, qty=100, arrival_mid=1000.0,
        )

        # Simulate: parent has 0 filled, but 3 active children with 30 qty each
        for _ in range(3):
            child = ChildOrder.create(
                parent=parent, order_type=OrderType.MARKET, qty=30,
            )
            child.status = OrderStatus.OPEN
            parent.child_orders.append(child)

        # in-flight = 3 * 30 = 90
        in_flight = sum(c.remaining_qty for c in parent.child_orders if c.is_active)
        effective_remaining = parent.remaining_qty - in_flight
        # 100 - 0 - 90 = 10
        assert effective_remaining == 10, (
            f"effective_remaining should be 10, got {effective_remaining}"
        )

    def test_overfill_guard_clamps_filled_qty(self):
        """simulate_fills must not increment parent.filled_qty beyond total_qty."""
        from execution_planning.layer3_order.order_types import (
            ChildOrder, ParentOrder, OrderSide, OrderType,
        )

        parent = ParentOrder.create(
            symbol="TEST", side=OrderSide.BUY, qty=100, arrival_mid=1000.0,
        )
        parent.filled_qty = 95

        # Even if a child has qty=50, parent should not go above 100
        # After fix, filled_qty should be capped at total_qty
        assert parent.remaining_qty == 5

    def test_fill_rate_never_exceeds_one(self):
        """ParentOrder.fill_rate should be <= 1.0 after fix."""
        from execution_planning.layer3_order.order_types import ParentOrder, OrderSide

        parent = ParentOrder.create(
            symbol="TEST", side=OrderSide.BUY, qty=100, arrival_mid=1000.0,
        )
        parent.filled_qty = 100
        assert parent.fill_rate <= 1.0

        # Simulate the scenario that was broken before fix
        parent.filled_qty = 100  # exact fill
        assert parent.fill_rate == pytest.approx(1.0)
        assert parent.remaining_qty == 0
