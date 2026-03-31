"""
monitoring/instrumented_pipeline_runner.py
-------------------------------------------
PipelineRunner subclass that wires InstrumentedFillSimulator and
emits TickStartEvent once per tick via _accumulate_state override.

No business logic is duplicated — only hooks are added.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from evaluation_orchestration.layer7_validation.pipeline_runner import PipelineRunner
from monitoring.event_bus import EventBus

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState


def _new_id() -> str:
    return str(uuid.uuid4())


class InstrumentedPipelineRunner(PipelineRunner):
    """
    PipelineRunner that collects monitoring events into an EventBus.

    Access the bus via ``runner.bus`` after construction.
    The run_id is re-generated at the start of each ``run()`` call
    to stay in sync with the base class.
    """

    def __init__(self, *args, bus: EventBus, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bus = bus
        self._monitoring_run_id: str = _new_id()
        self._monitoring_tick_counter: int = 0

    def _get_tick_index(self) -> int:
        return self._monitoring_tick_counter

    # ------------------------------------------------------------------
    # _setup_components: replace FillSimulator with instrumented version
    # ------------------------------------------------------------------

    def _setup_components(self, config) -> None:
        from monitoring.instrumented_fill_simulator import InstrumentedFillSimulator

        super()._setup_components(config)

        fs = self._fill_simulator
        self._fill_simulator = InstrumentedFillSimulator(
            matching_engine           = fs._matching_engine,
            order_book                = fs._order_book,
            latency_model             = fs._latency_model,
            fee_model                 = fs._fee_model,
            impact_model              = fs._impact_model,
            bookkeeper                = fs._bookkeeper,
            pnl_ledger                = fs._pnl_ledger,
            queue_model               = fs._queue_model,
            queue_position_assumption = fs._queue_position_assumption,
            rng_seed                  = config.seed,
            bus                       = self.bus,
            run_id                    = self._monitoring_run_id,
            tick_index_fn             = self._get_tick_index,
        )

    # ------------------------------------------------------------------
    # _accumulate_state: emit TickStartEvent then advance tick counter
    # ------------------------------------------------------------------

    def _accumulate_state(self, state: "MarketState") -> None:
        from monitoring.events import TickStartEvent

        super()._accumulate_state(state)

        # Observed state (uses delay already configured on the instance)
        effective_delay = self._effective_decision_delay_ms()
        observed = self._lookup_observed_state(
            state.symbol, state.timestamp, effective_delay
        )
        obs_mid     = observed.lob.mid_price if observed is not None else None
        staleness   = (
            (state.timestamp - observed.timestamp).total_seconds() * 1000.0
            if observed is not None else 0.0
        )

        lob = state.lob
        self.bus.emit(TickStartEvent(
            event_id            = _new_id(),
            run_id              = self._monitoring_run_id,
            tick_index          = self._monitoring_tick_counter,
            timestamp           = state.timestamp,
            symbol              = state.symbol,
            true_mid            = lob.mid_price,
            observed_mid        = obs_mid,
            staleness_ms        = staleness,
            lob_best_bid        = lob.best_bid,
            lob_best_ask        = lob.best_ask,
            lob_total_bid_depth = float(lob.total_bid_depth),
            lob_total_ask_depth = float(lob.total_ask_depth),
            last_trade_price    = lob.last_trade_price,
            last_trade_volume   = lob.last_trade_volume,
        ))
        self._monitoring_tick_counter += 1

    # ------------------------------------------------------------------
    # run: reset counters and sync run_id with base class
    # ------------------------------------------------------------------

    def run(self, states):
        self._monitoring_tick_counter = 0
        self._monitoring_run_id = _new_id()
        result = super().run(states)
        # Keep the monitoring run_id accessible on the result
        return result
