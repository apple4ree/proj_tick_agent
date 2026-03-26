"""
Tests for bidirectional (long/short) PnL accounting.

Covers:
1. Long open → close (baseline regression)
2. Short open → cover
3. Short-only (realized=0 until covered)
4. Mixed long/short sequence
5. Position flip (long → short in one fill)
6. Bookkeeper FIFO for shorts
7. Aggregation regression (report/series)
"""
from __future__ import annotations

import pandas as pd
import pytest

from execution_planning.layer3_order.order_types import OrderSide
from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger
from market_simulation.layer5_simulator.bookkeeper import Bookkeeper, FillEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fill(
    ts: str,
    symbol: str,
    side: str,
    qty: int,
    price: float,
    fee: float = 0.0,
) -> FillEvent:
    return FillEvent(
        timestamp=pd.Timestamp(ts),
        order_id=f"o-{ts}",
        parent_id="p-1",
        symbol=symbol,
        side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
        filled_qty=qty,
        fill_price=price,
        fee=fee,
        slippage_bps=0.0,
        market_impact_bps=0.0,
        latency_ms=0.0,
    )


# ===================================================================
# 1. Long open → close  (baseline regression)
# ===================================================================

class TestLongOpenClose:
    """Basic long-only flow must still work correctly."""

    def test_buy_then_sell_realized(self):
        ledger = PnLLedger()
        f1 = _fill("2026-01-01 10:00", "A", "BUY", 100, 1000.0)
        e1 = ledger.record_fill(f1, cost_basis=1000.0, mark_price=1010.0)
        assert e1.realized_pnl == 0.0
        assert e1.unrealized_pnl == pytest.approx(1000.0)  # (1010-1000)*100

        f2 = _fill("2026-01-01 10:01", "A", "SELL", 100, 1020.0)
        e2 = ledger.record_fill(f2, cost_basis=1000.0, mark_price=1020.0)
        assert e2.realized_pnl == pytest.approx(2000.0)  # (1020-1000)*100
        assert e2.unrealized_pnl == 0.0

    def test_partial_close(self):
        ledger = PnLLedger()
        ledger.record_fill(_fill("2026-01-01 10:00", "A", "BUY", 100, 1000.0),
                           cost_basis=0.0, mark_price=1000.0)
        e = ledger.record_fill(_fill("2026-01-01 10:01", "A", "SELL", 40, 1050.0),
                               cost_basis=1000.0, mark_price=1050.0)
        assert e.realized_pnl == pytest.approx(2000.0)  # (1050-1000)*40
        # Remaining 60 @ avg 1000, mark 1050 → unrealized = 3000
        assert e.unrealized_pnl == pytest.approx(3000.0)

    def test_position_tracked_correctly(self):
        ledger = PnLLedger()
        ledger.record_fill(_fill("2026-01-01 10:00", "A", "BUY", 100, 1000.0),
                           cost_basis=0.0, mark_price=1000.0)
        _, qty = ledger._open_positions["A"]
        assert qty == 100

        ledger.record_fill(_fill("2026-01-01 10:01", "A", "SELL", 100, 1020.0),
                           cost_basis=1000.0, mark_price=1020.0)
        _, qty = ledger._open_positions["A"]
        assert qty == 0


# ===================================================================
# 2. Short open → cover
# ===================================================================

class TestShortOpenCover:
    """Short sell then buy-to-cover must compute realized correctly."""

    def test_short_sell_realized_zero(self):
        """Opening a short must produce zero realized PnL."""
        ledger = PnLLedger()
        f = _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0)
        e = ledger.record_fill(f, cost_basis=0.0, mark_price=50000.0)
        assert e.realized_pnl == 0.0

    def test_short_position_is_negative(self):
        ledger = PnLLedger()
        ledger.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
                           cost_basis=0.0, mark_price=50000.0)
        _, qty = ledger._open_positions["A"]
        assert qty == -100

    def test_short_unrealized_when_price_drops(self):
        """Short at 50000, mark at 49000 → unrealized profit."""
        ledger = PnLLedger()
        e = ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=49000.0,
        )
        # (49000 - 50000) * (-100) = 100000 profit
        assert e.unrealized_pnl == pytest.approx(100_000.0)

    def test_short_unrealized_when_price_rises(self):
        """Short at 50000, mark at 51000 → unrealized loss."""
        ledger = PnLLedger()
        e = ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=51000.0,
        )
        # (51000 - 50000) * (-100) = -100000 loss
        assert e.unrealized_pnl == pytest.approx(-100_000.0)

    def test_cover_realizes_profit(self):
        """Sell short at 50000, cover at 49000 → realized profit = 100000."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        e = ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "BUY", 100, 49000.0),
            cost_basis=0.0, mark_price=49000.0,
        )
        # realized = (50000 - 49000) * 100 = 100000
        assert e.realized_pnl == pytest.approx(100_000.0)
        assert e.unrealized_pnl == 0.0

    def test_cover_realizes_loss(self):
        """Sell short at 50000, cover at 51000 → realized loss = -100000."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        e = ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "BUY", 100, 51000.0),
            cost_basis=0.0, mark_price=51000.0,
        )
        assert e.realized_pnl == pytest.approx(-100_000.0)
        assert e.unrealized_pnl == 0.0

    def test_partial_cover(self):
        """Cover only half the short position."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        e = ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "BUY", 50, 49000.0),
            cost_basis=0.0, mark_price=49000.0,
        )
        # realized = (50000 - 49000) * 50 = 50000
        assert e.realized_pnl == pytest.approx(50_000.0)
        # remaining short 50 @ avg 50000, mark 49000
        # unrealized = (49000 - 50000) * (-50) = 50000
        assert e.unrealized_pnl == pytest.approx(50_000.0)
        _, qty = ledger._open_positions["A"]
        assert qty == -50


# ===================================================================
# 3. Short-only (realized=0 until covered)
# ===================================================================

class TestShortOnlyNoPnL:
    """Multiple short sells without cover: realized must stay 0."""

    def test_multiple_short_entries_no_realized(self):
        ledger = PnLLedger()
        for i in range(5):
            e = ledger.record_fill(
                _fill(f"2026-01-01 10:0{i}", "A", "SELL", 20, 50000.0 + i * 100),
                cost_basis=0.0, mark_price=50500.0,
            )
            assert e.realized_pnl == 0.0

        # Position should be -100
        _, qty = ledger._open_positions["A"]
        assert qty == -100

    def test_report_total_realized_zero_for_short_only(self):
        """Report total_realized must be 0 when only shorts, no covers."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        report = ledger.generate_report()
        assert report.total_realized == 0.0


# ===================================================================
# 4. Mixed long/short sequence
# ===================================================================

class TestMixedSequence:
    """Long trade, then short trade on the same symbol."""

    def test_long_close_then_short_open(self):
        ledger = PnLLedger()
        # BUY 100 @ 1000
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "BUY", 100, 1000.0),
            cost_basis=0.0, mark_price=1000.0,
        )
        # SELL 100 @ 1100 → close long, realized = 10000
        e2 = ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "SELL", 100, 1100.0),
            cost_basis=1000.0, mark_price=1100.0,
        )
        assert e2.realized_pnl == pytest.approx(10_000.0)
        _, qty = ledger._open_positions["A"]
        assert qty == 0

        # SELL 100 @ 1100 → open short, realized = 0
        e3 = ledger.record_fill(
            _fill("2026-01-01 10:02", "A", "SELL", 100, 1100.0),
            cost_basis=0.0, mark_price=1050.0,
        )
        assert e3.realized_pnl == 0.0
        _, qty = ledger._open_positions["A"]
        assert qty == -100

        # BUY 100 @ 1050 → cover short, realized = (1100-1050)*100 = 5000
        e4 = ledger.record_fill(
            _fill("2026-01-01 10:03", "A", "BUY", 100, 1050.0),
            cost_basis=0.0, mark_price=1050.0,
        )
        assert e4.realized_pnl == pytest.approx(5_000.0)

        report = ledger.generate_report()
        assert report.total_realized == pytest.approx(15_000.0)

    def test_weighted_avg_for_adding_to_short(self):
        """Two short entries at different prices → weighted avg."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 60, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "SELL", 40, 51000.0),
            cost_basis=0.0, mark_price=50500.0,
        )
        avg, qty = ledger._open_positions["A"]
        assert qty == -100
        # Weighted avg: (50000*60 + 51000*40) / 100 = 50400
        assert avg == pytest.approx(50400.0)


# ===================================================================
# 5. Position flip (long → short in one fill)
# ===================================================================

class TestPositionFlip:
    """Sell more than long position → close long + open short in one fill."""

    def test_long_to_short_flip(self):
        ledger = PnLLedger()
        # BUY 100 @ 1000
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "BUY", 100, 1000.0),
            cost_basis=0.0, mark_price=1000.0,
        )
        # SELL 150 @ 1100 → close 100 long (realized=10000), open 50 short
        e = ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "SELL", 150, 1100.0),
            cost_basis=1000.0, mark_price=1100.0,
        )
        # realized = (1100-1000)*100 = 10000 from closing long
        assert e.realized_pnl == pytest.approx(10_000.0)
        avg, qty = ledger._open_positions["A"]
        assert qty == -50
        assert avg == pytest.approx(1100.0)  # new short at fill price

    def test_short_to_long_flip(self):
        ledger = PnLLedger()
        # SELL 100 @ 50000
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        # BUY 150 @ 49000 → cover 100 short (realized=100000), open 50 long
        e = ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "BUY", 150, 49000.0),
            cost_basis=0.0, mark_price=49000.0,
        )
        # realized = (50000-49000)*100 = 100000 from covering short
        assert e.realized_pnl == pytest.approx(100_000.0)
        avg, qty = ledger._open_positions["A"]
        assert qty == 50
        assert avg == pytest.approx(49000.0)  # new long at fill price


# ===================================================================
# 6. Bookkeeper FIFO for shorts
# ===================================================================

class TestBookkeeperShort:
    """Bookkeeper must track short FIFO queue and compute correct PnL."""

    def test_short_then_cover_fifo(self):
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0))
        assert bk.get_position("A") == -100
        assert bk.get_average_cost("A") == pytest.approx(50000.0)

        bk.record_fill(_fill("2026-01-01 10:01", "A", "BUY", 100, 49000.0))
        assert bk.get_position("A") == 0
        # realized = (50000-49000)*100 = 100000
        assert bk.state.realized_pnl == pytest.approx(100_000.0)

    def test_short_fifo_two_lots(self):
        """Two short entries at different prices, covered in FIFO order."""
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 60, 50000.0))
        bk.record_fill(_fill("2026-01-01 10:01", "A", "SELL", 40, 51000.0))
        assert bk.get_position("A") == -100

        # Cover 60 @ 49000 → matches first lot (sold@50000): realized = (50000-49000)*60 = 60000
        bk.record_fill(_fill("2026-01-01 10:02", "A", "BUY", 60, 49000.0))
        assert bk.state.realized_pnl == pytest.approx(60_000.0)

        # Cover 40 @ 49500 → matches second lot (sold@51000): realized = (51000-49500)*40 = 60000
        bk.record_fill(_fill("2026-01-01 10:03", "A", "BUY", 40, 49500.0))
        assert bk.state.realized_pnl == pytest.approx(120_000.0)

    def test_compute_realized_pnl_short(self):
        """compute_realized_pnl (re-computation) handles shorts."""
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0))
        bk.record_fill(_fill("2026-01-01 10:01", "A", "BUY", 100, 49000.0))
        assert bk.compute_realized_pnl("A") == pytest.approx(100_000.0)

    def test_short_no_cover_realized_zero(self):
        """Short only, no cover → realized must be 0."""
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0))
        assert bk.state.realized_pnl == 0.0
        assert bk.compute_realized_pnl("A") == 0.0

    def test_bookkeeper_mark_to_market_short(self):
        """mark_to_market uses signed qty, so short profits when price drops."""
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0))
        # Price dropped to 49000
        unrealized = bk.mark_to_market({"A": 49000.0})
        # (49000 - 50000) * (-100) = 100000
        assert unrealized == pytest.approx(100_000.0)

    def test_bookkeeper_flip_long_to_short(self):
        """BUY 100, then SELL 150 → close long + open short."""
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "BUY", 100, 1000.0))
        bk.record_fill(_fill("2026-01-01 10:01", "A", "SELL", 150, 1100.0))
        assert bk.get_position("A") == -50
        # Long close: (1100-1000)*100 = 10000
        assert bk.state.realized_pnl == pytest.approx(10_000.0)
        # Short FIFO queue should have 50 @ 1100
        assert bk.get_average_cost("A") == pytest.approx(1100.0)

    def test_bookkeeper_reset_clears_short_queues(self):
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0))
        bk.reset()
        assert bk.get_position("A") == 0
        assert bk.state.realized_pnl == 0.0
        assert len(bk._short_cost_queues) == 0


# ===================================================================
# 7. Aggregation regression (report / cumulative series)
# ===================================================================

class TestAggregationRegression:
    """Report and cumulative series must reflect correct short PnL semantics."""

    def test_report_short_round_trip(self):
        """Short sell → cover round trip must show correct total_realized."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "BUY", 100, 49000.0),
            cost_basis=0.0, mark_price=49000.0,
        )
        report = ledger.generate_report()
        assert report.total_realized == pytest.approx(100_000.0)
        assert report.total_unrealized == 0.0
        assert report.net_pnl == pytest.approx(100_000.0)

    def test_pnl_series_short_round_trip(self):
        """Cumulative PnL series must reflect short realized correctly."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "BUY", 100, 49000.0),
            cost_basis=0.0, mark_price=49000.0,
        )
        series = ledger.cumulative_pnl_series()
        # Step 0: short open → realized=0, unrealized=0 → cumulative=0
        assert series.iloc[0] == pytest.approx(0.0)
        # Step 1: cover → realized=100000, unrealized=0 → cumulative=100000
        assert series.iloc[1] == pytest.approx(100_000.0)

    def test_net_pnl_identity_with_short(self):
        """net_pnl = realized + unrealized(last) - commission - tax (short)."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0, fee=100.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        ledger.record_fill(
            _fill("2026-01-01 10:01", "A", "BUY", 50, 49000.0, fee=50.0),
            cost_basis=0.0, mark_price=49000.0,
        )
        report = ledger.generate_report()
        expected = (
            report.total_realized
            + report.total_unrealized
            - report.total_commission
            - report.total_tax
        )
        assert report.net_pnl == pytest.approx(expected, abs=0.01)

    def test_close_position_short(self):
        """close_position() must handle short direction."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        e = ledger.close_position(
            "A", price=49000.0, qty=100,
            timestamp=pd.Timestamp("2026-01-01 10:01"),
        )
        # (50000 - 49000) * 100 = 100000
        assert e.realized_pnl == pytest.approx(100_000.0)
        _, qty = ledger._open_positions["A"]
        assert qty == 0

    def test_mark_to_market_short(self):
        """mark_to_market entry must reflect correct short unrealized."""
        ledger = PnLLedger()
        ledger.record_fill(
            _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0),
            cost_basis=0.0, mark_price=50000.0,
        )
        e = ledger.mark_to_market(
            "A", price=49500.0, qty=-100,
            timestamp=pd.Timestamp("2026-01-01 10:01"),
        )
        # (49500 - 50000) * (-100) = 50000
        assert e.unrealized_pnl == pytest.approx(50_000.0)


# ===================================================================
# 8. Opening cashflow must NOT be treated as realized profit
# ===================================================================

class TestNoPhantomPnL:
    """The original bug: short SELL created massive phantom realized PnL."""

    def test_short_sell_no_phantom_realized(self):
        """A short sell at 50000 with cost_basis=0 must NOT produce
        realized PnL of 50000*qty. Realized must be 0."""
        ledger = PnLLedger()
        f = _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0)
        # Simulate the old buggy caller that passes cost_basis=0
        e = ledger.record_fill(f, cost_basis=0.0, mark_price=50000.0)
        assert e.realized_pnl == 0.0, (
            f"Short entry must not produce realized PnL, got {e.realized_pnl}"
        )

    def test_bookkeeper_short_sell_no_phantom(self):
        """Bookkeeper must not produce realized PnL on short entry."""
        bk = Bookkeeper(initial_cash=1e8)
        bk.record_fill(_fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0))
        assert bk.state.realized_pnl == 0.0

    def test_end_to_end_no_phantom(self):
        """Full flow: bookkeeper + ledger, short entry must have realized=0."""
        bk = Bookkeeper(initial_cash=1e8)
        ledger = PnLLedger()

        f = _fill("2026-01-01 10:00", "A", "SELL", 100, 50000.0)
        cost_basis = bk.get_average_cost(f.symbol)  # 0.0 (no position)
        bk.record_fill(f)
        e = ledger.record_fill(f, cost_basis=cost_basis, mark_price=50000.0)

        assert bk.state.realized_pnl == 0.0
        assert e.realized_pnl == 0.0
