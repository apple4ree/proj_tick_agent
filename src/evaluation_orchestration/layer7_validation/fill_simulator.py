"""
fill_simulator.py
-----------------
Fill simulation logic extracted from PipelineRunner.

Handles matching child orders against the LOB, applying impact/fee models,
and recording fills into the bookkeeper and PnL ledger.

**Queue-position semantics are owned exclusively by this module.**
For passive resting orders, FillSimulator:
  1. Determines passive-queue candidacy  (_is_passive_queue_candidate)
  2. Initializes queue state (_initialize_queue_state)
  3. Advances queue_ahead_qty each tick  (_advance_queue_and_ready)
  4. Gates the order: only forwards it to MatchingEngine once the
     queue has been consumed (queue_ahead_qty <= 0).
  5. (pro_rata only) Caps fill qty via pro-rata allocation after
     MatchingEngine returns its result.

MatchingEngine (layer5) performs pure price/qty/exchange-model matching
and does NOT apply any queue filtering.  This separation eliminates the
risk of double-counting queue position.

Supported queue models
----------------------
- ``none``         — queue gate disabled; immediate fill eligibility
- ``price_time``   — strict FIFO conservative approximation; trade-only
                     queue advancement, depth drop ignored
- ``risk_adverse`` — same as price_time (trade-only advancement)
- ``prob_queue``   — trade-driven + partial depth-drop credit weighted
                     by (1 − queue_position_assumption)
- ``random``       — trade-driven + stochastic depth-drop credit
                     (uniform fraction per tick, deterministic under seed)
- ``pro_rata``     — queue gate (risk_adverse-style) + post-gate fill qty
                     capped by approximate size-proportional allocation

Model taxonomy:
  Gate-only models  — price_time, risk_adverse, prob_queue, random
  Gate + allocation — pro_rata
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from evaluation_orchestration.layer7_validation.queue_models import build_queue_model, QueueModel

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer3_order.order_types import ParentOrder
    from market_simulation.layer5_simulator.bookkeeper import FillEvent

logger = logging.getLogger(__name__)


class FillSimulator:
    """Simulates fill execution for child orders against the LOB.

    Single owner of queue-position semantics for passive fills.
    Aggressive / marketable / IOC / FOK orders bypass the queue gate
    and are forwarded directly to MatchingEngine.

    Queue models fall into two categories:

    * **Gate-only** (``price_time``, ``risk_adverse``, ``prob_queue``,
      ``random``): the queue gate blocks fills until ``queue_ahead_qty``
      is consumed; once through, MatchingEngine fills are accepted as-is.

    * **Gate + allocation** (``pro_rata``): after the gate passes, fill
      qty is further capped by the order's proportional share of the
      resting volume.
    """

    def __init__(
        self,
        matching_engine,
        order_book,
        latency_model,
        fee_model,
        impact_model,
        bookkeeper,
        pnl_ledger,
        queue_model: str = "prob_queue",
        queue_position_assumption: float = 0.5,
        rng_seed: int | None = None,
    ) -> None:
        self._matching_engine = matching_engine
        self._order_book = order_book
        self._latency_model = latency_model
        self._fee_model = fee_model
        self._impact_model = impact_model
        self._bookkeeper = bookkeeper
        self._pnl_ledger = pnl_ledger
        self._queue_model = (queue_model or "prob_queue").strip().lower()
        self._queue_position_assumption = float(np.clip(queue_position_assumption, 0.0, 1.0))
        self._rng = np.random.default_rng(rng_seed)
        # Lightweight queue diagnostics (always-on aggregate only).
        self._queue_blocked_count: int = 0
        self._queue_ready_count: int = 0
        self._queue_blocked_miss_count: int = 0
        self._queue_ready_but_not_filled_count: int = 0
        self._queue_wait_ticks_sum: float = 0.0
        self._queue_wait_ms_sum: float = 0.0
        self._queue_wait_samples: int = 0

        self._submit_latency_sum_ms: float = 0.0
        self._submit_latency_count: int = 0
        self._ack_latency_sum_ms: float = 0.0
        self._ack_latency_count: int = 0
        self._cancel_latency_sum_ms: float = 0.0
        self._cancel_latency_count: int = 0
        self._cancel_pending_count: int = 0
        self._cancel_effective_lag_sum_ms: float = 0.0
        self._cancel_effective_lag_count: int = 0
        self._fill_latency_sum_ms: float = 0.0
        self._fill_latency_count: int = 0
        self._pending_before_arrival_count: int = 0
        self._fills_before_cancel_effective_count: int = 0

        # Build explicit queue model interface
        self._queue_model_impl: QueueModel = build_queue_model(
            name=self._queue_model,
            queue_position_assumption=self._queue_position_assumption,
            rng_seed=rng_seed,
        )
    @staticmethod
    def _as_timestamp(value) -> pd.Timestamp | None:
        if isinstance(value, pd.Timestamp):
            return value
        return None

    def _clear_cancel_pending(self, child) -> None:
        if not isinstance(child.meta, dict):
            return
        if bool(child.meta.get("cancel_pending", False)):
            child.meta["cancel_pending"] = False
            self._cancel_pending_count = max(0, self._cancel_pending_count - 1)

    def _record_queue_wait(self, child, state: "MarketState") -> None:
        if not isinstance(child.meta, dict):
            return
        enter_ts = self._as_timestamp(child.meta.get("queue_enter_ts"))
        if enter_ts is None:
            enter_ts = self._as_timestamp(getattr(child, "queue_enter_ts", None))
        if enter_ts is None:
            return

        wait_ms = max(0.0, (state.timestamp - enter_ts).total_seconds() * 1000.0)
        tick_ms = float(child.meta.get("canonical_tick_interval_ms", 0.0))
        wait_ticks = (wait_ms / tick_ms) if tick_ms > 0.0 else 0.0

        self._queue_wait_ms_sum += float(wait_ms)
        self._queue_wait_ticks_sum += float(wait_ticks)
        self._queue_wait_samples += 1

    def register_submit_request(self, child, request_time: pd.Timestamp) -> None:
        if not isinstance(child.meta, dict):
            child.meta = {}
        if self._as_timestamp(child.meta.get("venue_arrival_time")) is not None:
            return

        submit_ms, ack_ms = self._latency_model.sample_submit_and_ack_latency()
        venue_arrival_time = request_time + pd.Timedelta(milliseconds=submit_ms)
        ack_time = venue_arrival_time + pd.Timedelta(milliseconds=ack_ms)

        child.meta["submit_request_time"] = request_time
        child.meta["submit_latency_ms"] = float(submit_ms)
        child.meta["venue_arrival_time"] = venue_arrival_time
        child.meta["ack_latency_ms"] = float(ack_ms)
        child.meta["ack_time"] = ack_time

        self._submit_latency_sum_ms += float(submit_ms)
        self._submit_latency_count += 1
        self._ack_latency_sum_ms += float(ack_ms)
        self._ack_latency_count += 1

    def register_cancel_request(self, child, request_time: pd.Timestamp, reason: str) -> None:
        if not isinstance(child.meta, dict):
            child.meta = {}
        if bool(child.meta.get("cancel_pending", False)):
            return

        cancel_ms = self._latency_model.sample_cancel_latency()
        cancel_effective_time = request_time + pd.Timedelta(milliseconds=cancel_ms)

        child.meta["cancel_pending"] = True
        self._cancel_pending_count += 1
        child.meta["cancel_request_reason"] = reason
        child.meta["cancel_requested_time"] = request_time
        child.meta["cancel_latency_ms"] = float(cancel_ms)
        child.meta["cancel_effective_time"] = cancel_effective_time

        self._cancel_latency_sum_ms += float(cancel_ms)
        self._cancel_latency_count += 1

    def finalize_cancel_if_due(self, child, current_time: pd.Timestamp) -> bool:
        from execution_planning.layer3_order.order_types import OrderStatus

        if not isinstance(child.meta, dict):
            return False
        if not bool(child.meta.get("cancel_pending", False)):
            return False

        effective_time = self._as_timestamp(child.meta.get("cancel_effective_time"))
        if effective_time is None or current_time < effective_time:
            return False

        requested_time = self._as_timestamp(child.meta.get("cancel_requested_time"))
        if requested_time is not None:
            lag_ms = max(0.0, (current_time - requested_time).total_seconds() * 1000.0)
            self._cancel_effective_lag_sum_ms += float(lag_ms)
            self._cancel_effective_lag_count += 1

        self._clear_cancel_pending(child)
        if child.remaining_qty <= 0:
            return False

        child.status = OrderStatus.CANCELLED
        child.cancel_time = current_time
        child.meta["cancel_reason"] = str(child.meta.get("cancel_request_reason", "cancel_requested"))
        return True

    def latency_diagnostics(self) -> dict[str, float | int]:
        submit_avg = (self._submit_latency_sum_ms / self._submit_latency_count) if self._submit_latency_count > 0 else 0.0
        ack_avg = (self._ack_latency_sum_ms / self._ack_latency_count) if self._ack_latency_count > 0 else 0.0
        cancel_avg = (self._cancel_latency_sum_ms / self._cancel_latency_count) if self._cancel_latency_count > 0 else 0.0
        fill_avg = (self._fill_latency_sum_ms / self._fill_latency_count) if self._fill_latency_count > 0 else 0.0
        cancel_effective_avg = (self._cancel_effective_lag_sum_ms / self._cancel_effective_lag_count) if self._cancel_effective_lag_count > 0 else 0.0

        return {
            "configured_order_submit_ms": float(self._latency_model.profile.order_submit_ms),
            "configured_order_ack_ms": float(self._latency_model.profile.order_ack_ms),
            "configured_cancel_ms": float(self._latency_model.profile.cancel_ms),
            "sampled_avg_submit_latency_ms": float(submit_avg),
            "sampled_avg_ack_latency_ms": float(ack_avg),
            "sampled_avg_cancel_latency_ms": float(cancel_avg),
            "avg_cancel_effective_lag_ms": float(cancel_effective_avg),
            "sampled_avg_fill_latency_ms": float(fill_avg),
            "sampled_submit_latency_count": int(self._submit_latency_count),
            "sampled_ack_latency_count": int(self._ack_latency_count),
            "sampled_cancel_latency_count": int(self._cancel_latency_count),
            "cancel_effective_samples_count": int(self._cancel_effective_lag_count),
            "cancel_pending_count": int(self._cancel_pending_count),
            "sampled_fill_latency_count": int(self._fill_latency_count),
            "pending_before_arrival_count": int(self._pending_before_arrival_count),
            "fills_before_cancel_effective_count": int(self._fills_before_cancel_effective_count),
        }

    def simulate_fills(
        self,
        parent: "ParentOrder",
        child_orders: list,
        state: "MarketState",
    ) -> list["FillEvent"]:
        """Simulate fill execution for child orders against the current LOB."""
        from execution_planning.layer3_order.order_types import OrderStatus
        from market_simulation.layer5_simulator.bookkeeper import FillEvent

        fills: list[FillEvent] = []
        mid = state.lob.mid_price
        if mid is None:
            return fills

        self._order_book.update(state.lob)
        adv_proxy = max(1.0, float(state.lob.total_bid_depth + state.lob.total_ask_depth))

        for child in child_orders:
            # Parent-level overfill guard: stop filling once parent is complete
            if parent.remaining_qty <= 0:
                self._clear_cancel_pending(child)
                child.status = OrderStatus.CANCELLED
                continue

            if self._as_timestamp(child.meta.get("venue_arrival_time")) is None:
                self.register_submit_request(child, state.timestamp)

            if self.finalize_cancel_if_due(child, state.timestamp):
                continue

            venue_arrival_time = self._as_timestamp(child.meta.get("venue_arrival_time"))
            if venue_arrival_time is not None and state.timestamp < venue_arrival_time:
                tick_ms = float(child.meta.get("canonical_tick_interval_ms", 0.0)) if isinstance(child.meta, dict) else 0.0
                ms_until_arrival = (venue_arrival_time - state.timestamp).total_seconds() * 1000.0
                if tick_ms <= 0.0 or ms_until_arrival >= tick_ms:
                    self._pending_before_arrival_count += 1
                    child.status = OrderStatus.PENDING
                    continue

            if child.status == OrderStatus.PENDING:
                child.status = OrderStatus.OPEN

            remaining_qty = child.remaining_qty
            if remaining_qty <= 0:
                self._clear_cancel_pending(child)
                child.status = OrderStatus.FILLED
                continue

            queue_ready_this_tick = False

            # Passive queue-position gate: non-marketable passive orders must first
            # burn through ahead queue before matching can occur.
            if self._is_queue_gate_enabled() and self._is_passive_queue_candidate(child, state):
                self._initialize_queue_state(child, state)
                if not self._advance_queue_and_ready(child, state):
                    self._queue_blocked_count += 1
                    self._queue_blocked_miss_count += 1
                    child.status = OrderStatus.OPEN
                    continue

                queue_ready_this_tick = True
                if not bool(child.meta.get("queue_ready_recorded", False)):
                    self._queue_ready_count += 1
                    self._record_queue_wait(child, state)
                    child.meta["queue_ready_recorded"] = True

            # Cap child fill to parent remaining to prevent overfill
            remaining_qty = min(remaining_qty, parent.remaining_qty)

            submit_latency_ms = float(child.meta.get("submit_latency_ms", 0.0))
            ack_latency_ms = float(child.meta.get("ack_latency_ms", 0.0))
            latency_ms = submit_latency_ms + ack_latency_ms
            filled_qty, matched_price = self._matching_engine.match(
                child=replace(child, qty=remaining_qty, filled_qty=0),
                book=self._order_book,
                state=state,
                latency_ms=latency_ms,
            )

            if filled_qty <= 0:
                if queue_ready_this_tick:
                    self._queue_ready_but_not_filled_count += 1
                child.status = OrderStatus.CANCELLED if child.tif.name == "IOC" else OrderStatus.OPEN
                if child.status == OrderStatus.CANCELLED:
                    self._clear_cancel_pending(child)
                continue

            # Post-gate allocation cap (e.g. pro_rata)
            if self._queue_model_impl.has_allocation and child.queue_initialized:
                filled_qty = self._queue_model_impl.cap_fill(
                    child, state, filled_qty,
                    level_qty_fn=self._level_qty_for_price,
                    same_level_trade_qty_fn=self._same_level_trade_qty,
                )
                if filled_qty <= 0:
                    child.status = OrderStatus.OPEN
                    continue

            # Final guard: clamp to parent remaining
            filled_qty = min(filled_qty, parent.remaining_qty)

            impacted_price = self._impact_model.adjust_price(
                base_price=matched_price,
                qty=filled_qty,
                adv=adv_proxy,
                mid=mid,
                side=child.side,
            )
            impact_bps = abs((impacted_price - matched_price) / mid) * 10_000.0 if mid else 0.0
            slippage_bps = self._compute_slippage_bps(child.arrival_mid or mid, impacted_price, child.side)
            fee = self._fee_model.compute(
                qty=filled_qty,
                price=impacted_price,
                side=child.side,
                is_maker=self._is_maker_fill(child, state),
            )

            fill = FillEvent(
                timestamp=state.timestamp,
                order_id=child.child_id,
                parent_id=child.parent_id,
                symbol=child.symbol,
                side=child.side,
                filled_qty=filled_qty,
                fill_price=impacted_price,
                fee=fee,
                is_maker=self._is_maker_fill(child, state),
                slippage_bps=slippage_bps,
                market_impact_bps=impact_bps,
                latency_ms=latency_ms,
            )
            fills.append(fill)
            self._fill_latency_sum_ms += float(latency_ms)
            self._fill_latency_count += 1
            cancel_effective_time = self._as_timestamp(child.meta.get("cancel_effective_time"))
            if bool(child.meta.get("cancel_pending", False)) and cancel_effective_time is not None and state.timestamp < cancel_effective_time:
                self._fills_before_cancel_effective_count += 1

            existing_child_qty = child.filled_qty
            child.filled_qty += filled_qty
            child.avg_fill_price = self._weighted_avg_price(
                child.avg_fill_price, existing_child_qty, impacted_price, filled_qty,
            )
            child.fill_time = state.timestamp
            if child.is_complete:
                self._clear_cancel_pending(child)
                child.status = OrderStatus.FILLED
            else:
                child.status = OrderStatus.PARTIAL

            existing_parent_qty = parent.filled_qty
            parent.filled_qty += filled_qty
            parent.avg_fill_price = self._weighted_avg_price(
                parent.avg_fill_price, existing_parent_qty, impacted_price, filled_qty,
            )
            parent.status = OrderStatus.FILLED if parent.is_complete else OrderStatus.PARTIAL

        return fills

    def record_fills(
        self,
        fills: list["FillEvent"],
        mid: float | None,
        all_fills: list["FillEvent"],
    ) -> None:
        """Record fills into bookkeeper and PnL ledger."""
        if mid is None:
            return
        for fill in fills:
            cost_basis = self._bookkeeper.get_average_cost(fill.symbol)
            self._bookkeeper.record_fill(fill)
            self._pnl_ledger.record_fill(fill, cost_basis=cost_basis, mark_price=mid)
            all_fills.append(fill)

    def queue_diagnostics(self) -> dict[str, float | str]:
        """Return lightweight queue diagnostics for reporting."""
        wait_ticks = (self._queue_wait_ticks_sum / self._queue_wait_samples) if self._queue_wait_samples > 0 else 0.0
        wait_ms = (self._queue_wait_ms_sum / self._queue_wait_samples) if self._queue_wait_samples > 0 else 0.0

        return {
            "queue_model": self._queue_model,
            "queue_position_assumption": self._queue_position_assumption,
            "queue_blocked_count": float(self._queue_blocked_count),
            "queue_ready_count": float(self._queue_ready_count),
            "queue_wait_ticks": float(wait_ticks),
            "queue_wait_ms": float(wait_ms),
            "blocked_miss_count": float(self._queue_blocked_miss_count),
            "ready_but_not_filled_count": float(self._queue_ready_but_not_filled_count),
            "queue_wait_samples_count": float(self._queue_wait_samples),
        }

    def _is_queue_gate_enabled(self) -> bool:
        return self._queue_model != "none"

    def _is_passive_queue_candidate(self, child, state: "MarketState") -> bool:
        from execution_planning.layer3_order.order_types import OrderTIF, OrderType

        if child.order_type != OrderType.LIMIT or child.price is None:
            return False
        if child.tif in {OrderTIF.IOC, OrderTIF.FOK}:
            return False
        if self._is_marketable(child, state):
            return False

        hints = child.meta.get("execution_hints", {}) if isinstance(child.meta, dict) else {}
        placement_mode = hints.get("placement_mode") if isinstance(hints, dict) else None
        if isinstance(placement_mode, str) and placement_mode.strip().lower() in {"passive_join", "passive_only"}:
            return True

        policy_name = child.meta.get("placement_policy") if isinstance(child.meta, dict) else None
        if policy_name == "PassivePlacement":
            return True
        if policy_name == "SpreadAdaptivePlacement":
            # Adaptive policy may emit passive resting orders; treat non-marketable
            # DAY-style limits as passive-like for queue realism.
            return True

        return False

    def _initialize_queue_state(self, child, state: "MarketState") -> None:
        if child.queue_initialized:
            return

        queue_price = child.price
        level_qty = self._level_qty_for_price(child, state)
        ahead = max(0.0, float(level_qty))

        child.queue_ahead_qty = ahead
        child.queue_enter_ts = state.timestamp
        child.queue_price = queue_price
        child.queue_side = child.side.value
        child.queue_initialized = True
        child.queue_model = self._queue_model
        child.initial_level_qty = float(level_qty)
        child.queue_last_level_qty = float(level_qty)

        child.meta["queue_initialized"] = True
        child.meta["queue_ahead_qty"] = child.queue_ahead_qty
        child.meta["queue_model"] = self._queue_model
        child.meta.setdefault("queue_ready_recorded", False)

    def _advance_queue_and_ready(self, child, state: "MarketState") -> bool:
        """Advance queue_ahead_qty and return True if the order is ready to fill.

        Orchestrates three sub-steps via QueueModel interface:
          1. Trade-driven advancement (common to all models)
          2. Depth-driven advancement (model-specific)
          3. Gate check: queue_ahead_qty <= 0 and price still at our level
        """
        same_level_trade_qty = float(self._same_level_trade_qty(child, state))
        prev_level_qty = max(0.0, float(child.queue_last_level_qty))
        curr_level_qty = max(0.0, float(self._level_qty_for_price(child, state)))

        # 1) trade-driven advancement — common to all models
        queue_ahead = QueueModel.advance_trade(child, same_level_trade_qty)

        # 2) depth-driven advancement — delegated to queue model
        depth_drop = max(0.0, prev_level_qty - curr_level_qty)
        unexplained_depth_drop = max(0.0, depth_drop - same_level_trade_qty)
        depth_advancement = self._queue_model_impl.advance_depth(unexplained_depth_drop)

        queue_ahead = max(0.0, queue_ahead - depth_advancement)

        # Persist queue state
        child.queue_ahead_qty = queue_ahead
        child.queue_last_level_qty = curr_level_qty
        child.meta["queue_ahead_qty"] = queue_ahead
        child.meta["queue_last_level_qty"] = curr_level_qty

        # 3) Gate check — delegated to queue model
        return QueueModel.ready_to_match(child, state)

    def _level_qty_for_price(self, child, state: "MarketState") -> float:
        levels = state.lob.bid_levels if child.side.value == "BUY" else state.lob.ask_levels
        if not levels:
            return 0.0

        assert child.price is not None

        for level in levels:
            if level.price == child.price:
                return float(level.volume)

        best = levels[0]
        if best.price == child.price:
            return float(best.volume)

        # Conservative nearest-level fallback when exact level is absent.
        nearest = min(levels, key=lambda lvl: abs(lvl.price - child.price))
        return float(nearest.volume)

    def _same_level_trade_qty(self, child, state: "MarketState") -> int:
        if child.price is None:
            return 0

        if state.trades is not None and not state.trades.empty and "price" in state.trades.columns:
            prices = state.trades["price"].astype(float)
            if "volume" in state.trades.columns:
                volumes = state.trades["volume"].astype(float)
            else:
                volumes = np.ones(len(state.trades), dtype=float)
            return int(volumes[prices == float(child.price)].sum())

        # LOB fallback: use last trade only when it happened at our queue price.
        if state.lob.last_trade_price is None or state.lob.last_trade_price != child.price:
            return 0
        return int(state.lob.last_trade_volume or 0)

    @staticmethod
    def _compute_slippage_bps(arrival_mid: float, fill_price: float, side) -> float:
        from execution_planning.layer3_order.order_types import OrderSide
        if arrival_mid <= 0.0:
            return 0.0
        raw_bps = ((fill_price - arrival_mid) / arrival_mid) * 10_000.0
        return raw_bps if side == OrderSide.BUY else -raw_bps

    @staticmethod
    def _is_marketable(child, state: "MarketState") -> bool:
        from execution_planning.layer3_order.order_types import OrderSide

        if child.price is None:
            return True
        if child.side == OrderSide.BUY:
            best_ask = state.lob.best_ask
            return best_ask is not None and child.price >= best_ask
        best_bid = state.lob.best_bid
        return best_bid is not None and child.price <= best_bid

    @staticmethod
    def _is_maker_fill(child, state: "MarketState") -> bool:
        from execution_planning.layer3_order.order_types import OrderSide, OrderType
        if child.order_type != OrderType.LIMIT or child.price is None:
            return False
        if child.side == OrderSide.BUY:
            best_bid = state.lob.best_bid
            return best_bid is not None and child.price <= best_bid
        best_ask = state.lob.best_ask
        return best_ask is not None and child.price >= best_ask

    @staticmethod
    def _weighted_avg_price(
        existing_price: float | None,
        existing_qty: int,
        new_price: float,
        new_qty: int,
    ) -> float:
        if existing_qty <= 0 or existing_price is None:
            return new_price
        total_qty = existing_qty + new_qty
        if total_qty <= 0:
            return new_price
        return ((existing_price * existing_qty) + (new_price * new_qty)) / total_qty
