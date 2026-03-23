"""
safety_guardrails.py
--------------------
Safety checks for Layer 4 execution.

SafetyGuardrails validates child orders and monitors parent execution progress
to prevent runaway orders, excessive slippage, and missed deadlines.

Violation severity levels:
  - 'warning'  : log and continue
  - 'error'    : block the action, try alternative
  - 'critical' : halt execution and escalate
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ParentOrder, ChildOrder
    from data.layer0_data.market_state import MarketState

from execution_planning.layer3_order.order_types import OrderSide


@dataclass
class GuardrailViolation:
    """Describes a single safety-guardrail breach."""
    rule: str
    details: str
    severity: str           # 'warning' | 'error' | 'critical'
    timestamp: pd.Timestamp


class SafetyGuardrails:
    """
    Collection of safety checks applied before/during child order execution.

    매개변수
    ----------
    max_single_child_pct : float
        Maximum fraction of parent.total_qty that a single child may represent.
        Default 0.25 → no child may exceed 25 % of the parent.
    max_slippage_bps : float
        Maximum allowed deviation of child limit price from current mid (bps).
    max_open_orders : int
        Hard cap on simultaneous open child orders.
    min_fill_rate_by : float | None
        If elapsed_fraction >= this value and fill_rate < 0.5, issue a warning.
        None = disabled.
    emergency_liquidation_threshold_bps : float
        If mid has moved this many bps against the parent since arrival, and
        > 50 % of the order remains unfilled, trigger emergency liquidation.
    """

    def __init__(
        self,
        max_single_child_pct: float = 0.25,
        max_slippage_bps: float = 100.0,
        max_open_orders: int = 20,
        min_fill_rate_by: Optional[float] = None,
        emergency_liquidation_threshold_bps: float = 200.0,
    ) -> None:
        self.max_single_child_pct = max_single_child_pct
        self.max_slippage_bps = max_slippage_bps
        self.max_open_orders = max_open_orders
        self.min_fill_rate_by = min_fill_rate_by
        self.emergency_liquidation_threshold_bps = emergency_liquidation_threshold_bps

    # ------------------------------------------------------------------
    # 개별 점검
    # ------------------------------------------------------------------

    def check_child_size(
        self,
        child: ChildOrder,
        parent: ParentOrder,
    ) -> Optional[GuardrailViolation]:
        """
        Ensure child qty does not exceed max_single_child_pct * parent.total_qty.
        """
        if parent.total_qty == 0:
            return None
        pct = child.qty / parent.total_qty
        if pct > self.max_single_child_pct:
            return GuardrailViolation(
                rule="MAX_CHILD_SIZE",
                details=(
                    f"Child qty {child.qty} is {pct*100:.1f}% of parent total "
                    f"{parent.total_qty} (limit {self.max_single_child_pct*100:.1f}%)"
                ),
                severity="error",
                timestamp=pd.Timestamp.now(),
            )
        return None

    def check_slippage(
        self,
        child: ChildOrder,
        state: MarketState,
    ) -> Optional[GuardrailViolation]:
        """
        Compare child limit price to current mid; flag if > max_slippage_bps.
        """
        mid = state.mid
        if mid is None or mid == 0.0 or child.price is None:
            return None

        deviation_bps = abs((child.price - mid) / mid) * 10_000.0
        if deviation_bps > self.max_slippage_bps:
            return GuardrailViolation(
                rule="MAX_SLIPPAGE",
                details=(
                    f"Child limit {child.price:.2f} deviates {deviation_bps:.1f} bps "
                    f"from mid {mid:.2f} (limit {self.max_slippage_bps:.1f} bps)"
                ),
                severity="error",
                timestamp=pd.Timestamp.now(),
            )
        return None

    def check_open_order_count(
        self,
        n_open: int,
    ) -> Optional[GuardrailViolation]:
        """Enforce the hard cap on simultaneous open orders."""
        if n_open >= self.max_open_orders:
            return GuardrailViolation(
                rule="MAX_OPEN_ORDERS",
                details=(
                    f"Open order count {n_open} >= limit {self.max_open_orders}"
                ),
                severity="error",
                timestamp=pd.Timestamp.now(),
            )
        return None

    def check_fill_progress(
        self,
        parent: ParentOrder,
        elapsed_fraction: float,
    ) -> Optional[GuardrailViolation]:
        """
        If elapsed_fraction >= 0.80 but fill_rate < 0.50, issue a warning to
        accelerate execution (deadline risk).
        """
        check_at = self.min_fill_rate_by if self.min_fill_rate_by is not None else 0.80
        if elapsed_fraction >= check_at and parent.fill_rate < 0.50:
            severity = "critical" if elapsed_fraction >= 0.95 else "warning"
            return GuardrailViolation(
                rule="LOW_FILL_PROGRESS",
                details=(
                    f"Elapsed {elapsed_fraction*100:.1f}% of time but only "
                    f"{parent.fill_rate*100:.1f}% filled "
                    f"({parent.filled_qty}/{parent.total_qty} shares)"
                ),
                severity=severity,
                timestamp=pd.Timestamp.now(),
            )
        return None

    # ------------------------------------------------------------------
    # 종합 점검
    # ------------------------------------------------------------------

    def validate_child(
        self,
        child: ChildOrder,
        parent: ParentOrder,
        state: MarketState,
        n_open: int,
    ) -> list[GuardrailViolation]:
        """
        Run all pre-submission checks on a child order.
        반환값 a (possibly empty) list of violations.
        """
        violations: list[GuardrailViolation] = []

        v = self.check_child_size(child, parent)
        if v:
            violations.append(v)

        v = self.check_slippage(child, state)
        if v:
            violations.append(v)

        v = self.check_open_order_count(n_open)
        if v:
            violations.append(v)

        return violations

    # ------------------------------------------------------------------
    # 긴급 청산
    # ------------------------------------------------------------------

    def should_emergency_liquidate(
        self,
        parent: ParentOrder,
        state: MarketState,
        elapsed_fraction: float,
    ) -> bool:
        """
        Return True if the position should be force-liquidated using market
        orders.

        Triggers when:
          - More than 80 % of allotted time has elapsed AND
          - More than 50 % of the parent remains unfilled AND
          - (Optionally) mid has moved adversely by > threshold since arrival
        """
        if parent.fill_rate >= 1.0:
            return False  # already complete

        # Time pressure condition
        if elapsed_fraction < 0.80:
            return False

        remaining_fraction = 1.0 - parent.fill_rate
        if remaining_fraction < 0.10:
            return False  # only a small stub left — not an emergency

        # Adverse price movement check (optional, only when arrival_mid set)
        arrival_mid = parent.arrival_mid
        current_mid = state.mid
        if arrival_mid is not None and current_mid is not None and arrival_mid > 0.0:
            move_bps = ((current_mid - arrival_mid) / arrival_mid) * 10_000.0
            if parent.side == OrderSide.BUY:
                adverse = move_bps >= self.emergency_liquidation_threshold_bps
            else:
                adverse = move_bps <= -self.emergency_liquidation_threshold_bps
            if adverse:
                return True

        # Pure time-pressure liquidation (>95% elapsed, >50% remaining)
        if elapsed_fraction >= 0.95 and remaining_fraction > 0.50:
            return True

        return False
