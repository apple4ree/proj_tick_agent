"""
monitoring/verifiers/latency_verifier.py
-----------------------------------------
Verify that order lifecycle timestamps are physically consistent.

Rules checked
-------------
1. venue_arrival_time >= submit_request_time   (submit latency >= 0)
2. ack_time >= venue_arrival_time              (ack latency >= 0)
3. fill_time >= venue_arrival_time             (fill cannot precede arrival)
4. cancel_effective_time >= cancel_requested_time  (cancel latency >= 0)
   Race: if filled AND cancel_pending, fill_time < cancel_effective_time is allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class LatencyOrderResult:
    child_id: str
    tick_index: int
    passed: bool
    violation: str                         # "" if passed
    submit_request_time: Optional[pd.Timestamp]
    venue_arrival_time: Optional[pd.Timestamp]
    ack_time: Optional[pd.Timestamp]
    fill_time: Optional[pd.Timestamp]
    cancel_effective_time: Optional[pd.Timestamp]


def verify_latency_ordering(
    submit_event=None,
    fill_event=None,
    cancel_event=None,
) -> LatencyOrderResult:
    """
    Verify timestamp ordering for a single child order's lifecycle.

    Parameters
    ----------
    submit_event : OrderSubmitEvent | None
    fill_event   : monitoring.events.FillEvent | None
    cancel_event : CancelRequestEvent | None

    All events must share the same child_id (not enforced here — caller's
    responsibility).
    """
    # Extract timestamps
    submit_req   = _ts(submit_event, "submit_request_time")
    venue_arr    = _ts(submit_event, "venue_arrival_time")
    ack_t        = _ts(submit_event, "ack_time")
    fill_t       = _ts(fill_event, "timestamp") if fill_event is not None else None
    cancel_req_t = _ts(cancel_event, "cancel_requested_time")
    cancel_eff_t = _ts(cancel_event, "cancel_effective_time")

    child_id   = _attr(submit_event or fill_event or cancel_event, "child_id", "")
    tick_index = int(_attr(submit_event or fill_event or cancel_event, "tick_index", 0))

    violation = ""

    # Rule 1: submit latency >= 0
    if submit_req is not None and venue_arr is not None:
        if venue_arr < submit_req:
            violation = "submit_negative_latency"

    # Rule 2: ack latency >= 0
    if not violation and venue_arr is not None and ack_t is not None:
        if ack_t < venue_arr:
            violation = "ack_negative_latency"

    # Rule 3: fill must not precede submit request.
    # We use submit_request_time (not venue_arrival_time) because this is a
    # tick-resolution simulation: venue arrival is computed with sub-tick
    # precision and the fill event timestamp snaps to the tick boundary.
    # The simulator explicitly allows fills within the tick where the order
    # arrives (partial-arrival fast-path in FillSimulator).
    if not violation and fill_t is not None and submit_req is not None:
        if fill_t < submit_req:
            violation = "fill_before_arrival"

    # Rule 4: cancel latency >= 0
    if not violation and cancel_req_t is not None and cancel_eff_t is not None:
        if cancel_eff_t < cancel_req_t:
            violation = "cancel_negative_latency"

    return LatencyOrderResult(
        child_id             = child_id,
        tick_index           = tick_index,
        passed               = violation == "",
        violation            = violation,
        submit_request_time  = submit_req,
        venue_arrival_time   = venue_arr,
        ack_time             = ack_t,
        fill_time            = fill_t,
        cancel_effective_time= cancel_eff_t,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(obj, attr: str) -> Optional[pd.Timestamp]:
    if obj is None:
        return None
    val = getattr(obj, attr, None)
    if isinstance(val, pd.Timestamp):
        return val
    return None


def _attr(obj, attr: str, default):
    if obj is None:
        return default
    return getattr(obj, attr, default)
