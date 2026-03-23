from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer1_signal import Signal


class Strategy(ABC):
    """Strategy interface for tick-level signal generation."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    @abstractmethod
    def generate_signal(self, state: "MarketState") -> "Signal | None":
        """Generate a directional signal for the given market state."""

    def reset(self) -> None:
        """Reset any strategy-internal state before a new run."""
