"""
slicing_policy.py
-----------------
Execution slicing algorithms for Layer 4.

Defines how a ParentOrder's total quantity is divided into a schedule of
child slices over time.  Supported algorithms:
  - TWAPSlicer   : uniform time-weighted average price schedule
  - VWAPSlicer   : volume-weighted schedule (historical or LOB-proxy)
  - POVSlicer    : percentage-of-volume (dynamic, per-step)
  - AlmgrenChrissSlicer : optimal execution via Almgren-Chriss model
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import ParentOrder, ChildOrder
    from data.layer0_data.market_state import MarketState


class SlicingPolicy(ABC):
    """추상 기반 class for all slicing algorithms."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the slicing policy."""
        ...

    @abstractmethod
    def generate_schedule(
        self,
        parent: ParentOrder,
        states: list[MarketState],
    ) -> list[tuple[int, int]]:
        """
        Compute the full execution schedule upfront.

        매개변수
        ----------
        parent : ParentOrder
            The parent order to be sliced.
        states : list[MarketState]
            Sequence of anticipated market states (one per step).

        반환값
        -------
        list of (step_index, qty)
            Each tuple says: at `step_index`, send a child of `qty` shares.
            Quantities must sum to parent.total_qty.
        """
        ...

    def on_fill(
        self,
        filled_qty: int,
        remaining_qty: int,
        state: MarketState,
    ) -> None:
        """
        Callback invoked after each child fill.
        Subclasses may update internal state (e.g. POV baseline).
        Default implementation does nothing.
        """
        pass


# ---------------------------------------------------------------------------
# TWAP
# ---------------------------------------------------------------------------

class TWAPSlicer(SlicingPolicy):
    """
    Time-Weighted Average Price slicer.

    Divides total_qty as evenly as possible over the execution horizon.
    If n_slices is None it is derived from interval_seconds and the number
    of available states.
    """

    def __init__(
        self,
        n_slices: Optional[int] = None,
        interval_seconds: float = 30.0,
    ) -> None:
        self._n_slices = n_slices
        self.interval_seconds = interval_seconds

    @property
    def name(self) -> str:
        return "TWAP"

    def generate_schedule(
        self,
        parent: ParentOrder,
        states: list[MarketState],
    ) -> list[tuple[int, int]]:
        n_steps = len(states)
        if n_steps == 0:
            return []

        # Determine number of slices
        if self._n_slices is not None:
            n_slices = min(self._n_slices, n_steps)
        else:
            # Estimate seconds per step from first two states if possible
            if n_steps >= 2:
                dt = (states[1].timestamp - states[0].timestamp).total_seconds()
                dt = max(dt, 1.0)
            else:
                dt = self.interval_seconds
            n_slices = max(1, int(round(n_steps * dt / self.interval_seconds)))
            n_slices = min(n_slices, n_steps)

        total_qty = parent.total_qty
        base_qty = total_qty // n_slices
        remainder = total_qty - base_qty * n_slices

        # Spread slices evenly across available steps
        step_indices = _spread_indices(n_slices, n_steps)
        schedule: list[tuple[int, int]] = []
        for i, step in enumerate(step_indices):
            qty = base_qty + (1 if i < remainder else 0)
            if qty > 0:
                schedule.append((step, qty))
        return schedule


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------

class VWAPSlicer(SlicingPolicy):
    """
    Volume-Weighted Average Price slicer.

    Uses a historical intraday volume profile (if provided) or estimates
    expected volume from LOB depth at each state.
    """

    def __init__(self, volume_profile: Optional[np.ndarray] = None) -> None:
        self.volume_profile = volume_profile

    @property
    def name(self) -> str:
        return "VWAP"

    def _estimate_volume_profile(self, states: list[MarketState]) -> np.ndarray:
        """
        Use total LOB depth (bid + ask) as a proxy for expected volume at
        each time step.  반환값 a probability-normalised weight array.
        """
        depths = np.array(
            [
                state.lob.total_bid_depth + state.lob.total_ask_depth
                for state in states
            ],
            dtype=float,
        )
        total = depths.sum()
        if total == 0.0:
            return np.ones(len(states), dtype=float) / len(states)
        return depths / total

    def generate_schedule(
        self,
        parent: ParentOrder,
        states: list[MarketState],
    ) -> list[tuple[int, int]]:
        n_steps = len(states)
        if n_steps == 0:
            return []

        total_qty = parent.total_qty

        if self.volume_profile is not None:
            # Interpolate or truncate provided profile to match n_steps
            profile = np.interp(
                np.linspace(0, 1, n_steps),
                np.linspace(0, 1, len(self.volume_profile)),
                self.volume_profile,
            ).astype(float)
        else:
            profile = self._estimate_volume_profile(states)

        # Normalise to sum-1 weights
        profile_sum = profile.sum()
        if profile_sum == 0.0:
            profile = np.ones(n_steps, dtype=float) / n_steps
        else:
            profile = profile / profile_sum

        # Convert weights → integer quantities
        raw_qtys = profile * total_qty
        int_qtys = np.floor(raw_qtys).astype(int)
        deficit = total_qty - int_qtys.sum()

        # Distribute deficit to the slices with the largest fractional parts
        if deficit > 0:
            fractional = raw_qtys - int_qtys
            top_indices = np.argsort(fractional)[::-1][:deficit]
            int_qtys[top_indices] += 1

        schedule = [
            (step, int(qty))
            for step, qty in enumerate(int_qtys)
            if qty > 0
        ]
        return schedule


# ---------------------------------------------------------------------------
# POV
# ---------------------------------------------------------------------------

class POVSlicer(SlicingPolicy):
    """
    Percentage-of-Volume slicer.

    Dynamic policy: at each step, the child quantity is
        min(remaining_qty, participation_rate * observed_volume)
    where observed_volume is approximated from the LOB.

    Unlike TWAP/VWAP this slicer is used *dynamically* via next_qty(); the
    generate_schedule() method produces a best-effort static approximation.
    """

    def __init__(self, participation_rate: float = 0.05) -> None:
        if not 0.0 < participation_rate <= 1.0:
            raise ValueError("participation_rate must be in (0, 1]")
        self.participation_rate = participation_rate

    @property
    def name(self) -> str:
        return "POV"

    def next_qty(self, remaining_qty: int, state: MarketState) -> int:
        """
        Compute the next child quantity given current remaining and market state.
        """
        total_depth = state.lob.total_bid_depth + state.lob.total_ask_depth
        target = int(math.floor(self.participation_rate * total_depth))
        return max(0, min(remaining_qty, target))

    def generate_schedule(
        self,
        parent: ParentOrder,
        states: list[MarketState],
    ) -> list[tuple[int, int]]:
        remaining = parent.total_qty
        schedule: list[tuple[int, int]] = []
        for step, state in enumerate(states):
            if remaining <= 0:
                break
            qty = self.next_qty(remaining, state)
            if qty > 0:
                schedule.append((step, qty))
                remaining -= qty
        # If still has remainder after all steps, append to last step
        if remaining > 0:
            if schedule:
                last_step, last_qty = schedule[-1]
                schedule[-1] = (last_step, last_qty + remaining)
            elif states:
                schedule.append((len(states) - 1, remaining))
        return schedule


# ---------------------------------------------------------------------------
# Almgren-Chriss
# ---------------------------------------------------------------------------

class AlmgrenChrissSlicer(SlicingPolicy):
    """
    Simplified Almgren-Chriss optimal execution slicer.

    Minimises E[cost] + lambda * Var[cost] over a fixed time horizon T.

    Analytical solution (from Almgren & Chriss 2000):
        x_j = X * sinh(kappa * (T - j*tau)) / sinh(kappa * T)
    where
        kappa = sqrt(gamma / (eta * tau))
        tau   = T / n_steps

    매개변수
    ----------
    eta   : temporary impact coefficient
    gamma : permanent impact coefficient
    sigma : price 변동성 (annualised fraction → scaled internally)
    T     : number of abstract time periods (not wall-clock seconds)
    """

    def __init__(
        self,
        eta: float = 0.1,
        gamma: float = 0.01,
        sigma: float = 0.01,
        T: int = 100,
    ) -> None:
        self.eta = eta
        self.gamma = gamma
        self.sigma = sigma
        self.T = T

    @property
    def name(self) -> str:
        return "AlmgrenChriss"

    def _compute_trajectory(self, total_qty: int, n_steps: int) -> np.ndarray:
        """
        Compute optimal holdings trajectory x[0..n_steps].
        x[j] = remaining shares at time j*tau.
        반환값 the *trade quantities* (diff of holdings) as an array of
        length n_steps.
        """
        if n_steps <= 0:
            return np.array([], dtype=float)
        if n_steps == 1:
            return np.array([float(total_qty)])

        tau = self.T / n_steps
        kappa_sq = self.gamma / (self.eta * tau) if self.eta * tau > 0 else 0.0
        kappa = math.sqrt(max(kappa_sq, 0.0))

        # Holdings at each time point j = 0, 1, ..., n_steps
        j_arr = np.arange(n_steps + 1, dtype=float)
        if kappa < 1e-10:
            # kappa ≈ 0: linear (TWAP) schedule
            x_j = total_qty * (1.0 - j_arr / n_steps)
        else:
            sinh_kT = math.sinh(kappa * self.T)
            if sinh_kT < 1e-15:
                x_j = total_qty * (1.0 - j_arr / n_steps)
            else:
                x_j = total_qty * np.sinh(kappa * (self.T - j_arr * tau)) / sinh_kT

        # Trade quantities = decrease in holdings at each step
        trade_qtys = np.diff(-x_j)   # x[j] - x[j+1] → positive for sells
        trade_qtys = np.maximum(trade_qtys, 0.0)  # no negative trades
        return trade_qtys

    def generate_schedule(
        self,
        parent: ParentOrder,
        states: list[MarketState],
    ) -> list[tuple[int, int]]:
        n_steps = len(states)
        if n_steps == 0:
            return []

        total_qty = parent.total_qty
        raw_trades = self._compute_trajectory(total_qty, n_steps)

        # Convert to integers, preserving total
        int_trades = np.floor(raw_trades).astype(int)
        deficit = total_qty - int_trades.sum()
        if deficit > 0:
            fractional = raw_trades - int_trades
            top_idx = np.argsort(fractional)[::-1][:deficit]
            int_trades[top_idx] += 1
        elif deficit < 0:
            # Rare: reduce from largest slices
            for idx in np.argsort(int_trades)[::-1]:
                if deficit == 0:
                    break
                reduction = min(int_trades[idx], -deficit)
                int_trades[idx] -= reduction
                deficit += reduction

        schedule = [
            (step, int(qty))
            for step, qty in enumerate(int_trades)
            if qty > 0
        ]
        return schedule


# ---------------------------------------------------------------------------
# 내부 도우미
# ---------------------------------------------------------------------------

def _spread_indices(n_slices: int, n_steps: int) -> list[int]:
    """Return n_slices step indices spread evenly across [0, n_steps)."""
    if n_slices >= n_steps:
        return list(range(n_steps))
    return [int(round(i * (n_steps - 1) / (n_slices - 1))) if n_slices > 1 else 0
            for i in range(n_slices)]
