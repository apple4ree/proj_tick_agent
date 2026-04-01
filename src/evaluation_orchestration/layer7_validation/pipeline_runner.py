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
from collections import Counter
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

    _HISTORY_MIN_LEN = 20
    _HISTORY_SAFETY_TICKS = 10

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
        # Decision latency (Phase 2 realism): strategy compute time (ms).
        # Combined with observation lag for effective state lookup delay.
        self._decision_compute_ms: float = 0.0
        # Observation staleness accumulator (for reporting)
        self._staleness_sum_ms: float = 0.0
        self._staleness_count: int = 0
        self._staleness_max_ms: float = 0.0
        # Decision-evaluated state-age accumulator (actual decision steps only).
        self._decision_age_sum_ms: float = 0.0
        self._decision_age_count: int = 0
        self._decision_age_max_ms: float = 0.0
        # Canonical tick interval (ms): resample step duration.
        # Updated in run() from state metadata; default 1000.0 (1s).
        self._canonical_tick_ms: float = 1000.0
        # Bounded state-history retention: maximum number of states to keep
        # per symbol.  Computed in run() from delay settings and tick interval.
        # 0 = unbounded (only when no delay is configured).
        self._max_history_len: int = 0
        # Strategy/runtime lookback requirement inferred from LagExpr /
        # RollingExpr / PersistExpr in compiled v2 specs.
        self._strategy_lookback_ticks: int = 0

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

    def _effective_decision_delay_ms(self) -> float:
        """Return effective decision-path stale-state delay in milliseconds."""
        return max(0.0, float(self._market_data_delay_ms)) + max(0.0, float(self._decision_compute_ms))

    def _iter_strategy_expr_roots(self) -> list[Any]:
        """Collect expression roots from a compiled v2 strategy spec.

        Returns an empty list when strategy/spec metadata is unavailable.
        """
        strategy = self._strategy
        spec = getattr(strategy, "_spec", None) if strategy is not None else None
        if spec is None:
            return []

        roots: list[Any] = []

        def _add(node: Any) -> None:
            if node is not None:
                roots.append(node)

        for pc in getattr(spec, "preconditions", []) or []:
            _add(getattr(pc, "condition", None))
        for ep in getattr(spec, "entry_policies", []) or []:
            _add(getattr(ep, "trigger", None))
            _add(getattr(ep, "strength", None))
        for xp in getattr(spec, "exit_policies", []) or []:
            for rule in getattr(xp, "rules", []) or []:
                _add(getattr(rule, "condition", None))

        risk_policy = getattr(spec, "risk_policy", None)
        if risk_policy is not None:
            for rr in getattr(risk_policy, "degradation_rules", []) or []:
                _add(getattr(rr, "condition", None))

        execution_policy = getattr(spec, "execution_policy", None)
        if execution_policy is not None:
            _add(getattr(execution_policy, "do_not_trade_when", None))
            for ar in getattr(execution_policy, "adaptation_rules", []) or []:
                _add(getattr(ar, "condition", None))

        for regime in getattr(spec, "regimes", []) or []:
            _add(getattr(regime, "when", None))
            regime_risk = getattr(regime, "risk_override", None)
            if regime_risk is not None:
                for rr in getattr(regime_risk, "degradation_rules", []) or []:
                    _add(getattr(rr, "condition", None))
            regime_exec = getattr(regime, "execution_override", None)
            if regime_exec is not None:
                _add(getattr(regime_exec, "do_not_trade_when", None))
                for ar in getattr(regime_exec, "adaptation_rules", []) or []:
                    _add(getattr(ar, "condition", None))

        state_policy = getattr(spec, "state_policy", None)
        if state_policy is not None:
            for guard in getattr(state_policy, "guards", []) or []:
                _add(getattr(guard, "condition", None))

        return roots

    def _expr_required_lookback_ticks(self, node: Any, visited: set[int] | None = None) -> int:
        """Conservative lookback in ticks required by a single expression tree."""
        if node is None:
            return 0

        if visited is None:
            visited = set()
        node_id = id(node)
        if node_id in visited:
            return 0
        visited.add(node_id)

        node_type = getattr(node, "type", "")
        max_ticks = 0
        if node_type == "lag":
            max_ticks = max(max_ticks, max(0, int(getattr(node, "steps", 0) or 0)))
        elif node_type == "rolling":
            max_ticks = max(max_ticks, max(0, int(getattr(node, "window", 0) or 0)))
        elif node_type == "persist":
            max_ticks = max(max_ticks, max(0, int(getattr(node, "window", 0) or 0)))
        elif node_type == "cross":
            max_ticks = max(max_ticks, 1)

        for attr_name in ("expr", "left", "child"):
            child = getattr(node, attr_name, None)
            if child is not None:
                max_ticks = max(max_ticks, self._expr_required_lookback_ticks(child, visited))

        children = getattr(node, "children", None)
        if isinstance(children, list):
            for child in children:
                max_ticks = max(max_ticks, self._expr_required_lookback_ticks(child, visited))

        return max_ticks

    def _strategy_runtime_lookback_ticks(self) -> int:
        """Infer strategy/runtime lookback depth from compiled v2 expressions."""
        max_ticks = 0
        for node in self._iter_strategy_expr_roots():
            max_ticks = max(max_ticks, self._expr_required_lookback_ticks(node))
        return max_ticks

    def _compute_history_retention_len(self) -> int:
        """Compute bounded state-history length for observed-state lookup."""
        tick_ms = self._canonical_tick_ms if self._canonical_tick_ms > 0.0 else 1000.0
        delay_ticks = int(math.ceil(self._effective_decision_delay_ms() / tick_ms)) + 1
        self._strategy_lookback_ticks = self._strategy_runtime_lookback_ticks()
        required = delay_ticks + self._strategy_lookback_ticks + self._HISTORY_SAFETY_TICKS
        return max(required, self._HISTORY_MIN_LEN)

    def _annotate_decision_metadata(
        self,
        meta: dict[str, Any],
        *,
        true_state: "MarketState",
        observed_state: "MarketState",
        phase: str,
    ) -> None:
        """Stamp decision-time context into order metadata."""
        state_age_ms = (true_state.timestamp - observed_state.timestamp).total_seconds() * 1000.0
        meta["decision_phase"] = phase
        meta["configured_market_data_delay_ms"] = float(getattr(self, "_market_data_delay_ms", 0.0))
        meta["configured_decision_compute_ms"] = float(getattr(self, "_decision_compute_ms", 0.0))
        meta["decision_latency_enabled"] = bool(getattr(self, "_decision_compute_ms", 0.0) > 0.0)
        meta["effective_delay_ms"] = self._effective_decision_delay_ms()
        meta["decision_true_ts"] = true_state.timestamp
        meta["decision_observed_ts"] = observed_state.timestamp
        meta["decision_state_age_ms"] = state_age_ms

        self._decision_age_sum_ms += float(state_age_ms)
        self._decision_age_count += 1
        self._decision_age_max_ms = max(self._decision_age_max_ms, float(state_age_ms))

    
    
    
    
    @staticmethod
    def _cancel_reason_bucket(reason: str | None) -> str:
        raw = (reason or "").strip().lower()
        if not raw:
            return "unknown"
        if "micro_event_block" in raw:
            return "micro_event_block"
        if "max_reprices_reached" in raw:
            return "max_reprices_reached"
        if "adverse_selection" in raw:
            return "adverse_selection"
        if "timeout" in raw:
            return "timeout"
        if "stale_price" in raw or "price_very_stale" in raw:
            return "stale_price"
        return "unknown"

    def _aggregate_cancel_reasons(self, parent_orders: list["ParentOrder"]) -> dict[str, dict[str, float]]:
        buckets = (
            "timeout",
            "adverse_selection",
            "stale_price",
            "max_reprices_reached",
            "micro_event_block",
            "unknown",
        )
        counts = Counter({k: 0 for k in buckets})
        for parent in parent_orders:
            for child in parent.child_orders:
                if child.status.name != "CANCELLED":
                    continue
                reason = child.meta.get("cancel_reason") if isinstance(child.meta, dict) else None
                counts[self._cancel_reason_bucket(reason)] += 1

        total = sum(counts.values())
        shares = {k: (float(counts[k]) / float(total) if total > 0 else 0.0) for k in buckets}
        return {
            "counts": {k: float(counts[k]) for k in buckets},
            "shares": shares,
        }

    def _aggregate_lifecycle(
        self,
        parent_orders: list["ParentOrder"],
        signals: list,
        fills: list["FillEvent"],
    ) -> dict[str, float | str | None]:
        child_orders = [child for parent in parent_orders for child in parent.child_orders]

        lifetime_seconds: list[float] = []
        for child in child_orders:
            start_ts = child.submit_time or child.submitted_time
            end_ts = child.fill_time or child.cancel_time
            if start_ts is None or end_ts is None:
                continue
            dt = (end_ts - start_ts).total_seconds()
            if dt >= 0.0:
                lifetime_seconds.append(float(dt))

        parent_count = len(parent_orders)
        child_count = len(child_orders)
        fill_count = len(fills)
        avg_child_lifetime = (
            float(sum(lifetime_seconds) / len(lifetime_seconds))
            if lifetime_seconds else 0.0
        )

        max_children_per_parent = 0
        max_cancelled_children_per_parent = 0
        top_parent_by_children: str | None = None
        top_parent_by_cancelled: str | None = None

        for parent in parent_orders:
            n_children = len(parent.child_orders)
            n_cancelled = sum(1 for child in parent.child_orders if child.status.name == "CANCELLED")
            if n_children > max_children_per_parent:
                max_children_per_parent = n_children
                top_parent_by_children = str(parent.order_id)
            if n_cancelled > max_cancelled_children_per_parent:
                max_cancelled_children_per_parent = n_cancelled
                top_parent_by_cancelled = str(parent.order_id)

        return {
            "signal_count": float(len(signals)),
            "parent_order_count": float(parent_count),
            "child_order_count": float(child_count),
            "n_fills": float(fill_count),
            "avg_child_lifetime_seconds": avg_child_lifetime,
            "children_per_parent": (float(child_count) / float(parent_count) if parent_count > 0 else 0.0),
            "fills_per_parent": (float(fill_count) / float(parent_count) if parent_count > 0 else 0.0),
            "max_children_per_parent": float(max_children_per_parent),
            "max_cancelled_children_per_parent": float(max_cancelled_children_per_parent),
            "top_parent_by_children": top_parent_by_children,
            "top_parent_by_cancelled_children": top_parent_by_cancelled,
        }

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
        self._decision_compute_ms = getattr(self.config, "decision_compute_ms", 0.0)
        self._staleness_sum_ms = 0.0
        self._staleness_count = 0
        self._staleness_max_ms = 0.0
        self._decision_age_sum_ms = 0.0
        self._decision_age_count = 0
        self._decision_age_max_ms = 0.0
        self._strategy_lookback_ticks = 0

        # Bounded state-history retention: effective delay + strategy runtime
        # lookback (LagExpr/RollingExpr/PersistExpr) + safety buffer.
        effective_delay_ms = self._effective_decision_delay_ms()
        self._max_history_len = self._compute_history_retention_len()

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
                symbol, true_state.timestamp, effective_delay_ms,
            )

            # Track actual observation staleness for reporting
            if observed_state is not None:
                staleness_ms = (true_state.timestamp - observed_state.timestamp).total_seconds() * 1000.0
                self._staleness_sum_ms += staleness_ms
                self._staleness_count += 1
                self._staleness_max_ms = max(self._staleness_max_ms, staleness_ms)

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
                    if t % 60 == 0:
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
                        parent = self._create_parent_order(signal, target_delta, observed_state, true_state)
                        if parent is not None:
                            arrival_prices[symbol] = mid
                            all_parent_orders.append(parent)
                            self._active_parent_orders[symbol] = parent

            if parent is not None and self._parent_can_submit(parent):
                # Slicing uses observed state for pricing; fills use true state
                child_orders = self._slice_order(parent, observed_state, true_state)
                fills = self._fill_simulator.simulate_fills(parent, child_orders, true_state)
                self._sync_open_children(symbol, parent)
                self._fill_simulator.record_fills(fills, mid, all_fills)
                if parent.is_complete:
                    self._active_parent_orders.pop(symbol, None)
                    self._open_child_orders.pop(symbol, None)

            nav = self._bookkeeper.state.nav({symbol: mid})
            portfolio_values.append((true_state.timestamp, nav))
            if t % 60 == 0:
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

        base_timings = {
            "setup_s": round(t_setup, 3),
            "loop_s": round(t_loop, 3),
            "report_s": round(t_report, 3),
            "save_s": 0.0,
            "total_s": round(t_setup + t_loop + t_report, 3),
        }
        result.metadata["timings"] = dict(base_timings)

        avg_staleness_ms = (
            round(self._staleness_sum_ms / self._staleness_count, 3)
            if self._staleness_count > 0 else 0.0
        )
        max_staleness_ms = round(self._staleness_max_ms, 3) if self._staleness_count > 0 else 0.0
        staleness_samples = int(self._staleness_count)
        avg_decision_age_ms = (
            round(self._decision_age_sum_ms / self._decision_age_count, 3)
            if self._decision_age_count > 0 else 0.0
        )
        max_decision_age_ms = round(self._decision_age_max_ms, 3) if self._decision_age_count > 0 else 0.0
        decision_state_samples = int(self._decision_age_count)
        resample_interval = states[0].meta.get("resample_freq") if states else None

        observation_lag = {
            "configured_market_data_delay_ms": float(self._market_data_delay_ms),
            "avg_observation_staleness_ms": avg_staleness_ms,
            "max_observation_staleness_ms": max_staleness_ms,
            "staleness_samples_count": staleness_samples,
            "effective_delay_ms": float(effective_delay_ms),
            "resample_interval": resample_interval,
            "canonical_tick_interval_ms": float(self._canonical_tick_ms),
            "configured_decision_compute_ms": float(self._decision_compute_ms),
            "decision_latency_enabled": bool(self._decision_compute_ms > 0.0),
            "avg_decision_state_age_ms": avg_decision_age_ms,
            "decision_state_samples_count": decision_state_samples,
            "state_history_max_len": int(self._max_history_len),
            "strategy_runtime_lookback_ticks": int(self._strategy_lookback_ticks),
            "history_safety_buffer_ticks": int(self._HISTORY_SAFETY_TICKS),
        }
        result.metadata["observation_lag"] = observation_lag

        lifecycle = self._aggregate_lifecycle(all_parent_orders, all_signals, all_fills)
        lifecycle["cancel_rate"] = float(result.execution_report.cancel_rate)
        lifecycle["avg_holding_seconds"] = float(result.turnover_report.avg_holding_period) * (self._canonical_tick_ms / 1000.0)

        queue_diag = (
            self._fill_simulator.queue_diagnostics()
            if self._fill_simulator is not None and hasattr(self._fill_simulator, "queue_diagnostics")
            else {}
        )
        latency_diag = (
            self._fill_simulator.latency_diagnostics()
            if self._fill_simulator is not None and hasattr(self._fill_simulator, "latency_diagnostics")
            else {}
        )

        queue_model_cfg = self.config.exchange.queue_model if self.config.exchange is not None else self.config.queue_model
        queue_pos_cfg = (
            self.config.exchange.queue_position_assumption
            if self.config.exchange is not None
            else self.config.queue_position_assumption
        )
        queue_section: dict[str, float | str] = {
            "queue_model": str(queue_diag.get("queue_model", queue_model_cfg)),
            "queue_position_assumption": float(queue_diag.get("queue_position_assumption", queue_pos_cfg)),
            "maker_fill_ratio": float(result.execution_report.maker_fill_ratio),
        }
        for key in ("queue_blocked_count", "queue_ready_count", "queue_wait_ticks", "queue_wait_ms", "blocked_miss_count", "ready_but_not_filled_count", "queue_wait_samples_count"):
            if key in queue_diag:
                queue_section[key] = float(queue_diag[key])

        latency_cfg = self.config.latency
        configured_submit_ms = latency_diag.get("configured_order_submit_ms")
        configured_ack_ms = latency_diag.get("configured_order_ack_ms")
        configured_cancel_ms = latency_diag.get("configured_cancel_ms")
        if configured_submit_ms is None:
            configured_submit_ms = latency_cfg.order_submit_ms if latency_cfg and latency_cfg.order_submit_ms is not None else 0.0
        if configured_ack_ms is None:
            configured_ack_ms = latency_cfg.order_ack_ms if latency_cfg and latency_cfg.order_ack_ms is not None else 0.0
        if configured_cancel_ms is None:
            configured_cancel_ms = latency_cfg.cancel_ms if latency_cfg and latency_cfg.cancel_ms is not None else 0.0

        latency_section: dict[str, float | int | bool] = {
            "configured_order_submit_ms": float(configured_submit_ms),
            "configured_order_ack_ms": float(configured_ack_ms),
            "configured_cancel_ms": float(configured_cancel_ms),
            "latency_alias_applied": bool(getattr(self.config, "_latency_alias_applied", False)),
            "order_ack_used_for_fill_gating": False,
        }
        for key in ("sampled_avg_submit_latency_ms", "sampled_avg_ack_latency_ms", "sampled_avg_cancel_latency_ms", "avg_cancel_effective_lag_ms", "sampled_avg_fill_latency_ms"):
            if key in latency_diag:
                latency_section[key] = float(latency_diag[key])
        for key in ("sampled_submit_latency_count", "sampled_ack_latency_count", "sampled_cancel_latency_count", "cancel_effective_samples_count", "cancel_pending_count", "sampled_fill_latency_count", "pending_before_arrival_count", "fills_before_cancel_effective_count"):
            if key in latency_diag:
                latency_section[key] = int(latency_diag[key])

        cancel_reasons = self._aggregate_cancel_reasons(all_parent_orders)

        decision_latency = {
            "configured_decision_compute_ms": float(self._decision_compute_ms),
            "decision_latency_enabled": bool(self._decision_compute_ms > 0.0),
            "avg_decision_state_age_ms": avg_decision_age_ms,
            "max_decision_state_age_ms": max_decision_age_ms,
            "decision_state_samples_count": decision_state_samples,
            "avg_decision_state_age_ms_note": "Aggregated over decision-evaluated steps only.",
        }

        tick_time = {
            "canonical_tick_interval_ms": float(self._canonical_tick_ms),
            "resample_interval": resample_interval,
            "state_history_max_len": int(self._max_history_len),
            "strategy_runtime_lookback_ticks": int(self._strategy_lookback_ticks),
            "history_safety_buffer_ticks": int(self._HISTORY_SAFETY_TICKS),
        }

        config_snapshot = {
            "resample": resample_interval,
            "market_data_delay_ms": float(self._market_data_delay_ms),
            "decision_compute_ms": float(self._decision_compute_ms),
            "latency_ms": float(self.config.latency_ms),
            "latency_order_submit_ms": float(latency_section["configured_order_submit_ms"]),
            "latency_order_ack_ms": float(latency_section["configured_order_ack_ms"]),
            "latency_cancel_ms": float(latency_section["configured_cancel_ms"]),
            "latency_alias_applied": bool(latency_section["latency_alias_applied"]),
            "queue_model": str(queue_section["queue_model"]),
            "exchange_model": str(self.config.exchange_model),
            "placement_style": str(self.config.placement_style),
            "slicing_algo": str(self.config.slicing_algo),
        }

        realism_diagnostics = {
            "observation_lag": dict(observation_lag),
            "decision_latency": decision_latency,
            "tick_time": tick_time,
            "lifecycle": dict(lifecycle),
            "queue": dict(queue_section),
            "latency": dict(latency_section),
            "cancel_reasons": cancel_reasons,
            "timings": dict(base_timings),
            "config_snapshot": config_snapshot,
        }

        result.metadata["decision_latency"] = decision_latency
        result.metadata["tick_time"] = tick_time
        result.metadata["lifecycle"] = dict(lifecycle)
        result.metadata["queue"] = dict(queue_section)
        result.metadata["latency"] = dict(latency_section)
        result.metadata["cancel_reasons"] = cancel_reasons
        result.metadata["realism_diagnostics"] = realism_diagnostics

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
        result.metadata["realism_diagnostics"]["timings"] = timings

        if self.output_dir is not None:
            self._report_builder.save_runtime_artifacts(result, self.output_dir)

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
        queue_model = ComponentFactory.normalize_queue_model(config.exchange.queue_model)

        self._fill_simulator = FillSimulator(
            matching_engine=matching_engine,
            order_book=order_book,
            latency_model=latency_model,
            fee_model=fee_model,
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
        """Append state to per-symbol history for observed_state lookup.

        Prunes history beyond ``_max_history_len`` to bound memory usage,
        especially important at finer resolutions (500ms) and in universe
        backtests with many symbols.
        """
        sym = state.symbol
        if sym not in self._state_history:
            self._state_history[sym] = []
            self._state_ts[sym] = []
        self._state_history[sym].append(state)
        self._state_ts[sym].append(state.timestamp)

        # Bounded retention: drop oldest entries beyond the retention window.
        max_len = getattr(self, "_max_history_len", 0)
        if max_len > 0 and len(self._state_history[sym]) > max_len:
            excess = len(self._state_history[sym]) - max_len
            del self._state_history[sym][:excess]
            del self._state_ts[sym][:excess]

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

    def _create_parent_order(
        self,
        signal,
        delta: int,
        state: "MarketState",
        true_state: "MarketState | None" = None,
    ):
        if delta == 0:
            return None

        decision_true_state = true_state if true_state is not None else state
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
        self._annotate_decision_metadata(
            parent.meta,
            true_state=decision_true_state,
            observed_state=state,
            phase="parent_create",
        )

        hints: dict[str, object] = {}
        if signal is not None and getattr(signal, "tags", None):
            hints = self._normalize_execution_hints(signal.tags)
        if hints:
            parent.meta["execution_hints"] = hints

        parent = self._order_constraints.apply_all(parent, state)
        if parent.status.name == "REJECTED" or parent.total_qty <= 0:
            return None
        return parent

    def _slice_order(
        self,
        parent: "ParentOrder",
        state: "MarketState",
        true_state: "MarketState | None" = None,
    ) -> list:
        decision_true_state = true_state if true_state is not None else state
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
        self._annotate_decision_metadata(
            child.meta,
            true_state=decision_true_state,
            observed_state=state,
            phase="child_slice",
        )
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
        child.meta["canonical_tick_interval_ms"] = float(self._canonical_tick_ms)
        if self._fill_simulator is not None and hasattr(self._fill_simulator, "register_submit_request"):
            submit_request_time = decision_true_state.timestamp
            self._fill_simulator.register_submit_request(child, submit_request_time)

        # TimingLogic current_time is based on observed-state timestamps, so
        # last_sent must share the same clock to keep elapsed calculations sane.
        self._last_child_submission[parent.symbol] = state.timestamp
        return [child]

    def _process_micro_events(self, prev_state, state):
        if prev_state is None or prev_state.symbol != state.symbol:
            return []
        return self._micro_event_handler.process(prev_state, state)

    def _is_state_actionable(self, state, events):
        return self._micro_event_handler.is_tradable(state, events)

    def _process_open_orders(self, parent, true_state, observed_state, events):
        """Process open children with delayed-cancel lifecycle gating.

        - Cancel/replace decisions use observed_state timestamps
        - Submit/cancel lifecycle gating and fills use true_state timestamps
        """
        from execution_planning.layer3_order.order_types import OrderStatus

        symbol = parent.symbol
        open_children = self._open_child_orders.get(symbol, [])
        if not open_children:
            return []

        live_children = []
        for child in open_children:
            if (
                self._fill_simulator is not None
                and hasattr(self._fill_simulator, "finalize_cancel_if_due")
                and self._fill_simulator.finalize_cancel_if_due(child, true_state.timestamp)
            ):
                continue
            live_children.append(child)

        if not live_children:
            self._sync_open_children(symbol, parent)
            return []

        if not self._is_state_actionable(true_state, events):
            for child in self._micro_event_handler.cancel_orders_on_halt(live_children, events):
                self._request_cancel_child(child, true_state, reason="micro_event_block")
            self._sync_open_children(symbol, parent)
            return []

        hints = parent.meta.get("execution_hints", {}) if parent.meta else {}
        cancel_after_ticks = hints.get("cancel_after_ticks") if isinstance(hints, dict) else None
        max_reprices = hints.get("max_reprices") if isinstance(hints, dict) else None
        placement_mode = hints.get("placement_mode") if isinstance(hints, dict) else None

        decision_candidates = [
            child
            for child in live_children
            if (
                not bool((child.meta or {}).get("cancel_pending", False))
                and child.status != OrderStatus.PENDING
            )
        ]

        # Cancel/replace decisions use observed (delayed) market data
        actions = self._cancel_replace.process_open_orders(
            open_orders=decision_candidates,
            state=observed_state,
            current_time=observed_state.timestamp,
            cancel_after_ticks=(int(cancel_after_ticks) if isinstance(cancel_after_ticks, (int, float)) else None),
            max_reprices=(int(max_reprices) if isinstance(max_reprices, (int, float)) else None),
            placement_mode=(str(placement_mode) if isinstance(placement_mode, str) else None),
        )

        executable_children = [
            child
            for child in live_children
            if (
                bool((child.meta or {}).get("cancel_pending", False))
                or child.status == OrderStatus.PENDING
            )
        ]

        for action in actions:
            child = action["order"]
            decision = action["action"]
            self._annotate_decision_metadata(
                child.meta,
                true_state=true_state,
                observed_state=observed_state,
                phase=f"cancel_replace:{decision}",
            )
            if decision == "cancel":
                self._request_cancel_child(child, true_state, reason=action["reason"])
                if child.is_active:
                    executable_children.append(child)
                continue
            if decision == "replace":
                replacement = self._replace_child_order(
                    parent=parent, child=child, true_state=true_state, observed_state=observed_state,
                    new_price=action["new_price"], reason=action["reason"],
                )
                if replacement is not None:
                    executable_children.append(replacement)
                continue
            executable_children.append(child)

        if self._fill_simulator is None:
            self._sync_open_children(symbol, parent)
            return []

        # Fill execution uses true (current) market state
        fills = self._fill_simulator.simulate_fills(parent, executable_children, true_state)
        self._sync_open_children(symbol, parent)
        return fills

    def _request_cancel_child(self, child, state, reason):
        if not child.is_active:
            return
        if not isinstance(child.meta, dict):
            child.meta = {}

        if self._fill_simulator is not None and hasattr(self._fill_simulator, "register_cancel_request"):
            self._fill_simulator.register_cancel_request(child, state.timestamp, reason)
            return

        # Fallback for unit tests that bypass component setup.
        self._cancel_child(child, state, reason=reason)

    def _cancel_child(self, child, state, reason):
        from execution_planning.layer3_order.order_types import OrderStatus

        child.status = OrderStatus.CANCELLED
        child.cancel_time = state.timestamp
        if not isinstance(child.meta, dict):
            child.meta = {}
        child.meta["cancel_pending"] = False
        child.meta["cancel_reason"] = reason

    def _replace_child_order(self, parent, child, true_state, observed_state, new_price, reason):
        from execution_planning.layer3_order.order_types import ChildOrder
        remaining_qty = child.remaining_qty
        # Intentional minimal exception: replace currently uses immediate
        # cancel of the old child, then starts a new child lifecycle.
        self._cancel_child(child, true_state, reason=f"replace:{reason}")
        if remaining_qty <= 0 or new_price is None:
            return None

        replacement_child = ChildOrder.create(
            parent=parent, order_type=child.order_type,
            qty=remaining_qty, price=new_price, tif=child.tif,
            submitted_time=observed_state.timestamp, arrival_mid=child.arrival_mid,
        )
        replacement_child.meta["replaces"] = child.child_id
        replacement_child.meta["replace_reason"] = reason
        self._annotate_decision_metadata(
            replacement_child.meta,
            true_state=true_state,
            observed_state=observed_state,
            phase="replace_create",
        )
        prev_reprices = int(child.meta.get("reprice_count", 0))
        replacement_child.meta["reprice_count"] = prev_reprices + 1
        if parent.meta and isinstance(parent.meta.get("execution_hints"), dict):
            replacement_child.meta["execution_hints"] = dict(parent.meta["execution_hints"])
        parent.child_orders.append(replacement_child)
        replacement_child.meta["canonical_tick_interval_ms"] = float(self._canonical_tick_ms)
        if self._fill_simulator is not None and hasattr(self._fill_simulator, "register_submit_request"):
            self._fill_simulator.register_submit_request(replacement_child, true_state.timestamp)

        # Cancel/replace timing also runs on observed-state timestamps.
        self._last_child_submission[parent.symbol] = observed_state.timestamp
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
