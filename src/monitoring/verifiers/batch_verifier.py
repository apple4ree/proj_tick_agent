"""
monitoring/verifiers/batch_verifier.py
---------------------------------------
Run all verifiers over the full EventBus in one pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class BatchVerificationReport:
    n_fills: int
    fee_pass_rate: float
    fee_failures: list
    slippage_pass_rate: float
    slippage_failures: list
    latency_pass_rate: float
    latency_failures: list
    queue_pass_rate: float
    queue_failures: list
    run_id: str
    generated_at: pd.Timestamp


def run_all_verifiers(
    bus,
    commission_bps: float = 1.5,
    tax_bps_kospi: float = 20.0,
    tax_bps_kosdaq: float = 18.0,
    verify_queue: bool = False,
) -> BatchVerificationReport:
    """
    Pull events from the bus and run fee, slippage, latency, and (optionally)
    queue arithmetic verifiers.

    Parameters
    ----------
    bus : EventBus
    commission_bps : float
    tax_bps_kospi : float
    tax_bps_kosdaq : float
    verify_queue : bool
        If True, also run queue arithmetic verification.
        Only meaningful when bus was created with verbose=True.
    """
    from monitoring.events import (
        FillEvent as MonFillEvent,
        OrderSubmitEvent,
        CancelRequestEvent,
        QueueTickEvent,
    )
    from monitoring.verifiers.fee_verifier import verify_fill_fee
    from monitoring.verifiers.slippage_verifier import verify_slippage
    from monitoring.verifiers.latency_verifier import verify_latency_ordering
    from monitoring.verifiers.queue_verifier import verify_queue_arithmetic

    fill_events   = bus.query(MonFillEvent)
    submit_events = bus.query(OrderSubmitEvent)
    cancel_events = bus.query(CancelRequestEvent)
    queue_events  = bus.query(QueueTickEvent)

    # Index by child_id for join
    submit_by_id = {e.child_id: e for e in submit_events}
    cancel_by_id = {e.child_id: e for e in cancel_events}
    queue_by_id: dict[str, list] = {}
    for ev in queue_events:
        queue_by_id.setdefault(ev.child_id, []).append(ev)

    # --- Fee + Slippage ---
    fee_results = []
    slip_results = []
    for fill in fill_events:
        fee_results.append(
            verify_fill_fee(fill, commission_bps=commission_bps,
                            tax_bps_kospi=tax_bps_kospi, tax_bps_kosdaq=tax_bps_kosdaq)
        )
        slip_results.append(verify_slippage(fill))

    n_fills = len(fill_events)
    fee_pass  = sum(r.passed for r in fee_results)
    slip_pass = sum(r.passed for r in slip_results)

    # --- Latency ---
    latency_results = []
    all_child_ids = set(submit_by_id) | {f.child_id for f in fill_events} | set(cancel_by_id)
    for cid in all_child_ids:
        sub = submit_by_id.get(cid)
        fil = next((f for f in fill_events if f.child_id == cid), None)
        can = cancel_by_id.get(cid)
        latency_results.append(verify_latency_ordering(sub, fil, can))

    lat_pass = sum(r.passed for r in latency_results)
    n_lat    = len(latency_results)

    # --- Queue arithmetic (optional) ---
    queue_results = []
    if verify_queue:
        for cid, ticks in queue_by_id.items():
            ticks_sorted = sorted(ticks, key=lambda e: e.tick_index)
            queue_results.append(verify_queue_arithmetic(ticks_sorted))

    q_pass = sum(r.passed for r in queue_results)
    n_q    = len(queue_results)

    # Determine run_id from any event
    all_events_sample = fill_events or submit_events or cancel_events
    run_id = all_events_sample[0].run_id if all_events_sample else ""

    return BatchVerificationReport(
        n_fills          = n_fills,
        fee_pass_rate    = fee_pass / n_fills if n_fills > 0 else 1.0,
        fee_failures     = [r for r in fee_results if not r.passed],
        slippage_pass_rate = slip_pass / n_fills if n_fills > 0 else 1.0,
        slippage_failures  = [r for r in slip_results if not r.passed],
        latency_pass_rate  = lat_pass / n_lat if n_lat > 0 else 1.0,
        latency_failures   = [r for r in latency_results if not r.passed],
        queue_pass_rate    = q_pass / n_q if n_q > 0 else 1.0,
        queue_failures     = [r for r in queue_results if not r.passed],
        run_id             = run_id,
        generated_at       = pd.Timestamp.now(),
    )
