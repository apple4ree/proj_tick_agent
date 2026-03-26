"""pro_rata.py — Risk-adverse gate + size-proportional fill allocation."""
from __future__ import annotations

from typing import TYPE_CHECKING

from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState


class ProRataQueue(QueueModel):
    """Pro-rata queue model (gate + allocation).

    Gate logic is identical to risk_adverse (trade-only advancement).
    After the gate passes, fill qty is capped by an approximate
    size-proportional (pro-rata) allocation:

        share = child.qty / (resting_volume + child.qty)
        fillable = share × same_level_trade_qty
    """

    has_allocation: bool = True

    def advance_depth(self, unexplained_depth_drop: float) -> float:
        return 0.0

    def cap_fill(
        self,
        child,
        state: "MarketState",
        filled_qty: int,
        *,
        level_qty_fn=None,
        same_level_trade_qty_fn=None,
    ) -> int:
        """Cap fill qty by approximate pro-rata share.

        ``level_qty_fn`` and ``same_level_trade_qty_fn`` are callables
        provided by FillSimulator to avoid duplicating LOB-reading logic.
        """
        if level_qty_fn is None or same_level_trade_qty_fn is None:
            return filled_qty

        resting_volume = max(0.0, level_qty_fn(child, state))
        child_qty = max(1, child.remaining_qty)
        total = resting_volume + child_qty
        if total <= 0:
            return filled_qty

        share = child_qty / total
        same_trade = float(same_level_trade_qty_fn(child, state))
        pro_rata_fillable = max(1, int(share * same_trade))

        return min(filled_qty, pro_rata_fillable)
