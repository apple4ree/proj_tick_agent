"""
execution_metrics.py
--------------------
Layer 6: Execution Quality Metrics

Measures how well orders were executed compared to reference benchmarks:
  - Implementation Shortfall (IS) vs arrival price
  - Fill VWAP vs market VWAP
  - Spread, slippage, and market-impact costs in bps
  - Fill rate, cancel rate, participation rate
  - Timing score (did we fill on favorable price moves?)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from market_simulation.layer5_simulator.bookkeeper import FillEvent
    from execution_planning.layer3_order.order_types import ParentOrder
    from data.layer0_data.market_state import MarketState


# ---------------------------------------------------------------------------
# ExecutionReport
# ---------------------------------------------------------------------------

@dataclass
class ExecutionReport:
    """
    Aggregated execution quality metrics across a set of parent orders.

    속성
    ----------
    n_parent_orders : int
    n_child_orders : int
    total_qty_ordered : int
    total_qty_filled : int
    cancel_rate : float
        Fraction of child orders that were cancelled.
    implementation_shortfall_bps : float
        IS vs arrival price in basis points.  Positive = we paid more than
        arrival (adverse slippage); negative = we received a better price.
    vwap_diff_bps : float
        Difference between our fill VWAP and market VWAP in bps.
        Positive = our VWAP was worse than market VWAP.
    avg_spread_paid_bps : float
        Average spread cost per fill in bps.
    avg_slippage_bps : float
        Average slippage across fills in bps.
    avg_market_impact_bps : float
        Average market-impact estimate across fills in bps.
    timing_score : float
        0–1 score; 1.0 means fills always happened at locally favorable prices.
    participation_rate : float
        Average fraction of market volume that our fills represented.
    """
    n_parent_orders: int
    n_child_orders: int
    total_qty_ordered: int
    total_qty_filled: int
    cancel_rate: float
    implementation_shortfall_bps: float
    vwap_diff_bps: float
    avg_spread_paid_bps: float
    avg_slippage_bps: float
    avg_market_impact_bps: float
    timing_score: float
    participation_rate: float
    partial_fill_rate: float
    maker_fill_ratio: float
    avg_latency_ms: float
    p95_latency_ms: float

    @property
    def fill_rate(self) -> float:
        """Fraction of ordered shares that were filled."""
        if self.total_qty_ordered == 0:
            return 0.0
        return self.total_qty_filled / self.total_qty_ordered

    def to_dict(self) -> dict:
        return {
            "n_parent_orders": self.n_parent_orders,
            "n_child_orders": self.n_child_orders,
            "total_qty_ordered": self.total_qty_ordered,
            "total_qty_filled": self.total_qty_filled,
            "fill_rate": self.fill_rate,
            "cancel_rate": self.cancel_rate,
            "implementation_shortfall_bps": self.implementation_shortfall_bps,
            "vwap_diff_bps": self.vwap_diff_bps,
            "avg_spread_paid_bps": self.avg_spread_paid_bps,
            "avg_slippage_bps": self.avg_slippage_bps,
            "avg_market_impact_bps": self.avg_market_impact_bps,
            "timing_score": self.timing_score,
            "participation_rate": self.participation_rate,
            "partial_fill_rate": self.partial_fill_rate,
            "maker_fill_ratio": self.maker_fill_ratio,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
        }

    def __str__(self) -> str:
        rows = [
            ("Parent Orders", str(self.n_parent_orders)),
            ("Child Orders", str(self.n_child_orders)),
            ("Total Qty Ordered", str(self.total_qty_ordered)),
            ("Total Qty Filled", str(self.total_qty_filled)),
            ("Fill Rate", f"{self.fill_rate:.4f}  ({self.fill_rate * 100:.2f}%)"),
            ("Cancel Rate", f"{self.cancel_rate:.4f}  ({self.cancel_rate * 100:.2f}%)"),
            ("IS vs Arrival (bps)", f"{self.implementation_shortfall_bps:.2f}"),
            ("VWAP Diff (bps)", f"{self.vwap_diff_bps:.2f}"),
            ("Avg Spread Paid (bps)", f"{self.avg_spread_paid_bps:.2f}"),
            ("Avg Slippage (bps)", f"{self.avg_slippage_bps:.2f}"),
            ("Avg 시장 충격 (bps)", f"{self.avg_market_impact_bps:.2f}"),
            ("Timing Score", f"{self.timing_score:.4f}"),
            ("Participation Rate", f"{self.participation_rate:.4f}"),
            ("Partial Fill Rate", f"{self.partial_fill_rate:.4f}"),
            ("Maker Fill Ratio", f"{self.maker_fill_ratio:.4f}"),
            ("Avg 지연 (ms)", f"{self.avg_latency_ms:.3f}"),
            ("P95 지연 (ms)", f"{self.p95_latency_ms:.3f}"),
        ]
        width = max(len(k) for k, _ in rows) + 2
        lines = ["Execution Report", "-" * (width + 20)]
        for key, val in rows:
            lines.append(f"  {key:<{width}}: {val}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ExecutionMetrics
# ---------------------------------------------------------------------------

class ExecutionMetrics:
    """
    Stateless utility for computing execution quality metrics.
    """

    @classmethod
    def compute(
        cls,
        fills: list["FillEvent"],
        parent_orders: list["ParentOrder"],
        market_states: list["MarketState"],
    ) -> ExecutionReport:
        """
        Compute a full ExecutionReport.

        매개변수
        ----------
        fills : list[FillEvent]
        parent_orders : list[ParentOrder]
        market_states : list[MarketState]
            Ordered sequence of market snapshots used for VWAP and timing.
        """
        if not fills and not parent_orders:
            return cls._empty_report()

        # --- Order counts ---
        n_parents = len(parent_orders)
        n_children = sum(len(p.child_orders) for p in parent_orders)
        total_qty_ordered = sum(p.total_qty for p in parent_orders)
        total_qty_filled = sum(f.filled_qty for f in fills)

        # --- Cancel rate ---
        from execution_planning.layer3_order.order_types import OrderStatus
        n_cancelled = sum(
            1
            for p in parent_orders
            for c in p.child_orders
            if c.status == OrderStatus.CANCELLED
        )
        cancel_rate = n_cancelled / n_children if n_children > 0 else 0.0

        # --- IS vs arrival ---
        arrival_prices: dict[str, float] = {}
        for p in parent_orders:
            if p.arrival_mid is not None:
                arrival_prices[p.symbol] = p.arrival_mid

        if arrival_prices:
            is_bps = cls.compute_is(fills, arrival_prices)
        else:
            is_bps = 0.0

        # --- VWAP diff ---
        fill_vwap = cls.compute_vwap(fills)
        market_vwap_val = 0.0
        if market_states and fills:
            start_ts = min(f.timestamp for f in fills)
            end_ts = max(f.timestamp for f in fills)
            market_vwap_val = cls.compute_market_vwap(market_states, start_ts, end_ts)

        if fill_vwap > 0.0 and market_vwap_val > 0.0:
            vwap_diff_bps = (fill_vwap - market_vwap_val) / market_vwap_val * 10_000.0
        else:
            vwap_diff_bps = 0.0

        # --- Per-fill averages ---
        if fills:
            avg_slippage = float(np.mean([f.slippage_bps for f in fills]))
            avg_impact = float(np.mean([f.market_impact_bps for f in fills]))
            avg_latency = float(np.mean([f.latency_ms for f in fills]))
            p95_latency = float(np.percentile([f.latency_ms for f in fills], 95))
            maker_fill_ratio = (
                float(sum(f.filled_qty for f in fills if getattr(f, "is_maker", False)))
                / float(sum(f.filled_qty for f in fills))
            )
        else:
            avg_slippage = 0.0
            avg_impact = 0.0
            avg_latency = 0.0
            p95_latency = 0.0
            maker_fill_ratio = 0.0

        # Spread paid: approximated from slippage (half-spread component)
        avg_spread_paid = max(0.0, avg_slippage - avg_impact)

        # --- Timing score ---
        timing_score = cls._compute_timing_score(fills, market_states)

        # --- Participation rate ---
        participation_rate = cls._compute_participation_rate(fills, market_states)
        partial_fill_rate = cls._compute_partial_fill_rate(parent_orders)

        return ExecutionReport(
            n_parent_orders=n_parents,
            n_child_orders=n_children,
            total_qty_ordered=total_qty_ordered,
            total_qty_filled=total_qty_filled,
            cancel_rate=cancel_rate,
            implementation_shortfall_bps=is_bps,
            vwap_diff_bps=vwap_diff_bps,
            avg_spread_paid_bps=avg_spread_paid,
            avg_slippage_bps=avg_slippage,
            avg_market_impact_bps=avg_impact,
            timing_score=timing_score,
            participation_rate=participation_rate,
            partial_fill_rate=partial_fill_rate,
            maker_fill_ratio=maker_fill_ratio,
            avg_latency_ms=avg_latency,
            p95_latency_ms=p95_latency,
        )

    # ------------------------------------------------------------------
    # IS 계산
    # ------------------------------------------------------------------

    @staticmethod
    def compute_is(
        fills: list["FillEvent"],
        arrival_prices: dict[str, float],
    ) -> float:
        """
        Implementation Shortfall vs arrival prices, in basis points.

        IS = sum((fill_price - arrival_price) * signed_qty)
             / sum(arrival_price * abs(qty)) * 10_000

        For buys:  positive IS = bad (paid more than arrival).
        For sells: positive IS = bad (received less than arrival).
        The sign convention here returns a positive number when execution
        was adverse vs arrival.

        매개변수
        ----------
        fills : list[FillEvent]
        arrival_prices : dict[str, float]
            Arrival mid prices keyed by symbol.
        """
        from execution_planning.layer3_order.order_types import OrderSide

        if not fills:
            return 0.0

        numerator = 0.0
        denominator = 0.0

        for f in fills:
            arr = arrival_prices.get(f.symbol)
            if arr is None or arr == 0.0:
                continue

            notional = arr * f.filled_qty
            denominator += notional

            if f.side == OrderSide.BUY:
                # Cost: we paid fill_price, benchmark is arrival
                numerator += (f.fill_price - arr) * f.filled_qty
            else:
                # Revenue: we received fill_price, benchmark is arrival
                numerator += (arr - f.fill_price) * f.filled_qty

        if denominator == 0.0:
            return 0.0
        return numerator / denominator * 10_000.0

    # ------------------------------------------------------------------
    # VWAP
    # ------------------------------------------------------------------

    @staticmethod
    def compute_vwap(fills: list["FillEvent"]) -> float:
        """
        Volume-weighted average fill price across all fills.

        반환값 0.0 if fills list is empty.
        """
        if not fills:
            return 0.0
        total_notional = sum(f.filled_qty * f.fill_price for f in fills)
        total_qty = sum(f.filled_qty for f in fills)
        return total_notional / total_qty if total_qty > 0 else 0.0

    @staticmethod
    def compute_market_vwap(
        states: list["MarketState"],
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> float:
        """
        Proxy market VWAP using LOB mid prices weighted by total depth.

        매개변수
        ----------
        states : list[MarketState]
        start : pd.Timestamp
        end : pd.Timestamp
        """
        if not states:
            return 0.0

        window = [s for s in states if start <= s.timestamp <= end]
        if not window:
            return 0.0

        total_weight = 0.0
        weighted_price = 0.0
        for s in window:
            mid = s.lob.mid_price
            if mid is None:
                continue
            depth = s.lob.total_bid_depth + s.lob.total_ask_depth
            weight = float(depth) if depth > 0 else 1.0
            weighted_price += mid * weight
            total_weight += weight

        return weighted_price / total_weight if total_weight > 0.0 else 0.0

    # ------------------------------------------------------------------
    # Per-order fill rate
    # ------------------------------------------------------------------

    @staticmethod
    def fill_rate_by_order(
        parent: "ParentOrder",
        fills: list["FillEvent"],
    ) -> float:
        """
        Fraction of the parent order's total_qty that was filled.

        매개변수
        ----------
        parent : ParentOrder
        fills : list[FillEvent]
            All fills (filtered to this parent's ID inside the method).
        """
        if parent.total_qty == 0:
            return 0.0
        filled = sum(
            f.filled_qty for f in fills if f.parent_id == parent.order_id
        )
        return filled / parent.total_qty

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_timing_score(
        fills: list["FillEvent"],
        states: list["MarketState"],
    ) -> float:
        """
        Measure how well fills were timed within the execution window.

        Score = fraction of fills that occurred when the fill price was
        at or below the rolling average mid price for buys, or at/above for sells.
        Range: 0 (worst) to 1 (best).
        """
        from execution_planning.layer3_order.order_types import OrderSide

        if not fills or not states:
            return 0.5  # neutral when no data

        state_map: dict[pd.Timestamp, float] = {}
        for s in states:
            mid = s.lob.mid_price
            if mid is not None:
                state_map[s.timestamp] = mid

        if not state_map:
            return 0.5

        ts_sorted = sorted(state_map.keys())
        prices_sorted = [state_map[t] for t in ts_sorted]

        # Compute rolling average at time of each fill
        good_fills = 0
        total_fills = 0

        for f in fills:
            # Find the mid at or before fill time
            idx = np.searchsorted(ts_sorted, f.timestamp, side="right") - 1
            if idx < 0:
                continue

            window_prices = prices_sorted[max(0, idx - 20): idx + 1]
            avg_mid = np.mean(window_prices) if window_prices else prices_sorted[idx]

            total_fills += 1
            if f.side == OrderSide.BUY:
                if f.fill_price <= avg_mid:
                    good_fills += 1
            else:
                if f.fill_price >= avg_mid:
                    good_fills += 1

        return good_fills / total_fills if total_fills > 0 else 0.5

    @staticmethod
    def _compute_participation_rate(
        fills: list["FillEvent"],
        states: list["MarketState"],
    ) -> float:
        """
        Approximate participation rate: our fill volume / market depth.

        Uses LOB depth as a proxy for available volume at each timestamp.
        """
        if not fills or not states:
            return 0.0

        state_map: dict[pd.Timestamp, "MarketState"] = {s.timestamp: s for s in states}
        ts_sorted = sorted(state_map.keys())

        participation_rates: list[float] = []
        for f in fills:
            idx = np.searchsorted(ts_sorted, f.timestamp, side="right") - 1
            if idx < 0:
                continue
            ts_key = ts_sorted[idx]
            state = state_map[ts_key]
            market_depth = state.lob.total_bid_depth + state.lob.total_ask_depth
            if market_depth > 0:
                participation_rates.append(f.filled_qty / market_depth)

        return float(np.mean(participation_rates)) if participation_rates else 0.0

    @staticmethod
    def _compute_partial_fill_rate(parent_orders: list["ParentOrder"]) -> float:
        from execution_planning.layer3_order.order_types import OrderStatus

        child_orders = [child for parent in parent_orders for child in parent.child_orders]
        if not child_orders:
            return 0.0

        partial_statuses = {OrderStatus.PARTIAL, OrderStatus.PARTIALLY_FILLED}
        partial_count = sum(
            1
            for child in child_orders
            if child.status in partial_statuses or (0 < child.filled_qty < child.qty)
        )
        return partial_count / len(child_orders)

    @staticmethod
    def _empty_report() -> ExecutionReport:
        return ExecutionReport(
            n_parent_orders=0,
            n_child_orders=0,
            total_qty_ordered=0,
            total_qty_filled=0,
            cancel_rate=0.0,
            implementation_shortfall_bps=0.0,
            vwap_diff_bps=0.0,
            avg_spread_paid_bps=0.0,
            avg_slippage_bps=0.0,
            avg_market_impact_bps=0.0,
            timing_score=0.5,
            participation_rate=0.0,
            partial_fill_rate=0.0,
            maker_fill_ratio=0.0,
            avg_latency_ms=0.0,
            p95_latency_ms=0.0,
        )
