"""
monitoring/instrumented_fill_simulator.py
------------------------------------------
FillSimulator subclass that emits monitoring events to an EventBus.

All event emission happens around super() calls — no business logic is
duplicated or altered.  The base FillSimulator behaviour is unchanged.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pandas as pd

from evaluation_orchestration.layer7_validation.fill_simulator import FillSimulator

if TYPE_CHECKING:
    from monitoring.event_bus import EventBus


def _new_id() -> str:
    return str(uuid.uuid4())


class InstrumentedFillSimulator(FillSimulator):
    """FillSimulator that emits monitoring events to the provided EventBus."""

    def __init__(
        self,
        *args,
        bus: "EventBus",
        run_id: str,
        tick_index_fn,   # callable() -> int
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._bus = bus
        self._run_id = run_id
        self._tick_index_fn = tick_index_fn

    # ------------------------------------------------------------------
    # Submit / cancel hooks
    # ------------------------------------------------------------------

    def register_submit_request(self, child, request_time: pd.Timestamp) -> None:
        super().register_submit_request(child, request_time)
        from monitoring.events import OrderSubmitEvent
        # Only emit if the submit was actually registered (meta has the key)
        if not isinstance(child.meta, dict):
            return
        if child.meta.get("submit_request_time") is None:
            return
        self._bus.emit(OrderSubmitEvent(
            event_id            = _new_id(),
            run_id              = self._run_id,
            tick_index          = self._tick_index_fn(),
            timestamp           = request_time,
            child_id            = child.child_id,
            parent_id           = child.parent_id,
            symbol              = child.symbol,
            side                = child.side.value,
            order_type          = child.order_type.value,
            tif                 = child.tif.value,
            qty                 = child.qty,
            price               = child.price,
            submit_request_time = child.meta["submit_request_time"],
            venue_arrival_time  = child.meta["venue_arrival_time"],
            ack_time            = child.meta["ack_time"],
            submit_latency_ms   = child.meta["submit_latency_ms"],
            ack_latency_ms      = child.meta["ack_latency_ms"],
            placement_policy    = child.meta.get("placement_policy"),
            is_passive_candidate= bool(child.meta.get("is_passive_candidate", False)),
        ))

    def register_cancel_request(
        self, child, request_time: pd.Timestamp, reason: str
    ) -> None:
        super().register_cancel_request(child, request_time, reason)
        from monitoring.events import CancelRequestEvent
        if not isinstance(child.meta, dict):
            return
        if child.meta.get("cancel_requested_time") is None:
            return
        self._bus.emit(CancelRequestEvent(
            event_id             = _new_id(),
            run_id               = self._run_id,
            tick_index           = self._tick_index_fn(),
            timestamp            = request_time,
            child_id             = child.child_id,
            parent_id            = child.parent_id,
            symbol               = child.symbol,
            cancel_requested_time= child.meta["cancel_requested_time"],
            cancel_effective_time= child.meta["cancel_effective_time"],
            cancel_latency_ms    = child.meta["cancel_latency_ms"],
            reason               = reason,
        ))

    # ------------------------------------------------------------------
    # Queue hooks
    # ------------------------------------------------------------------

    def _initialize_queue_state(self, child, state) -> None:
        super()._initialize_queue_state(child, state)
        from monitoring.events import QueueInitEvent
        self._bus.emit(QueueInitEvent(
            event_id                  = _new_id(),
            run_id                    = self._run_id,
            tick_index                = self._tick_index_fn(),
            timestamp                 = state.timestamp,
            child_id                  = child.child_id,
            parent_id                 = child.parent_id,
            symbol                    = child.symbol,
            side                      = child.side.value,
            order_price               = child.price,
            queue_model               = child.queue_model,
            queue_position_assumption = self._queue_position_assumption,
            initial_level_qty         = child.initial_level_qty,
            queue_ahead_qty_init      = child.queue_ahead_qty,
        ))

    def _advance_queue_and_ready(self, child, state) -> bool:
        from monitoring.events import QueueTickEvent

        # Capture before state
        qa_before        = float(child.queue_ahead_qty)
        prev_level_qty   = max(0.0, float(child.queue_last_level_qty))
        same_trade_qty   = float(self._same_level_trade_qty(child, state))
        curr_level_qty   = max(0.0, float(self._level_qty_for_price(child, state)))
        depth_drop       = max(0.0, prev_level_qty - curr_level_qty)
        unexplained      = max(0.0, depth_drop - same_trade_qty)

        gate_passed = super()._advance_queue_and_ready(child, state)

        qa_after    = float(child.queue_ahead_qty)
        trade_adv   = min(qa_before, same_trade_qty)
        # depth advancement = whatever reduction is left after trade advancement
        after_trade = max(0.0, qa_before - trade_adv)
        depth_adv   = max(0.0, after_trade - qa_after)

        self._bus.emit(QueueTickEvent(
            event_id               = _new_id(),
            run_id                 = self._run_id,
            tick_index             = self._tick_index_fn(),
            timestamp              = state.timestamp,
            child_id               = child.child_id,
            parent_id              = child.parent_id,
            symbol                 = child.symbol,
            order_price            = child.price,
            queue_ahead_before     = qa_before,
            same_level_trade_qty   = same_trade_qty,
            prev_level_qty         = prev_level_qty,
            curr_level_qty         = curr_level_qty,
            depth_drop             = depth_drop,
            unexplained_depth_drop = unexplained,
            trade_advancement      = trade_adv,
            depth_advancement      = depth_adv,
            queue_ahead_after      = qa_after,
            gate_passed            = gate_passed,
            queue_model            = child.queue_model,
        ))
        return gate_passed

    # ------------------------------------------------------------------
    # Fill hook
    # ------------------------------------------------------------------

    def simulate_fills(self, parent, child_orders, state) -> list:
        from monitoring.events import FillEvent as MonFillEvent
        from monitoring.verifiers.fee_verifier import verify_fill_fee
        from monitoring.verifiers.slippage_verifier import verify_slippage

        fills = super().simulate_fills(parent, child_orders, state)

        if not fills:
            return fills

        tick_idx    = self._tick_index_fn()
        mid_at_fill = state.lob.mid_price
        child_by_id = {c.child_id: c for c in child_orders}

        for fill in fills:
            child = child_by_id.get(fill.order_id)
            arrival_mid = (child.arrival_mid if child is not None else None) or mid_at_fill

            # Bridge: create a thin adapter so verifiers can access needed attrs
            class _Adapter:
                child_id      = fill.order_id
                tick_index    = tick_idx
                filled_qty    = fill.filled_qty
                impacted_price= fill.fill_price
                fee           = fill.fee
                side          = fill.side
                is_maker      = fill.is_maker
                slippage_bps  = fill.slippage_bps

            fee_res  = verify_fill_fee(_Adapter)
            slip_res = verify_slippage(
                _Adapter,
                arrival_mid    = arrival_mid,
                impacted_price = fill.fill_price,
                side           = fill.side,
            )

            # Queue wait — computed from enter timestamp if queued
            queue_wait_ms    = 0.0
            queue_wait_ticks = 0.0
            if child is not None:
                enter_ts = getattr(child, "queue_enter_ts", None)
                if enter_ts is not None:
                    wait_ms      = max(0.0, (state.timestamp - enter_ts).total_seconds() * 1000.0)
                    tick_ms      = float(child.meta.get("canonical_tick_interval_ms", 0.0)) if isinstance(child.meta, dict) else 0.0
                    queue_wait_ms    = wait_ms
                    queue_wait_ticks = (wait_ms / tick_ms) if tick_ms > 0.0 else 0.0

            notional    = float(fill.filled_qty) * float(fill.fill_price)
            fee_err_bps = (fee_res.error_krw / max(1.0, notional)) * 10_000.0

            self._bus.emit(MonFillEvent(
                event_id             = _new_id(),
                run_id               = self._run_id,
                tick_index           = tick_idx,
                timestamp            = state.timestamp,
                child_id             = fill.order_id,
                parent_id            = fill.parent_id,
                symbol               = fill.symbol,
                side                 = fill.side.value,
                filled_qty           = fill.filled_qty,
                matched_price_raw    = fill.fill_price,
                impacted_price       = fill.fill_price,
                arrival_mid          = arrival_mid,
                mid_at_fill          = mid_at_fill,
                fee                  = fill.fee,
                is_maker             = fill.is_maker,
                slippage_bps         = fill.slippage_bps,
                impact_bps           = fill.market_impact_bps,
                latency_ms           = fill.latency_ms,
                expected_fee         = fee_res.expected_fee,
                expected_slippage_bps= slip_res.expected_bps,
                fee_error_bps        = fee_err_bps,
                slippage_error_bps   = slip_res.error_bps,
                queue_wait_ticks     = queue_wait_ticks,
                queue_wait_ms        = queue_wait_ms,
            ))

        return fills
