"""
base.py — Abstract interface for queue-position models.

All queue models implement this protocol.  FillSimulator delegates
queue-position bookkeeping to the active model instance.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState


class QueueModel(ABC):
    """Abstract base for queue-position simulation models.

    Lifecycle per passive child order:
      1. ``new_order``       — initialise queue_ahead_qty when the child first rests.
      2. ``advance_trade``   — reduce queue_ahead by same-level trade volume.
      3. ``advance_depth``   — (model-specific) reduce queue_ahead by unexplained depth drop.
      4. ``ready_to_match``  — gate: is the order eligible for matching?
      5. ``cap_fill``        — (optional) post-gate fill-qty cap (e.g. pro-rata).

    Subclasses MUST implement ``advance_depth``.
    ``cap_fill`` defaults to identity (no cap); override for allocation models.
    """

    # True for models that cap fill qty after the gate passes (e.g. pro_rata).
    has_allocation: bool = False

    def __init__(
        self,
        queue_position_assumption: float = 0.5,
        rng_seed: int | None = None,
    ) -> None:
        self._queue_position_assumption = float(queue_position_assumption)
        self._rng_seed = rng_seed

    # ------------------------------------------------------------------
    # Queue lifecycle
    # ------------------------------------------------------------------

    def new_order(self, child, state: "MarketState") -> None:
        """Initialise queue state on a freshly resting child order.

        Sets ``child.queue_ahead_qty`` to the full level depth at the
        child's price.  Subclasses may override for alternative initial
        placement assumptions.
        """
        # Default: place at back of queue (full level depth ahead)
        # This is a pass-through; FillSimulator handles actual initialisation.

    @staticmethod
    def advance_trade(child, same_level_trade_qty: float) -> float:
        """Reduce queue_ahead by same-level trade volume (common to all models).

        Returns the updated queue_ahead value.
        """
        return max(0.0, float(child.queue_ahead_qty) - same_level_trade_qty)

    @abstractmethod
    def advance_depth(self, unexplained_depth_drop: float) -> float:
        """Model-specific depth-driven queue advancement.

        Returns the amount to subtract from queue_ahead_qty.
        """

    @staticmethod
    def ready_to_match(child, state: "MarketState") -> bool:
        """Gate check: is the order ready to be forwarded to MatchingEngine?

        Returns True when queue_ahead_qty <= 0 AND price is still at best level.
        """
        from execution_planning.layer3_order.order_types import OrderSide

        if child.queue_ahead_qty > 0.0:
            return False

        if child.price is None:
            return True

        if child.side == OrderSide.BUY:
            best_ask = state.lob.best_ask
            if best_ask is not None and child.price >= best_ask:
                return True  # marketable
            return state.lob.best_bid is not None and child.price >= state.lob.best_bid
        else:
            best_bid = state.lob.best_bid
            if best_bid is not None and child.price <= best_bid:
                return True  # marketable
            return state.lob.best_ask is not None and child.price <= state.lob.best_ask

    def cap_fill(self, child, state: "MarketState", filled_qty: int) -> int:
        """Post-gate fill-qty cap.  Default: no cap (identity)."""
        return filled_qty
