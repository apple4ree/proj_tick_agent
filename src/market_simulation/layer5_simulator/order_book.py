"""
order_book.py
-------------
LOB simulator for Layer 5.

OrderBookSimulator wraps a LOBSnapshot and provides convenient query methods
used by the MatchingEngine to simulate fills against the order book.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from execution_planning.layer3_order.order_types import OrderSide

from data.layer0_data.market_state import LOBSnapshot, LOBLevel
from execution_planning.layer3_order.order_types import OrderSide


class OrderBookSimulator:
    """
    Maintains a simulated order book state synced to real LOB snapshots.

    The simulator does *not* modify its own internal LOB in response to
    simulated fills — it treats the real snapshot as the ground truth at each
    time step.  Impact-adjusted pricing is handled by the ImpactModel layer.

    매개변수
    ----------
    n_levels : int
        Maximum number of LOB levels to track.
    """

    def __init__(self, n_levels: int = 10) -> None:
        self.n_levels = n_levels
        self._snapshot: Optional[LOBSnapshot] = None

    # ------------------------------------------------------------------
    # 스냅샷 관리
    # ------------------------------------------------------------------

    def update(self, snapshot: LOBSnapshot) -> None:
        """Synchronise the simulated book with a new real LOB snapshot."""
        self._snapshot = snapshot

    @property
    def snapshot(self) -> LOBSnapshot:
        if self._snapshot is None:
            raise RuntimeError("OrderBookSimulator has no snapshot; call update() first.")
        return self._snapshot

    # ------------------------------------------------------------------
    # 최우선 호가
    # ------------------------------------------------------------------

    def get_best_bid(self) -> float:
        bid = self.snapshot.best_bid
        if bid is None:
            raise ValueError("No bid levels in current snapshot.")
        return bid

    def get_best_ask(self) -> float:
        ask = self.snapshot.best_ask
        if ask is None:
            raise ValueError("No ask levels in current snapshot.")
        return ask

    def get_mid(self) -> float:
        mid = self.snapshot.mid_price
        if mid is None:
            raise ValueError("Cannot compute mid: empty bid or ask side.")
        return mid

    def get_spread(self) -> float:
        spread = self.snapshot.spread
        if spread is None:
            raise ValueError("Cannot compute spread: empty bid or ask side.")
        return spread

    # ------------------------------------------------------------------
    # 체결 가능 수량
    # ------------------------------------------------------------------

    def available_to_fill(
        self,
        side: OrderSide,
        price: float,
        qty: int,
    ) -> int:
        """
        Compute how many shares of `qty` can be filled for a LIMIT order.

        For a LIMIT BUY at `price`: sum all ask levels where ask_price <= price.
        For a LIMIT SELL at `price`: sum all bid levels where bid_price >= price.

        반환값
        -------
        int
            Available shares (capped at `qty`).
        """
        snap = self.snapshot
        if side == OrderSide.BUY:
            available = sum(
                lvl.volume for lvl in snap.ask_levels if lvl.price <= price
            )
        else:
            available = sum(
                lvl.volume for lvl in snap.bid_levels if lvl.price >= price
            )
        return min(available, qty)

    def walk_book(
        self,
        side: OrderSide,
        qty: int,
    ) -> tuple[float, int]:
        """
        Simulate walking the book with a market order for `qty` shares.

        Consumes levels from best to worst until qty is exhausted or book
        is empty.

        반환값
        -------
        (avg_price, filled_qty)
            avg_price  : volume-weighted average fill price
            filled_qty : total shares actually filled (may be < qty if book thin)
        """
        snap = self.snapshot
        levels: list[LOBLevel] = (
            snap.ask_levels if side == OrderSide.BUY else snap.bid_levels
        )

        remaining = qty
        total_cost = 0.0
        total_filled = 0

        for lvl in levels:
            if remaining <= 0:
                break
            fill_here = min(remaining, lvl.volume)
            total_cost += fill_here * lvl.price
            total_filled += fill_here
            remaining -= fill_here

        if total_filled == 0:
            return 0.0, 0

        avg_price = total_cost / total_filled
        return avg_price, total_filled

    def eligible_levels(
        self,
        side: OrderSide,
        price: float | None,
    ) -> list[LOBLevel]:
        if price is None:
            return []

        snap = self.snapshot
        if side == OrderSide.BUY:
            return [lvl for lvl in snap.ask_levels if lvl.price <= price]
        return [lvl for lvl in snap.bid_levels if lvl.price >= price]

    def level_volume_at_price(
        self,
        side: OrderSide,
        price: float | None,
    ) -> int:
        if price is None:
            return 0

        levels = self.snapshot.bid_levels if side == OrderSide.BUY else self.snapshot.ask_levels
        for level in levels:
            if level.price == price:
                return level.volume
        return 0
