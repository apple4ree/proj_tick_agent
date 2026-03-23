"""
target_builder.py
-----------------
Converts Layer 1 signals into target positions for Layer 2.

클래스
-------
TargetPosition  - Dataclass describing desired portfolio positions
TargetBuilder   - Translates signals into TargetPosition, applying sizing logic
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from execution_planning.layer1_signal.signal import Signal
    from .risk_caps import RiskCaps, RiskReport


# ---------------------------------------------------------------------------
# TargetPosition 데이터 계약
# ---------------------------------------------------------------------------

@dataclass
class TargetPosition:
    """
    Desired portfolio state output by TargetBuilder.

    속성
    ----------
    timestamp : pd.Timestamp
        When this target was computed.
    targets : dict[str, int]
        Symbol → target quantity in shares.  Negative values denote shorts.
    signal_ref : dict[str, float]
        Symbol → signal score that drove the target (for audit/debug).
    risk_report : RiskReport | None
        Risk constraint report attached after caps are applied.
    """

    timestamp: pd.Timestamp
    targets: dict[str, int] = field(default_factory=dict)
    signal_ref: dict[str, float] = field(default_factory=dict)
    risk_report: RiskReport | None = None

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    def total_exposure(self, prices: dict[str, float]) -> float:
        """
        Gross notional value of all positions at the given prices.

        매개변수
        ----------
        prices : dict[str, float]
            Symbol → current price.

        반환값
        -------
        float
            Sum of abs(qty * price) across all symbols.
        """
        return sum(
            abs(qty) * prices.get(sym, 0.0)
            for sym, qty in self.targets.items()
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain-dict representation."""
        return {
            "timestamp": self.timestamp,
            "targets": dict(self.targets),
            "signal_ref": dict(self.signal_ref),
            "risk_report": (
                {
                    "gross_exposure": self.risk_report.gross_exposure,
                    "net_exposure": self.risk_report.net_exposure,
                    "violations": self.risk_report.violations,
                    "is_compliant": self.risk_report.is_compliant,
                }
                if self.risk_report is not None
                else None
            ),
        }

    def __repr__(self) -> str:
        n = len(self.targets)
        return (
            f"TargetPosition(ts={self.timestamp}, symbols={n}, "
            f"targets={self.targets})"
        )


# ---------------------------------------------------------------------------
# TargetBuilder
# ---------------------------------------------------------------------------

class TargetBuilder:
    """
    Translates a list of Signal objects into a TargetPosition.

    Sizing modes
    ------------
    'signal_proportional'
        Position size ∝ |signal.score| × confidence.
        size = max_position × |score| × confidence
    'fixed_size'
        Always ±default_size regardless of signal strength.
    'Kelly'
        Simple half-Kelly sizing: size = max_position × 0.5 × |score|
        (where |score| acts as the edge fraction estimate).

    매개변수
    ----------
    mode : str
        Sizing mode (default 'signal_proportional').
    max_position : int
        Maximum absolute position per symbol in shares.
    default_size : int
        Base size for 'fixed_size' mode and lower bound for other modes.
    """

    _VALID_MODES = {"signal_proportional", "fixed_size", "Kelly"}

    def __init__(
        self,
        mode: str = "signal_proportional",
        max_position: int = 10_000,
        default_size: int = 1_000,
    ) -> None:
        if mode not in self._VALID_MODES:
            raise ValueError(
                f"Unknown mode {mode!r}. Choose from {sorted(self._VALID_MODES)}."
            )
        self._mode = mode
        self._max_position = max_position
        self._default_size = default_size
        # Optional hold-timer: symbol → steps held with current sign
        self._hold_steps: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    def build(
        self,
        signals: list[Signal],
        current_positions: dict[str, int],
        risk_caps: RiskCaps,
        prices: dict[str, float] | None = None,
        portfolio_value: float = 1e8,
        min_hold_steps: int = 0,
    ) -> TargetPosition:
        """
        Compute target positions from signals, subject to risk constraints.

        매개변수
        ----------
        signals : list[Signal]
            Current directional signals.
        current_positions : dict[str, int]
            Symbol → current held quantity.
        risk_caps : RiskCaps
            Risk constraint checker/applier.
        prices : dict[str, float] | None
            Current prices for notional calculations.  Defaults to all 1.0.
        portfolio_value : float
            포트폴리오 NAV used to compute leverage constraints.
        min_hold_steps : int
            Minimum steps before a position flip is allowed.

        반환값
        -------
        TargetPosition
        """
        prices = prices or {}

        if not signals:
            # No signals: carry existing positions through unchanged
            targets = dict(current_positions)
            ts = pd.Timestamp.utcnow()
            adj_targets, report = risk_caps.apply(
                targets, prices, portfolio_value
            )
            return TargetPosition(
                timestamp=ts,
                targets=adj_targets,
                signal_ref={},
                risk_report=report,
            )

        ts = max(s.timestamp for s in signals)
        raw_targets: dict[str, int] = {}
        signal_ref: dict[str, float] = {}

        for sig in signals:
            if not sig.is_valid or sig.score == 0.0:
                continue

            size = self._compute_size(sig)
            direction = 1 if sig.score > 0 else -1
            raw_targets[sig.symbol] = direction * size
            signal_ref[sig.symbol] = sig.score

        # Apply hold rules
        if min_hold_steps > 0:
            raw_targets = self._apply_hold_rules(
                raw_targets, current_positions, min_hold_steps
            )

        # Apply risk caps
        adj_targets, report = risk_caps.apply(raw_targets, prices, portfolio_value)

        # Update hold step counters
        self._update_hold_steps(adj_targets, current_positions)

        return TargetPosition(
            timestamp=ts,
            targets=adj_targets,
            signal_ref=signal_ref,
            risk_report=report,
        )

    # ------------------------------------------------------------------
    # 사이징 도우미
    # ------------------------------------------------------------------

    def _compute_size(self, sig: Signal) -> int:
        """Return unsigned position size for the given signal."""
        if self._mode == "fixed_size":
            return self._default_size
        elif self._mode == "signal_proportional":
            size = self._max_position * abs(sig.score) * sig.confidence
            return max(self._default_size, int(round(size)))
        elif self._mode == "Kelly":
            # Half-Kelly: edge = |score|, bet fraction = 0.5 * edge
            half_kelly = 0.5 * abs(sig.score)
            size = self._max_position * half_kelly
            return max(self._default_size, int(round(size)))
        return self._default_size

    # ------------------------------------------------------------------
    # Hold rule
    # ------------------------------------------------------------------

    def _apply_hold_rules(
        self,
        targets: dict[str, int],
        current: dict[str, int],
        min_hold_steps: int,
    ) -> dict[str, int]:
        """
        Prevent position flips when a symbol has been held for fewer than
        min_hold_steps since the last direction change.

        매개변수
        ----------
        targets : dict[str, int]
            Proposed target quantities.
        current : dict[str, int]
            Current quantities.
        min_hold_steps : int
            Minimum steps before allowing a sign change.

        반환값
        -------
        dict[str, int]
            Adjusted targets with flips suppressed if necessary.
        """
        adjusted = dict(targets)
        for sym, target_qty in targets.items():
            current_qty = current.get(sym, 0)
            steps = self._hold_steps.get(sym, min_hold_steps)
            # Detect flip: different non-zero signs
            if (
                current_qty != 0
                and target_qty != 0
                and math.copysign(1, current_qty) != math.copysign(1, target_qty)
                and steps < min_hold_steps
            ):
                # Suppress the flip: keep current position
                adjusted[sym] = current_qty
        return adjusted

    def _update_hold_steps(
        self,
        targets: dict[str, int],
        current: dict[str, int],
    ) -> None:
        """Increment or reset hold step counters based on direction changes."""
        for sym, target_qty in targets.items():
            current_qty = current.get(sym, 0)
            if (
                current_qty != 0
                and target_qty != 0
                and math.copysign(1, current_qty) == math.copysign(1, target_qty)
            ):
                self._hold_steps[sym] = self._hold_steps.get(sym, 0) + 1
            else:
                self._hold_steps[sym] = 0
