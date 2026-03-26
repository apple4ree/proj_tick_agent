"""
pnl_ledger.py
-------------
Layer 6: PnL Ledger

Tracks realized and unrealized profit/loss, explicit fees, slippage,
and market-impact costs across fills. Generates comprehensive PnL reports
with cost decomposition.

Design note on cost layering:
  - realized_pnl       = (fill_price - cost_basis) * qty  (embeds spread/impact in fill price)
  - commission_cost    = explicit brokerage/exchange fees from FeeModel
  - tax_cost           = transaction/securities tax
  - slippage_cost      = fill_price vs arrival_mid in KRW
  - impact_cost        = estimated temporary market-impact cost in KRW
  - spread_cost        = half-spread component in KRW
  - gross_pnl          = realized_pnl + unrealized_pnl   (before explicit fees)
  - net_pnl            = gross_pnl - commission_cost - tax_cost
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from market_simulation.layer5_simulator.bookkeeper import FillEvent


# ---------------------------------------------------------------------------
# PnLEntry
# ---------------------------------------------------------------------------

@dataclass
class PnLEntry:
    """
    Single PnL record, typically corresponding to one FillEvent or
    a mark-to-market update.

    속성
    ----------
    timestamp : pd.Timestamp
    symbol : str
    realized_pnl : float
        (fill_price - cost_basis) * qty; includes embedded spread/impact
        since fill prices already reflect those.
    unrealized_pnl : float
        (mark_price - avg_cost_basis) * open_qty at this moment.
    commission_cost : float
        Explicit exchange/broker fee in KRW.
    tax_cost : float
        Securities/transaction tax in KRW.
    slippage_cost : float
        Extra cost vs arrival mid price in KRW  (slippage_bps * notional / 10000).
    impact_cost : float
        Estimated temporary market-impact cost in KRW.
    spread_cost : float
        Half-spread component paid in KRW (spread_bps/2 * notional / 10000).
    """
    timestamp: pd.Timestamp
    symbol: str
    realized_pnl: float
    unrealized_pnl: float
    commission_cost: float = 0.0
    tax_cost: float = 0.0
    slippage_cost: float = 0.0
    impact_cost: float = 0.0
    spread_cost: float = 0.0

    @property
    def total_pnl(self) -> float:
        """Realized + unrealized PnL (before explicit fees)."""
        return self.realized_pnl + self.unrealized_pnl

    @property
    def total_cost(self) -> float:
        """Sum of all explicit cost components."""
        return (
            self.commission_cost
            + self.tax_cost
            + self.slippage_cost
            + self.impact_cost
            + self.spread_cost
        )

    @property
    def gross_pnl(self) -> float:
        """Realized + unrealized (before explicit commission/tax)."""
        return self.total_pnl

    @property
    def net_pnl(self) -> float:
        """gross_pnl minus commission and tax (explicit invoiced costs)."""
        return self.gross_pnl - self.commission_cost - self.tax_cost

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "total_pnl": self.total_pnl,
            "commission_cost": self.commission_cost,
            "tax_cost": self.tax_cost,
            "slippage_cost": self.slippage_cost,
            "impact_cost": self.impact_cost,
            "spread_cost": self.spread_cost,
            "total_cost": self.total_cost,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
        }


# ---------------------------------------------------------------------------
# PnLReport
# ---------------------------------------------------------------------------

@dataclass
class PnLReport:
    """
    Aggregated PnL report over a time window.

    속성
    ----------
    start_time : pd.Timestamp
    end_time : pd.Timestamp
    entries : list[PnLEntry]
    total_realized : float
    total_unrealized : float
    total_commission : float
    total_tax : float
    total_slippage : float
    total_impact : float
    net_pnl : float
    pnl_series : pd.Series
        Cumulative net PnL indexed by timestamp.
    cost_breakdown : dict[str, float]
        Keys: 'commission', 'tax', 'slippage', 'impact', 'spread'.
    """
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    entries: list[PnLEntry]
    total_realized: float
    total_unrealized: float
    total_commission: float
    total_tax: float
    total_slippage: float
    total_impact: float
    net_pnl: float
    pnl_series: pd.Series
    cost_breakdown: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "start_time": str(self.start_time),
            "end_time": str(self.end_time),
            "n_entries": len(self.entries),
            "total_realized": self.total_realized,
            "total_unrealized": self.total_unrealized,
            "gross_pnl": self.total_realized + self.total_unrealized,
            "total_commission": self.total_commission,
            "total_tax": self.total_tax,
            "total_slippage": self.total_slippage,
            "total_impact": self.total_impact,
            "net_pnl": self.net_pnl,
            "cost_breakdown": self.cost_breakdown,
        }

    def __str__(self) -> str:
        lines = [
            f"PnL Report [{self.start_time} — {self.end_time}]",
            f"  Entries        : {len(self.entries)}",
            f"  Total Realized : {self.total_realized:>15,.2f}",
            f"  Total Unrealized: {self.total_unrealized:>14,.2f}",
            f"  Gross PnL      : {self.total_realized + self.total_unrealized:>15,.2f}",
            f"  Commission     : {self.total_commission:>15,.2f}",
            f"  Tax            : {self.total_tax:>15,.2f}",
            f"  Slippage       : {self.total_slippage:>15,.2f}",
            f"  시장 충격  : {self.total_impact:>15,.2f}",
            f"  Net PnL        : {self.net_pnl:>15,.2f}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PnLLedger
# ---------------------------------------------------------------------------

class PnLLedger:
    """
    Records fill-level PnL entries and supports mark-to-market updates.

    Usage
    -----
    ledger = PnLLedger()
    ledger.record_fill(fill, cost_basis=50000.0, mark_price=50100.0)
    ledger.mark_to_market('005930', price=50200.0, qty=100, timestamp=ts)
    report = ledger.generate_report()
    """

    def __init__(self) -> None:
        self.entries: list[PnLEntry] = []
        # Track open position cost basis: symbol -> (avg_price, signed_qty)
        # Positive qty = long, negative qty = short.
        self._open_positions: dict[str, tuple[float, int]] = {}

    # ------------------------------------------------------------------
    # Internal position tracking
    # ------------------------------------------------------------------

    def _update_position(
        self,
        symbol: str,
        side: "OrderSide",
        fill_qty: int,
        fill_price: float,
    ) -> float:
        """Update internal position and return realized PnL.

        Handles long open/close, short open/cover, and position flips.
        """
        from execution_planning.layer3_order.order_types import OrderSide

        avg_price, pos = self._open_positions.get(symbol, (0.0, 0))
        delta = fill_qty if side == OrderSide.BUY else -fill_qty
        new_pos = pos + delta
        realized = 0.0

        # Closing phase: fill opposes existing position
        if pos != 0 and ((pos > 0) != (delta > 0)):
            close_qty = min(abs(delta), abs(pos))
            if pos > 0:
                # Closing long: profit = (sell_price - avg_cost) * qty
                realized = close_qty * (fill_price - avg_price)
            else:
                # Covering short: profit = (avg_sell - buy_price) * qty
                realized = close_qty * (avg_price - fill_price)

        # Update average cost
        if new_pos == 0:
            new_avg = 0.0
        elif pos == 0:
            # Opening from flat
            new_avg = fill_price
        elif (pos > 0) == (delta > 0):
            # Same direction: weighted average
            old_abs = abs(pos)
            new_abs = abs(new_pos)
            new_avg = (avg_price * old_abs + fill_price * fill_qty) / new_abs
        elif (pos > 0) == (new_pos > 0):
            # Partial close, direction unchanged — avg unchanged
            new_avg = avg_price
        else:
            # Position flipped — the new portion opens at fill_price
            new_avg = fill_price

        self._open_positions[symbol] = (new_avg, new_pos)
        return realized

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_fill(
        self,
        fill: "FillEvent",
        cost_basis: float,
        mark_price: float,
    ) -> PnLEntry:
        """
        Create a PnLEntry from a FillEvent.

        Handles four accounting paths:
        - Long open (BUY when flat/long): realized=0, avg cost updated
        - Long close (SELL when long): realized = (fill_price - avg) * qty
        - Short open (SELL when flat/short): realized=0, avg cost updated
        - Short cover (BUY when short): realized = (avg - fill_price) * qty
        - Position flip: close existing + open opposite in one fill

        매개변수
        ----------
        fill : FillEvent
            Completed fill from the matching engine.
        cost_basis : float
            Advisory cost basis from bookkeeper (kept for API compat).
            Internal position tracking is used for PnL computation.
        mark_price : float
            Current mark price for unrealized PnL computation.

        반환값
        -------
        PnLEntry
        """
        from execution_planning.layer3_order.order_types import OrderSide

        notional = float(fill.filled_qty) * fill.fill_price
        sym = fill.symbol

        realized = self._update_position(
            sym, fill.side, fill.filled_qty, fill.fill_price,
        )

        # Unrealized PnL on remaining open position (works for signed qty)
        sym_price, sym_qty = self._open_positions.get(sym, (0.0, 0))
        unrealized = (mark_price - sym_price) * sym_qty if sym_qty != 0 else 0.0

        # Decompose costs from fill metadata
        slippage_cost = abs(fill.slippage_bps) * notional / 10_000.0
        impact_cost = abs(fill.market_impact_bps) * notional / 10_000.0
        # Spread cost approximation: half-spread component
        # (slippage already includes spread so we don't double-count here;
        #  store as 0 unless caller has explicit spread data)
        spread_cost = 0.0

        # Fee decomposition: assume all of fill.fee is commission + tax
        # Callers can supply richer info via meta if needed
        commission_cost = fill.fee
        tax_cost = 0.0

        entry = PnLEntry(
            timestamp=fill.timestamp,
            symbol=fill.symbol,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            commission_cost=commission_cost,
            tax_cost=tax_cost,
            slippage_cost=slippage_cost,
            impact_cost=impact_cost,
            spread_cost=spread_cost,
        )
        self.entries.append(entry)
        return entry

    def mark_to_market(
        self,
        symbol: str,
        price: float,
        qty: int,
        timestamp: pd.Timestamp,
    ) -> PnLEntry:
        """
        Add an unrealized PnL mark entry for an open position.

        매개변수
        ----------
        symbol : str
        price : float
            Current market price.
        qty : int
            Current position size in shares (signed: positive=long, negative=short).
        timestamp : pd.Timestamp
        """
        avg_cost, tracked_qty = self._open_positions.get(symbol, (0.0, 0))
        # Use internal tracked qty when available; fall back to caller qty
        effective_qty = tracked_qty if tracked_qty != 0 else qty
        unrealized = (price - avg_cost) * effective_qty

        entry = PnLEntry(
            timestamp=timestamp,
            symbol=symbol,
            realized_pnl=0.0,
            unrealized_pnl=unrealized,
        )
        self.entries.append(entry)
        return entry

    def close_position(
        self,
        symbol: str,
        price: float,
        qty: int,
        timestamp: pd.Timestamp,
        fees: float = 0.0,
    ) -> PnLEntry:
        """
        Realize the PnL for a closing trade outside of a FillEvent.

        매개변수
        ----------
        symbol : str
        price : float
            Exit price.
        qty : int
            Number of shares to close (always positive).
        timestamp : pd.Timestamp
        fees : float
            Explicit fees on this closing trade.
        """
        avg_cost, open_qty = self._open_positions.get(symbol, (price, 0))

        if open_qty > 0:
            # Closing long
            closed_qty = min(qty, open_qty)
            realized = (price - avg_cost) * closed_qty
            new_qty = open_qty - closed_qty
        elif open_qty < 0:
            # Covering short
            closed_qty = min(qty, abs(open_qty))
            realized = (avg_cost - price) * closed_qty
            new_qty = open_qty + closed_qty
        else:
            realized = 0.0
            new_qty = 0

        self._open_positions[symbol] = (avg_cost if new_qty != 0 else 0.0, new_qty)

        entry = PnLEntry(
            timestamp=timestamp,
            symbol=symbol,
            realized_pnl=realized,
            unrealized_pnl=0.0,
            commission_cost=fees,
        )
        self.entries.append(entry)
        return entry

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_report(
        self,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> PnLReport:
        """
        Aggregate PnLEntries in [start, end] into a PnLReport.

        매개변수
        ----------
        start : pd.Timestamp | None
            Inclusive lower bound. None = use first entry timestamp.
        end : pd.Timestamp | None
            Inclusive upper bound. None = use last entry timestamp.
        """
        filtered = self.entries
        if start is not None:
            filtered = [e for e in filtered if e.timestamp >= start]
        if end is not None:
            filtered = [e for e in filtered if e.timestamp <= end]

        if not filtered:
            now = pd.Timestamp.now()
            empty_series = pd.Series(dtype=float)
            return PnLReport(
                start_time=start or now,
                end_time=end or now,
                entries=[],
                total_realized=0.0,
                total_unrealized=0.0,
                total_commission=0.0,
                total_tax=0.0,
                total_slippage=0.0,
                total_impact=0.0,
                net_pnl=0.0,
                pnl_series=empty_series,
                cost_breakdown={
                    "commission": 0.0,
                    "tax": 0.0,
                    "slippage": 0.0,
                    "impact": 0.0,
                    "spread": 0.0,
                },
            )

        total_realized = sum(e.realized_pnl for e in filtered)
        # Unrealized PnL = last snapshot only (each entry already reflects
        # the full open-position mark-to-market at that moment; summing
        # would inflate the figure across fills).
        total_unrealized = filtered[-1].unrealized_pnl
        total_commission = sum(e.commission_cost for e in filtered)
        total_tax = sum(e.tax_cost for e in filtered)
        total_slippage = sum(e.slippage_cost for e in filtered)
        total_impact = sum(e.impact_cost for e in filtered)
        total_spread = sum(e.spread_cost for e in filtered)
        net_pnl = (total_realized + total_unrealized) - total_commission - total_tax

        # Build cumulative PnL series (mark-to-market basis):
        #   cum_realized[i] = sum(realized_pnl[0..i])
        #   cum_costs[i]    = sum(commission + tax)[0..i]
        #   series[i]       = cum_realized[i] - cum_costs[i] + unrealized[i]
        # Each entry's unrealized is a full-position snapshot, so it is NOT
        # cumulated — only the realized component accumulates over time.
        timestamps = [e.timestamp for e in filtered]
        cum_realized = np.cumsum([e.realized_pnl for e in filtered])
        cum_costs = np.cumsum(
            [e.commission_cost + e.tax_cost for e in filtered]
        )
        unrealized_at_step = np.array([e.unrealized_pnl for e in filtered])
        pnl_series = pd.Series(
            data=cum_realized - cum_costs + unrealized_at_step,
            index=pd.DatetimeIndex(timestamps),
            name="cumulative_net_pnl",
        )

        return PnLReport(
            start_time=filtered[0].timestamp,
            end_time=filtered[-1].timestamp,
            entries=filtered,
            total_realized=total_realized,
            total_unrealized=total_unrealized,
            total_commission=total_commission,
            total_tax=total_tax,
            total_slippage=total_slippage,
            total_impact=total_impact,
            net_pnl=net_pnl,
            pnl_series=pnl_series,
            cost_breakdown={
                "commission": total_commission,
                "tax": total_tax,
                "slippage": total_slippage,
                "impact": total_impact,
                "spread": total_spread,
            },
        )

    def to_dataframe(self) -> pd.DataFrame:
        """Return all PnLEntries as a tidy DataFrame."""
        if not self.entries:
            return pd.DataFrame()
        records = [e.to_dict() for e in self.entries]
        df = pd.DataFrame(records)
        df.set_index("timestamp", inplace=True)
        df.index = pd.DatetimeIndex(df.index)
        return df

    def cumulative_pnl_series(self) -> pd.Series:
        """
        Cumulative mark-to-market PnL indexed by timestamp.

        At each step:  cumsum(realized - costs) + unrealized_snapshot.

        반환값
        -------
        pd.Series
            Index: pd.DatetimeIndex, Values: cumulative net PnL.
        """
        if not self.entries:
            return pd.Series(dtype=float, name="cumulative_net_pnl")

        timestamps = [e.timestamp for e in self.entries]
        cum_realized = np.cumsum([e.realized_pnl for e in self.entries])
        cum_costs = np.cumsum(
            [e.commission_cost + e.tax_cost for e in self.entries]
        )
        unrealized_at_step = np.array([e.unrealized_pnl for e in self.entries])
        return pd.Series(
            data=cum_realized - cum_costs + unrealized_at_step,
            index=pd.DatetimeIndex(timestamps),
            name="cumulative_net_pnl",
        )

    def daily_pnl(self) -> pd.Series:
        """
        Daily aggregation of net PnL.

        반환값
        -------
        pd.Series
            Index: date, Values: daily net PnL.
        """
        if not self.entries:
            return pd.Series(dtype=float, name="daily_net_pnl")

        df = self.to_dataframe()
        return df["net_pnl"].resample("D").sum().rename("daily_net_pnl")
