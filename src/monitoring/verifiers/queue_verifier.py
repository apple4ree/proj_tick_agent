"""
monitoring/verifiers/queue_verifier.py
---------------------------------------
Verify that queue arithmetic is monotone and internally consistent.

Rules checked (per tick, per child order)
------------------------------------------
1. queue_ahead_after >= 0                          (no negative queue)
2. queue_ahead_after ≈ queue_ahead_before
   - trade_advancement - depth_advancement         (arithmetic identity, ±1e-4)
3. Once gate_passed=True, it must not revert to False in a later tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


_TOL = 1e-4
_GATE_REVERT_TOL = 0.0   # exact: gate may not revert


@dataclass(frozen=True)
class QueueArithmeticResult:
    child_id: str
    passed: bool
    violation_tick_index: Optional[int]   # first violating tick; None if passed
    violation_desc: str                   # "" if passed


def verify_queue_arithmetic(
    tick_events: list,   # list[QueueTickEvent]
) -> QueueArithmeticResult:
    """
    Verify queue arithmetic consistency across all ticks for one child order.

    Parameters
    ----------
    tick_events : list[QueueTickEvent]
        Must all share the same child_id, sorted by tick_index ascending.
    """
    if not tick_events:
        child_id = ""
        return QueueArithmeticResult(child_id=child_id, passed=True,
                                     violation_tick_index=None, violation_desc="")

    child_id     = tick_events[0].child_id
    gate_was_true = False

    for ev in tick_events:
        tick_idx = ev.tick_index

        # Rule 1: non-negative
        if ev.queue_ahead_after < -_TOL:
            return QueueArithmeticResult(
                child_id             = child_id,
                passed               = False,
                violation_tick_index = tick_idx,
                violation_desc       = f"negative_queue_ahead: {ev.queue_ahead_after:.6f}",
            )

        # Rule 2: arithmetic identity
        expected_after = (ev.queue_ahead_before
                          - ev.trade_advancement
                          - ev.depth_advancement)
        expected_after = max(0.0, expected_after)
        if abs(ev.queue_ahead_after - expected_after) > _TOL:
            return QueueArithmeticResult(
                child_id             = child_id,
                passed               = False,
                violation_tick_index = tick_idx,
                violation_desc       = (
                    f"arithmetic_mismatch: before={ev.queue_ahead_before:.4f} "
                    f"trade={ev.trade_advancement:.4f} depth={ev.depth_advancement:.4f} "
                    f"expected_after={expected_after:.4f} actual_after={ev.queue_ahead_after:.4f}"
                ),
            )

        # Rule 3: gate monotone
        if gate_was_true and not ev.gate_passed:
            return QueueArithmeticResult(
                child_id             = child_id,
                passed               = False,
                violation_tick_index = tick_idx,
                violation_desc       = "gate_reverted_after_pass",
            )
        if ev.gate_passed:
            gate_was_true = True

    return QueueArithmeticResult(
        child_id             = child_id,
        passed               = True,
        violation_tick_index = None,
        violation_desc       = "",
    )
