"""
signal.py
---------
Defines the Signal dataclass - the primary output of Layer 1.
A Signal encapsulates a directional prediction with quality metadata
consumed by Layer 2 (position/inventory controller).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Signal:
    """
    Directional alpha signal produced by Layer 1.

    속성
    ----------
    timestamp : pd.Timestamp
        Time at which the signal was generated.
    symbol : str
        Instrument identifier.
    score : float
        Raw signal score in [-1, +1] after normalization.
        Negative → bearish / sell, positive → bullish / buy.
    expected_return : float
        Expected return over the prediction horizon, expressed in bps.
    confidence : float
        Estimated prediction quality in [0, 1].
        Higher values indicate stronger conviction.
    horizon_steps : int
        Prediction horizon in ticks or time-steps.
    tags : dict[str, Any]
        Arbitrary metadata (e.g. regime, alpha_source, model_version).
    is_valid : bool
        True when the signal has passed all quality gates.
    """

    timestamp: pd.Timestamp
    symbol: str
    score: float
    expected_return: float
    confidence: float
    horizon_steps: int
    tags: dict[str, Any] = field(default_factory=dict)
    is_valid: bool = True

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain-dict representation of the signal."""
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "score": self.score,
            "expected_return": self.expected_return,
            "confidence": self.confidence,
            "horizon_steps": self.horizon_steps,
            "tags": dict(self.tags),
            "is_valid": self.is_valid,
        }

    # ------------------------------------------------------------------
    # 문자열 표현
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        direction = "BUY" if self.score > 0 else ("SELL" if self.score < 0 else "FLAT")
        return (
            f"Signal(symbol={self.symbol!r}, ts={self.timestamp}, "
            f"score={self.score:.4f}, er={self.expected_return:.2f}bps, "
            f"conf={self.confidence:.3f}, dir={direction}, valid={self.is_valid})"
        )
