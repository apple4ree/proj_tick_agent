"""
attribution.py
--------------
Layer 6: Performance Attribution

Decomposes total PnL into:
  - Alpha contribution: what PnL would have been at arrival prices (signal value)
  - Execution contribution: improvement vs arrival due to execution quality
  - Cost contribution: explicit fees and slippage
  - Timing contribution: IS improvement vs naive TWAP benchmark
  - Residual: unexplained remainder

참고 프레임워크
-------------------
  total_pnl = alpha + execution + cost + timing + residual

  alpha_contribution    = PnL if all fills executed at arrival price
  execution_contribution = actual_pnl - alpha_pnl - cost
  cost_contribution     = -(total_fees + explicit_slippage)
  timing_contribution   = IS vs TWAP benchmark (positive = beat TWAP)
  residual              = total - alpha - execution - cost - timing
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from market_simulation.layer5_simulator.bookkeeper import FillEvent
    from execution_planning.layer3_order.order_types import ParentOrder
    from execution_planning.layer1_signal.signal import Signal
    from data.layer0_data.market_state import MarketState


# ---------------------------------------------------------------------------
# AttributionReport
# ---------------------------------------------------------------------------

@dataclass
class AttributionReport:
    """
    Performance attribution decomposition.

    속성
    ----------
    total_pnl : float
        Total realized + unrealized PnL in KRW.
    alpha_contribution : float
        Counterfactual PnL if all fills executed at arrival price.
        Captures the value of the trading signal.
    execution_contribution : float
        PnL improvement vs arrival benchmark from execution quality.
        total_pnl - alpha_contribution - cost_contribution.
    cost_contribution : float
        Negative: total fees + explicit slippage costs.
    timing_contribution : float
        IS improvement vs naive TWAP (positive = beat TWAP).
    residual : float
        Unexplained remainder.
    """
    total_pnl: float
    alpha_contribution: float
    execution_contribution: float
    cost_contribution: float
    timing_contribution: float
    residual: float

    @property
    def alpha_fraction(self) -> float:
        """Alpha contribution as fraction of total PnL."""
        if self.total_pnl == 0.0:
            return 0.0
        return self.alpha_contribution / self.total_pnl

    def to_dict(self) -> dict:
        return {
            "total_pnl": self.total_pnl,
            "alpha_contribution": self.alpha_contribution,
            "execution_contribution": self.execution_contribution,
            "cost_contribution": self.cost_contribution,
            "timing_contribution": self.timing_contribution,
            "residual": self.residual,
            "alpha_fraction": self.alpha_fraction,
        }

    def __str__(self) -> str:
        rows = [
            ("Total PnL", f"{self.total_pnl:>18,.2f}"),
            ("Alpha Contribution", f"{self.alpha_contribution:>18,.2f}"),
            ("Execution Contribution", f"{self.execution_contribution:>18,.2f}"),
            ("Cost Contribution", f"{self.cost_contribution:>18,.2f}"),
            ("Timing Contribution", f"{self.timing_contribution:>18,.2f}"),
            ("Residual", f"{self.residual:>18,.2f}"),
            ("Alpha Fraction", f"{self.alpha_fraction:>18.4f}"),
        ]
        width = max(len(k) for k, _ in rows) + 2
        lines = ["Attribution Report", "-" * (width + 22)]
        for key, val in rows:
            lines.append(f"  {key:<{width}}: {val}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# AttributionAnalyzer
# ---------------------------------------------------------------------------

class AttributionAnalyzer:
    """
    Stateless utility for performance attribution analysis.
    """

    @classmethod
    def compute(
        cls,
        fills: list["FillEvent"],
        signals: list["Signal"],
        parent_orders: list["ParentOrder"],
        states: list["MarketState"],
        arrival_prices: dict[str, float],
        twap_prices: dict[str, float],
    ) -> AttributionReport:
        """
        Decompose total PnL into attribution components.

        매개변수
        ----------
        fills : list[FillEvent]
        signals : list[Signal]
        parent_orders : list[ParentOrder]
        states : list[MarketState]
        arrival_prices : dict[str, float]
            Mid price at the time each parent order was submitted, keyed by symbol.
        twap_prices : dict[str, float]
            Hypothetical TWAP execution price for each symbol over the
            execution window.  Used as a cost benchmark.
        """
        if not fills:
            return AttributionReport(
                total_pnl=0.0,
                alpha_contribution=0.0,
                execution_contribution=0.0,
                cost_contribution=0.0,
                timing_contribution=0.0,
                residual=0.0,
            )

        from execution_planning.layer3_order.order_types import OrderSide

        # --- Total PnL: sum of fill PnL (realized component only) ---
        # Realized PnL = fill revenues - fill costs
        # For simplicity we compute net cash flow of fills
        total_pnl = cls._compute_total_fill_pnl(fills)

        # --- Alpha contribution: PnL at arrival prices ---
        alpha_pnl = cls.compute_alpha_pnl(signals, fills, arrival_prices)

        # --- Cost contribution: negative fees + slippage ---
        total_fees = sum(f.fee for f in fills)
        total_slippage_krw = sum(
            abs(f.slippage_bps) * f.notional / 10_000.0 for f in fills
        )
        cost_contribution = -(total_fees + total_slippage_krw)

        # --- Timing contribution: IS vs TWAP ---
        timing_contribution = cls._compute_timing_contribution(
            fills, parent_orders, states, arrival_prices, twap_prices
        )

        # --- Execution contribution: residual after alpha and cost ---
        execution_contribution = total_pnl - alpha_pnl - cost_contribution - timing_contribution

        # --- Residual ---
        residual = total_pnl - alpha_pnl - execution_contribution - cost_contribution - timing_contribution

        return AttributionReport(
            total_pnl=total_pnl,
            alpha_contribution=alpha_pnl,
            execution_contribution=execution_contribution,
            cost_contribution=cost_contribution,
            timing_contribution=timing_contribution,
            residual=residual,
        )

    # ------------------------------------------------------------------
    # 알파 손익
    # ------------------------------------------------------------------

    @classmethod
    def compute_alpha_pnl(
        cls,
        signals: list["Signal"],
        fills: list["FillEvent"],
        arrival_prices: dict[str, float],
    ) -> float:
        """
        Counterfactual PnL if all fills had executed exactly at the
        arrival price (the benchmark price at signal time).

        For each fill we substitute fill_price with arrival_price and
        recompute the position PnL.

        매개변수
        ----------
        signals : list[Signal]
        fills : list[FillEvent]
        arrival_prices : dict[str, float]
            symbol -> arrival mid price.
        """
        from execution_planning.layer3_order.order_types import OrderSide

        if not fills:
            return 0.0

        # Build per-symbol fill sequences using arrival prices
        # Replicate a simple FIFO accounting at arrival prices
        cost_queue: dict[str, list[tuple[float, int]]] = {}
        alpha_pnl = 0.0

        # Sort fills by timestamp for correct FIFO ordering
        sorted_fills = sorted(fills, key=lambda f: f.timestamp)

        for f in sorted_fills:
            arr_price = arrival_prices.get(f.symbol, f.fill_price)
            sym = f.symbol

            if f.side == OrderSide.BUY:
                cost_queue.setdefault(sym, []).append((arr_price, f.filled_qty))
            else:  # SELL
                queue = cost_queue.get(sym, [])
                remaining = f.filled_qty
                while remaining > 0 and queue:
                    cost_price, cost_qty = queue[0]
                    matched = min(remaining, cost_qty)
                    alpha_pnl += matched * (arr_price - cost_price)
                    remaining -= matched
                    if matched == cost_qty:
                        queue.pop(0)
                    else:
                        queue[0] = (cost_price, cost_qty - matched)
                cost_queue[sym] = queue

        return alpha_pnl

    # ------------------------------------------------------------------
    # TWAP 벤치마크 IS
    # ------------------------------------------------------------------

    @classmethod
    def compute_twap_benchmark_is(
        cls,
        parent: "ParentOrder",
        states: list["MarketState"],
    ) -> float:
        """
        Compute the hypothetical IS in bps that a naive uniform TWAP
        would have achieved for this parent order.

        TWAP is modeled as filling equal slices at each state's mid price
        over the execution window.

        매개변수
        ----------
        parent : ParentOrder
        states : list[MarketState]
            All market states in the simulation (filtered by symbol / time).

        반환값
        -------
        float
            IS in bps vs arrival price. Positive = adverse.
        """
        if parent.arrival_mid is None or parent.arrival_mid == 0.0:
            return 0.0

        start = parent.start_time
        end = parent.end_time

        window_states = [
            s for s in states
            if s.symbol == parent.symbol
            and (start is None or s.timestamp >= start)
            and (end is None or s.timestamp <= end)
        ]

        if not window_states:
            return 0.0

        # Uniform TWAP: equal weight on each state's mid
        mids = [s.lob.mid_price for s in window_states if s.lob.mid_price is not None]
        if not mids:
            return 0.0

        twap_price = float(np.mean(mids))
        arrival = parent.arrival_mid

        from execution_planning.layer3_order.order_types import OrderSide
        if parent.side == OrderSide.BUY:
            is_bps = (twap_price - arrival) / arrival * 10_000.0
        else:
            is_bps = (arrival - twap_price) / arrival * 10_000.0

        return is_bps

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_total_fill_pnl(fills: list["FillEvent"]) -> float:
        """
        Compute total PnL from fills using simple FIFO realized PnL.

        Sells realize PnL vs cost basis established by buys.
        """
        from execution_planning.layer3_order.order_types import OrderSide

        cost_queues: dict[str, list[tuple[float, int]]] = {}
        total_pnl = 0.0

        sorted_fills = sorted(fills, key=lambda f: f.timestamp)

        for f in sorted_fills:
            sym = f.symbol
            if f.side == OrderSide.BUY:
                cost_queues.setdefault(sym, []).append((f.fill_price, f.filled_qty))
            else:
                queue = cost_queues.get(sym, [])
                remaining = f.filled_qty
                while remaining > 0 and queue:
                    cost_price, cost_qty = queue[0]
                    matched = min(remaining, cost_qty)
                    total_pnl += matched * (f.fill_price - cost_price)
                    remaining -= matched
                    if matched == cost_qty:
                        queue.pop(0)
                    else:
                        queue[0] = (cost_price, cost_qty - matched)
                cost_queues[sym] = queue

        return total_pnl

    @classmethod
    def _compute_timing_contribution(
        cls,
        fills: list["FillEvent"],
        parent_orders: list["ParentOrder"],
        states: list["MarketState"],
        arrival_prices: dict[str, float],
        twap_prices: dict[str, float],
    ) -> float:
        """
        Timing contribution = actual IS improvement over TWAP baseline.

        For each parent, compute:
          timing_contrib += (twap_IS - actual_IS) * notional / 10_000

        Positive means our execution was better than TWAP.
        """
        from execution_planning.layer3_order.order_types import OrderSide
        from .execution_metrics import ExecutionMetrics

        if not parent_orders:
            return 0.0

        actual_is_bps = ExecutionMetrics.compute_is(fills, arrival_prices)

        # Compute TWAP IS as average over all parents
        twap_is_values: list[float] = []
        for parent in parent_orders:
            twap_is = cls.compute_twap_benchmark_is(parent, states)
            twap_is_values.append(twap_is)

        if not twap_is_values:
            return 0.0

        avg_twap_is = float(np.mean(twap_is_values))

        # Total notional of fills
        total_notional = sum(f.notional for f in fills)

        # 타이밍 contribution in KRW
        timing_contribution = (avg_twap_is - actual_is_bps) / 10_000.0 * total_notional
        return timing_contribution
