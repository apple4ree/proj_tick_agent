"""
pipeline_runner.py
------------------
Full backtest pipeline runner integrating all layers.

Orchestrates the complete simulation loop:
  Layer 0 (data) -> Layer 1 (signal) -> Layer 2 (position) ->
  Layer 3 (order) -> Layer 4 (execution) -> Layer 5 (simulator) ->
  Layer 6 (evaluation)

Data classes (BacktestConfig, BacktestResult) live in backtest_config.py.
Fill simulation logic lives in fill_simulator.py.
Report generation logic lives in report_builder.py.
"""
from __future__ import annotations

import bisect
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from strategy_block.strategy import Strategy
    from market_simulation.layer5_simulator.bookkeeper import FillEvent
    from execution_planning.layer3_order.order_types import ParentOrder

from evaluation_orchestration.layer7_validation.backtest_config import BacktestConfig, BacktestResult
from evaluation_orchestration.layer7_validation.fill_simulator import FillSimulator
from evaluation_orchestration.layer7_validation.report_builder import ReportBuilder

logger = logging.getLogger(__name__)


class PipelineRunner:
    """
    Orchestrates the full backtesting pipeline.

    매개변수
    ----------
    config : BacktestConfig
    data_dir : str | Path
    output_dir : str | Path | None
    strategy : Strategy | None
    """

    def __init__(
        self,
        config: BacktestConfig,
        data_dir: str | Path,
        output_dir: str | Path | None = None,
        strategy: "Strategy | None" = None,
    ) -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self._strategy = strategy

        # Layer components (initialized lazily in _setup_components)
        self._bookkeeper = None
        self._pnl_ledger = None
        self._risk_caps = None
        self._target_builder = None
        self._turnover_budget = None
        self._delta_computer = None
        self._order_constraints = None
        self._order_typer = None
        self._order_scheduler = None
        self._slicer = None
        self._placement_policy = None
        self._timing_logic = None
        self._guardrails = None
        self._cancel_replace = None
        self._micro_event_handler = None
        self._fill_simulator: FillSimulator | None = None
        self._report_builder: ReportBuilder | None = None

        # Order tracking state
        self._signal_history: dict[str, list[float]] = {}
        self._last_child_submission: dict[str, pd.Timestamp] = {}
        self._active_parent_orders: dict[str, "ParentOrder"] = {}
        self._open_child_orders: dict[str, list] = {}
        self._prev_state_by_symbol: dict[str, "MarketState"] = {}
        self._run_id: str | None = None

        # Observation-lag state history (Phase 1 realism)
        self._state_history: dict[str, list["MarketState"]] = {}
        self._state_ts: dict[str, list[pd.Timestamp]] = {}
        self._market_data_delay_ms: float = 0.0
        # Observation staleness accumulator (for reporting)
        self._staleness_sum_ms: float = 0.0
        self._staleness_count: int = 0
        # Canonical tick interval (ms): resample step duration.
        # Updated in run() from state metadata; default 1000.0 (1s).
        self._canonical_tick_ms: float = 1000.0

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Canonical tick interval
    # ------------------------------------------------------------------

    @staticmethod
    def _resample_freq_to_ms(freq: str | None) -> float:
        """Convert a resample frequency string to milliseconds.

        This is the *canonical tick interval* for the run: every tick-based
        parameter (``cancel_after_ticks``, ``holding_ticks``, ``LagExpr.steps``,
        etc.) represents this many wall-clock milliseconds per tick.

        Returns 1000.0 (1 s) when *freq* is ``None`` or unrecognised.
        """
        if freq == "500ms":
            return 500.0
        # Default / "1s" / unrecognised → 1 second
        return 1000.0

    def run(self, states: list["MarketState"]) -> BacktestResult:
        """Execute the full backtest pipeline over a sequence of market states."""
        import time as _time
        import uuid

        self._run_id = str(uuid.uuid4())
        logger.info(
            "Starting backtest run %s  symbol=%s  states=%d",
            self._run_id[:8], self.config.symbol, len(states),
        )

        from evaluation_orchestration.layer7_validation.reproducibility import ReproducibilityManager
        repro = ReproducibilityManager(seed=self.config.seed)
        repro.set_global_seed()

        # Derive canonical tick interval from state metadata
        resample_freq = states[0].meta.get("resample_freq") if states else None
        self._canonical_tick_ms = self._resample_freq_to_ms(resample_freq)

        t_setup_0 = _time.monotonic()
        self._setup_components(self.config)
        t_setup = _time.monotonic() - t_setup_0

        all_fills: list["FillEvent"] = []
        all_parent_orders: list["ParentOrder"] = []
        all_signals = []
        arrival_prices: dict[str, float] = {}
        twap_prices: dict[str, float] = {}
        portfolio_values: list[tuple[pd.Timestamp, float]] = []
        positions_history: list[dict] = []
        self._active_parent_orders.clear()
        self._open_child_orders.clear()
        self._prev_state_by_symbol.clear()
        self._state_history.clear()
        self._state_ts.clear()
        self._market_data_delay_ms = self.config.market_data_delay_ms
        self._staleness_sum_ms = 0.0
        self._staleness_count = 0

        # Running TWAP accumulators — O(1) per step instead of O(n)
        _twap_sum: dict[str, float] = {}
        _twap_count: dict[str, int] = {}

        # Pre-import modules used in hot loop to avoid per-step overhead
        from execution_planning.layer3_order.order_types import OrderStatus as _OrderStatus  # noqa: F841

        t_loop_0 = _time.monotonic()

        # --- Main simulation loop ---
        for t, state in enumerate(states):
            symbol = state.symbol
            true_state = state
            mid = true_state.lob.mid_price

            # Accumulate history BEFORE lookup so the current state is available
            self._accumulate_state(true_state)
            observed_state = self._lookup_observed_state(
                symbol, true_state.timestamp, self._market_data_delay_ms,
            )

            # Track actual observation staleness for reporting
            if observed_state is not None:
                staleness_ms = (true_state.timestamp - observed_state.timestamp).total_seconds() * 1000.0
                self._staleness_sum_ms += staleness_ms
                self._staleness_count += 1

            prev_state = self._prev_state_by_symbol.get(symbol)
            events = self._process_micro_events(prev_state, true_state)
            self._prev_state_by_symbol[symbol] = true_state

            active_parent = self._active_parent_orders.get(symbol)
            if active_parent is not None:
                lifecycle_fills = self._process_open_orders(
                    parent=active_parent,
                    true_state=true_state,
                    observed_state=observed_state,
                    events=events,
                )
                self._fill_simulator.record_fills(lifecycle_fills, mid, all_fills)
                if active_parent.is_complete:
                    self._active_parent_orders.pop(symbol, None)
                    self._open_child_orders.pop(symbol, None)
                    active_parent = None

            if not self._is_state_actionable(true_state, events):
                if mid is not None:
                    nav = self._bookkeeper.state.nav({symbol: mid})
                    portfolio_values.append((true_state.timestamp, nav))
                    positions_history.append(dict(self._bookkeeper.state.positions))
                    # O(1) running TWAP update
                    _twap_sum[symbol] = _twap_sum.get(symbol, 0.0) + mid
                    _twap_count[symbol] = _twap_count.get(symbol, 0) + 1
                    twap_prices[symbol] = _twap_sum[symbol] / _twap_count[symbol]
                continue

            mid = true_state.lob.mid_price
            if mid is None:
                continue

            parent = self._active_parent_orders.get(symbol)
            if parent is None:
                # Strategy decisions use observed (delayed) state
                signal = self._generate_signal(observed_state)
                if signal is not None:
                    all_signals.append(signal)
                    target_delta = self._compute_target_delta(signal, observed_state)
                    if target_delta != 0:
                        parent = self._create_parent_order(signal, target_delta, observed_state)
                        if parent is not None:
                            arrival_prices[symbol] = mid
                            all_parent_orders.append(parent)
                            self._active_parent_orders[symbol] = parent

            if parent is not None and self._parent_can_submit(parent):
                # Slicing uses observed state for pricing; fills use true state
                child_orders = self._slice_order(parent, observed_state)
                fills = self._fill_simulator.simulate_fills(parent, child_orders, true_state)
                self._sync_open_children(symbol, parent)
                self._fill_simulator.record_fills(fills, mid, all_fills)
                if parent.is_complete:
                    self._active_parent_orders.pop(symbol, None)
                    self._open_child_orders.pop(symbol, None)

            nav = self._bookkeeper.state.nav({symbol: mid})
            portfolio_values.append((true_state.timestamp, nav))
            positions_history.append(dict(self._bookkeeper.state.positions))
            # O(1) running TWAP update
            _twap_sum[symbol] = _twap_sum.get(symbol, 0.0) + mid
            _twap_count[symbol] = _twap_count.get(symbol, 0) + 1
            twap_prices[symbol] = _twap_sum[symbol] / _twap_count[symbol]

        t_loop = _time.monotonic() - t_loop_0

        logger.info("Simulation complete: %d fills, %d parent orders", len(all_fills), len(all_parent_orders))

        t_report_0 = _time.monotonic()
        result = self._report_builder.generate_reports(
            fills=all_fills,
            parent_orders=all_parent_orders,
            states=states,
            signals=all_signals,
            portfolio_values=portfolio_values,
            positions_history=positions_history,
            arrival_prices=arrival_prices,
            twap_prices=twap_prices,
            run_id=self._run_id,
        )
        t_report = _time.monotonic() - t_report_0

        t_save_0 = _time.monotonic()
        if self.output_dir is not None:
            self._report_builder.save_results(
                result, self.output_dir,
                signals=all_signals,
                parent_orders=all_parent_orders,
                fills=all_fills,
                states=states,
            )
        t_save = _time.monotonic() - t_save_0

        timings = {
            "setup_s": round(t_setup, 3),
            "loop_s": round(t_loop, 3),
            "report_s": round(t_report, 3),
            "save_s": round(t_save, 3),
            "total_s": round(t_setup + t_loop + t_report + t_save, 3),
        }
        result.metadata["timings"] = timings

        # Observation-lag diagnostics
        avg_staleness_ms = (
            round(self._staleness_sum_ms / self._staleness_count, 3)
            if self._staleness_count > 0 else 0.0
        )
        resample_freq = (
            states[0].meta.get("resample_freq") if states else None
        )
        result.metadata["observation_lag"] = {
            "configured_market_data_delay_ms": self._market_data_delay_ms,
            "resample_interval": resample_freq,
            "canonical_tick_interval_ms": self._canonical_tick_ms,
            "avg_observation_staleness_ms": avg_staleness_ms,
        }

        logger.info(
            "Pipeline timings: setup=%.1fs  loop=%.1fs  report=%.1fs  save=%.1fs  total=%.1fs",
            t_setup, t_loop, t_report, t_save,
            t_setup + t_loop + t_report + t_save,
        )

        return result

    # ------------------------------------------------------------------
    # Component setup
    # ------------------------------------------------------------------

    def _setup_components(self, config: BacktestConfig) -> None:
        """Initialize all layer components based on config."""
        from execution_planning.layer2_position import TurnoverBudget
        from execution_planning.layer3_order import DeltaComputer, OrderConstraints, OrderScheduler, OrderTyper
        from execution_planning.layer4_execution import CancelReplaceLogic, SafetyGuardrails, TimingLogic
        from market_simulation.layer5_simulator.bookkeeper import Bookkeeper
        from market_simulation.layer5_simulator import MicroEventHandler, OrderBookSimulator
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger
        from evaluation_orchestration.layer7_validation.component_factory import ComponentFactory

        # Core bookkeeping
        self._bookkeeper = Bookkeeper(initial_cash=config.initial_cash)
        self._pnl_ledger = PnLLedger()

        # Strategy (must be provided externally)
        if self._strategy is None:
            raise ValueError(
                "No strategy provided. Pass a Strategy instance to PipelineRunner()."
            )
        self._strategy.reset()

        # Risk & position sizing (from nested config)
        self._risk_caps = ComponentFactory.build_risk_caps(config.risk, config.initial_cash)
        self._target_builder = ComponentFactory.build_target_builder(config.risk)
        self._turnover_budget = TurnoverBudget()

        # Order management
        self._delta_computer = DeltaComputer()
        self._order_constraints = OrderConstraints()
        self._order_typer = OrderTyper()
        self._order_scheduler = OrderScheduler(default_algo=config.slicing.algo)

        # Execution policies (from nested config)
        self._slicer = ComponentFactory.build_slicer(config.slicing)
        self._placement_policy = ComponentFactory.build_placement_policy(config.placement)
        self._timing_logic = TimingLogic(interval_seconds=1.0)
        self._guardrails = SafetyGuardrails(max_single_child_pct=1.0)

        # Simulation components (from nested config)
        order_book = OrderBookSimulator()
        matching_engine = ComponentFactory.build_matching_engine(config.exchange, seed=config.seed)
        latency_model = ComponentFactory.build_latency_model(config.latency, seed=config.seed)
        fee_model = ComponentFactory.build_fee_model(config.fee)
        impact_model = ComponentFactory.build_impact_model(config.impact)
        queue_model = ComponentFactory.normalize_queue_model(config.exchange.queue_model)

        self._fill_simulator = FillSimulator(
            matching_engine=matching_engine,
            order_book=order_book,
            latency_model=latency_model,
            fee_model=fee_model,
            impact_model=impact_model,
            bookkeeper=self._bookkeeper,
            pnl_ledger=self._pnl_ledger,
            queue_model=queue_model,
            queue_position_assumption=config.exchange.queue_position_assumption,
            rng_seed=config.seed,
        )
        self._report_builder = ReportBuilder(config=config, pnl_ledger=self._pnl_ledger)

        # Event handling
        # tick_interval_ms = canonical tick interval (resample step),
        # NOT latency_ms.  cancel_after_ticks × tick_interval_ms gives the
        # wall-clock timeout in seconds.  See docs/analysis/tick_time_semantics_alignment.md.
        self._cancel_replace = CancelReplaceLogic(
            tick_interval_ms=self._canonical_tick_ms,
        )
        self._micro_event_handler = MicroEventHandler()

        # Clear state
        self._signal_history.clear()
        self._last_child_submission.clear()
        self._active_parent_orders.clear()
        self._open_child_orders.clear()
        self._prev_state_by_symbol.clear()
        self._state_history.clear()
        self._state_ts.clear()

        logger.debug("Components initialized: initial_cash=%.0f, seed=%d", config.initial_cash, config.seed)

    # ------------------------------------------------------------------
    # Observation-lag helpers
    # ------------------------------------------------------------------

    def _accumulate_state(self, state: "MarketState") -> None:
        """Append state to per-symbol history for observed_state lookup."""
        sym = state.symbol
        if sym not in self._state_history:
            self._state_history[sym] = []
            self._state_ts[sym] = []
        self._state_history[sym].append(state)
        self._state_ts[sym].append(state.timestamp)

    def _lookup_observed_state(
        self,
        symbol: str,
        current_ts: "pd.Timestamp",
        delay_ms: float,
    ) -> "MarketState":
        """Return the latest state at or before ``current_ts - delay_ms``.

        When ``delay_ms == 0`` the most recent state is returned directly
        (fast-path, preserves existing behaviour).
        """
        history = self._state_history.get(symbol)
        if not history:
            return None  # type: ignore[return-value]  # caller guards

        if delay_ms <= 0.0:
            return history[-1]

        target_ts = current_ts - pd.Timedelta(milliseconds=delay_ms)
        ts_list = self._state_ts[symbol]
        idx = bisect.bisect_right(ts_list, target_ts) - 1
        if idx < 0:
            # No state old enough — fall back to the earliest available.
            return history[0]
        return history[idx]

    # ------------------------------------------------------------------
    # Signal & position helpers
    # ------------------------------------------------------------------

    def _generate_signal(self, state: "MarketState"):
        if self._strategy is None:
            return None
        return self._strategy.generate_signal(state)

    def _compute_target_delta(self, signal, state: "MarketState") -> int:
        if signal is None:
            return 0

        current_positions = dict(self._bookkeeper.state.positions)
        mid = state.lob.mid_price or 1.0
        prices = {state.symbol: mid}
        portfolio_value = self._bookkeeper.state.nav(prices)

        target = self._target_builder.build(
            signals=[signal],
            current_positions=current_positions,
            risk_caps=self._risk_caps,
            prices=prices,
            portfolio_value=portfolio_value,
        )

        if not self._turnover_budget.is_within_budget(
            current=current_positions,
            targets=target.targets,
            prices=prices,
            portfolio_value=portfolio_value,
        ):
            target.targets = self._turnover_budget.throttle(
                current=current_positions,
                targets=target.targets,
                prices=prices,
                portfolio_value=portfolio_value,
            )

        deltas = self._delta_computer.compute(target, current_positions)
        return deltas.get(state.symbol, 0)

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_execution_hints(tags: dict[str, object]) -> dict[str, object]:
        hints: dict[str, object] = {}

        mode_raw = tags.get("placement_mode")
        mode = mode_raw.strip().lower() if isinstance(mode_raw, str) else None
        if mode is not None:
            hints["placement_mode"] = mode

        cancel_after_ticks_raw = tags.get("cancel_after_ticks")
        if isinstance(cancel_after_ticks_raw, (int, float)):
            cancel_after_ticks = max(1, int(cancel_after_ticks_raw))
            if mode == "passive_join":
                # avoid over-frequent timeout churn for short-horizon scalping hints
                cancel_after_ticks = max(3, cancel_after_ticks)
            hints["cancel_after_ticks"] = cancel_after_ticks

        max_reprices_raw = tags.get("max_reprices")
        if isinstance(max_reprices_raw, (int, float)):
            hints["max_reprices"] = max(0, int(max_reprices_raw))

        return hints

    def _create_parent_order(self, signal, delta: int, state: "MarketState"):
        if delta == 0:
            return None

        parent = self._delta_computer.to_parent_order(
            symbol=state.symbol,
            delta_qty=delta,
            urgency=abs(signal.score) if signal is not None else 0.5,
            state=state,
        )
        order_type, tif = self._order_typer.determine_type(parent, state)
        parent.limit_price = self._order_typer.determine_limit_price(parent, state, order_type)
        parent.meta["suggested_order_type"] = order_type.value
        parent.meta["suggested_tif"] = tif.value
        parent.meta["scheduling_hint"] = repr(self._order_scheduler.create_hint(parent, state))

        hints: dict[str, object] = {}
        if signal is not None and getattr(signal, "tags", None):
            hints = self._normalize_execution_hints(signal.tags)
        if hints:
            parent.meta["execution_hints"] = hints

        parent = self._order_constraints.apply_all(parent, state)
        if parent.status.name == "REJECTED" or parent.total_qty <= 0:
            return None
        return parent

    def _slice_order(self, parent: "ParentOrder", state: "MarketState") -> list:
        self._timing_logic.update_baseline(state)
        should_send, trigger = self._timing_logic.should_send(
            parent=parent,
            state=state,
            current_time=state.timestamp,
            last_sent=self._last_child_submission.get(parent.symbol),
        )
        if not should_send:
            return []

        # Effective remaining = parent remaining minus qty already
        # committed to active (unfilled) children.  Without this,
        # multiple slicing rounds can create children whose aggregate
        # qty exceeds parent.total_qty, causing overfill.
        in_flight_qty = sum(
            c.remaining_qty for c in parent.child_orders if c.is_active
        )
        effective_remaining = parent.remaining_qty - in_flight_qty
        if effective_remaining <= 0:
            return []

        if hasattr(self._slicer, "next_qty"):
            qty = self._slicer.next_qty(effective_remaining, state)
        else:
            schedule = self._slicer.generate_schedule(parent, [state])
            qty = schedule[0][1] if schedule else 0

        # Cap to effective remaining so we never exceed parent.total_qty
        qty = min(qty, effective_remaining)
        if qty <= 0:
            return []

        from execution_planning.layer4_execution.placement_policy import resolve_placement_policy

        hints = parent.meta.get("execution_hints", {}) if parent.meta else {}
        effective_placement = resolve_placement_policy(self._placement_policy, hints)
        child = effective_placement.place(parent, qty, state)
        child.meta["placement_policy"] = effective_placement.name
        child.submitted_time = state.timestamp
        child.submit_time = state.timestamp
        child.arrival_mid = parent.arrival_mid
        child.meta.setdefault("reprice_count", 0)
        if hints:
            child.meta["execution_hints"] = dict(hints)
        if trigger is not None:
            child.meta["timing_trigger"] = trigger.value

        violations = self._guardrails.validate_child(
            child=child, parent=parent, state=state,
            n_open=sum(1 for existing in parent.child_orders if existing.is_active),
        )
        blocking = [v for v in violations if v.severity in {"error", "critical"}]
        if blocking:
            parent.meta["guardrail_violations"] = [v.details for v in blocking]
            return []

        parent.child_orders.append(child)
        self._last_child_submission[parent.symbol] = state.timestamp
        return [child]

    def _process_micro_events(self, prev_state, state):
        if prev_state is None or prev_state.symbol != state.symbol:
            return []
        return self._micro_event_handler.process(prev_state, state)

    def _is_state_actionable(self, state, events):
        return self._micro_event_handler.is_tradable(state, events)

    def _process_open_orders(self, parent, true_state, observed_state, events):
        """Process open child orders: cancel/replace decisions use observed_state,
        fills and exchange-side checks use true_state."""
        from execution_planning.layer3_order.order_types import OrderStatus
        symbol = parent.symbol
        open_children = self._open_child_orders.get(symbol, [])
        if not open_children:
            return []

        if not self._is_state_actionable(true_state, events):
            for child in self._micro_event_handler.cancel_orders_on_halt(open_children, events):
                self._cancel_child(child, true_state, reason="micro_event_block")
            self._sync_open_children(symbol, parent)
            return []

        hints = parent.meta.get("execution_hints", {}) if parent.meta else {}
        cancel_after_ticks = hints.get("cancel_after_ticks") if isinstance(hints, dict) else None
        max_reprices = hints.get("max_reprices") if isinstance(hints, dict) else None
        placement_mode = hints.get("placement_mode") if isinstance(hints, dict) else None

        # Cancel/replace decisions use observed (delayed) market data
        actions = self._cancel_replace.process_open_orders(
            open_orders=open_children,
            state=observed_state,
            current_time=observed_state.timestamp,
            cancel_after_ticks=(int(cancel_after_ticks) if isinstance(cancel_after_ticks, (int, float)) else None),
            max_reprices=(int(max_reprices) if isinstance(max_reprices, (int, float)) else None),
            placement_mode=(str(placement_mode) if isinstance(placement_mode, str) else None),
        )

        executable_children = []
        for action in actions:
            child = action["order"]
            decision = action["action"]
            if decision == "cancel":
                self._cancel_child(child, true_state, reason=action["reason"])
                continue
            if decision == "replace":
                replacement = self._replace_child_order(
                    parent=parent, child=child, state=true_state,
                    new_price=action["new_price"], reason=action["reason"],
                )
                if replacement is not None:
                    executable_children.append(replacement)
                continue
            if child.status == OrderStatus.PENDING:
                child.status = OrderStatus.OPEN
            executable_children.append(child)

        # Fill execution uses true (current) market state
        fills = self._fill_simulator.simulate_fills(parent, executable_children, true_state)
        self._sync_open_children(symbol, parent)
        return fills

    def _cancel_child(self, child, state, reason):
        from execution_planning.layer3_order.order_types import OrderStatus
        child.status = OrderStatus.CANCELLED
        child.cancel_time = state.timestamp
        child.meta["cancel_reason"] = reason

    def _replace_child_order(self, parent, child, state, new_price, reason):
        from execution_planning.layer3_order.order_types import ChildOrder
        remaining_qty = child.remaining_qty
        self._cancel_child(child, state, reason=f"replace:{reason}")
        if remaining_qty <= 0 or new_price is None:
            return None

        replacement_child = ChildOrder.create(
            parent=parent, order_type=child.order_type,
            qty=remaining_qty, price=new_price, tif=child.tif,
            submitted_time=state.timestamp, arrival_mid=child.arrival_mid,
        )
        replacement_child.meta["replaces"] = child.child_id
        replacement_child.meta["replace_reason"] = reason
        prev_reprices = int(child.meta.get("reprice_count", 0))
        replacement_child.meta["reprice_count"] = prev_reprices + 1
        if parent.meta and isinstance(parent.meta.get("execution_hints"), dict):
            replacement_child.meta["execution_hints"] = dict(parent.meta["execution_hints"])
        parent.child_orders.append(replacement_child)
        self._last_child_submission[parent.symbol] = state.timestamp
        return replacement_child

    def _sync_open_children(self, symbol, parent):
        active_children = [child for child in parent.child_orders if child.is_active]
        if active_children:
            self._open_child_orders[symbol] = active_children
        else:
            self._open_child_orders.pop(symbol, None)

    def _parent_can_submit(self, parent):
        from execution_planning.layer3_order.order_types import OrderStatus
        return (
            parent.remaining_qty > 0
            and parent.status not in {OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FILLED}
        )

    @staticmethod
    def _compute_running_twap(symbol, states):
        mids = [
            state.lob.mid_price
            for state in states
            if state.symbol == symbol and state.lob.mid_price is not None
        ]
        if not mids:
            return 0.0
        return float(np.mean(mids))

    # Backward compat wrappers for tests that access these directly
    def _simulate_fills(self, parent, child_orders, state):
        return self._fill_simulator.simulate_fills(parent, child_orders, state)

    def _record_fills(self, fills, mid, all_fills):
        self._fill_simulator.record_fills(fills, mid, all_fills)

    def save_results(self, result, output_dir):
        self._report_builder.save_results(result, output_dir)
