"""
monitoring/verifiers/fee_verifier.py
-------------------------------------
KRX fee verification for fill events.

KRX 공식
--------
  notional   = filled_qty * impacted_price
  commission = notional * commission_bps / 10_000
  tax        = notional * tax_bps / 10_000  (SELL only; 0 for BUY)
  expected   = commission + tax

tolerance: 0.01 KRW
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeVerificationResult:
    child_id: str
    tick_index: int
    passed: bool
    expected_fee: float
    actual_fee: float
    error_krw: float    # actual - expected
    notional: float
    side: str
    is_maker: bool


_TOLERANCE_KRW = 0.01


def verify_fill_fee(
    fill_event,
    commission_bps: float = 1.5,
    tax_bps_kospi: float = 20.0,
    tax_bps_kosdaq: float = 18.0,
    is_kospi: bool = True,
) -> FeeVerificationResult:
    """
    Verify that the recorded fee matches the KRX formula.

    Parameters
    ----------
    fill_event : monitoring.events.FillEvent or any object with the
                 attributes: child_id, tick_index, filled_qty,
                 impacted_price, fee, side, is_maker
    commission_bps : float
        Broker commission in basis points (default 1.5 bps).
    tax_bps_kospi : float
        Transaction tax for KOSPI sell (default 20 bps = 0.20%).
    tax_bps_kosdaq : float
        Transaction tax for KOSDAQ sell (default 18 bps = 0.18%).
    is_kospi : bool
        True → use KOSPI tax rate; False → use KOSDAQ rate.
    """
    notional = float(fill_event.filled_qty) * float(fill_event.impacted_price)
    commission = notional * commission_bps / 10_000.0
    tax_bps = tax_bps_kospi if is_kospi else tax_bps_kosdaq
    side_str = fill_event.side if isinstance(fill_event.side, str) else fill_event.side.value
    tax = (notional * tax_bps / 10_000.0) if side_str == "SELL" else 0.0
    expected = commission + tax

    actual = float(fill_event.fee)
    error = actual - expected
    passed = abs(error) <= _TOLERANCE_KRW

    return FeeVerificationResult(
        child_id=fill_event.child_id,
        tick_index=fill_event.tick_index,
        passed=passed,
        expected_fee=expected,
        actual_fee=actual,
        error_krw=error,
        notional=notional,
        side=side_str,
        is_maker=bool(fill_event.is_maker),
    )
