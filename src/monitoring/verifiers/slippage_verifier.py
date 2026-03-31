"""
monitoring/verifiers/slippage_verifier.py
-----------------------------------------
Slippage verification for fill events.

공식
----
  raw_bps  = ((impacted_price - arrival_mid) / arrival_mid) * 10_000
  expected = raw_bps   (BUY:  positive = adverse)
           = -raw_bps  (SELL: negative = adverse)

tolerance: 1e-4 bps
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlippageVerificationResult:
    child_id: str
    tick_index: int
    passed: bool
    expected_bps: float
    actual_bps: float
    error_bps: float


_TOLERANCE_BPS = 1e-4


def verify_slippage(
    fill_event,
    arrival_mid: float | None = None,
    impacted_price: float | None = None,
    side: object | None = None,
) -> SlippageVerificationResult:
    """
    Verify that the recorded slippage_bps matches the formula.

    Parameters
    ----------
    fill_event : monitoring.events.FillEvent or any compatible object
                 with attributes: child_id, tick_index, slippage_bps,
                 arrival_mid, impacted_price, side
    arrival_mid : float | None
        Override for arrival mid price (uses fill_event.arrival_mid if None).
    impacted_price : float | None
        Override for fill price (uses fill_event.impacted_price if None).
    side : str | OrderSide | None
        Override for side (uses fill_event.side if None).
    """
    _arrival_mid = arrival_mid if arrival_mid is not None else getattr(fill_event, "arrival_mid", None)
    _price = impacted_price if impacted_price is not None else getattr(fill_event, "impacted_price", None)
    _side = side if side is not None else getattr(fill_event, "side", None)

    side_str = _side if isinstance(_side, str) else getattr(_side, "value", str(_side))

    if _arrival_mid is None or float(_arrival_mid) <= 0.0 or _price is None:
        return SlippageVerificationResult(
            child_id=fill_event.child_id,
            tick_index=fill_event.tick_index,
            passed=True,
            expected_bps=0.0,
            actual_bps=float(getattr(fill_event, "slippage_bps", 0.0)),
            error_bps=0.0,
        )

    raw_bps = ((_price - _arrival_mid) / _arrival_mid) * 10_000.0
    expected = raw_bps if side_str == "BUY" else -raw_bps

    actual = float(getattr(fill_event, "slippage_bps", expected))
    error = actual - expected
    passed = abs(error) <= _TOLERANCE_BPS

    return SlippageVerificationResult(
        child_id=fill_event.child_id,
        tick_index=fill_event.tick_index,
        passed=passed,
        expected_bps=expected,
        actual_bps=actual,
        error_bps=error,
    )
