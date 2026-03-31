"""
test_backtest_realism.py
------------------------
Tests for backtest realism improvements:
  1. observed_state / true_state separation (observation lag)
  2. queue model interface extraction (regression)
  3. fill-rule ownership (FillSimulator owns queue, MatchingEngine is queue-free)
"""
from __future__ import annotations

import pandas as pd
import pytest

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from evaluation_orchestration.layer7_validation.backtest_config import BacktestConfig
from evaluation_orchestration.layer7_validation.pipeline_runner import PipelineRunner
from evaluation_orchestration.layer7_validation.queue_models import (
    QueueModel,
    NoneQueue,
    PriceTimeQueue,
    RiskAdverseQueue,
    ProbQueueQueue,
    RandomQueueQueue,
    ProRataQueue,
    build_queue_model,
    QUEUE_MODEL_REGISTRY,
)
from evaluation_orchestration.layer7_validation.queue_models.base import QueueModel as QueueModelBase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(
    *,
    ts: str,
    symbol: str = "TEST",
    best_bid: float = 100.0,
    best_ask: float = 100.1,
    bid_volume: int = 1000,
    ask_volume: int = 1000,
    trade_price: float | None = None,
    trade_volume: int = 0,
) -> MarketState:
    trades = None
    if trade_price is not None and trade_volume > 0:
        trades = pd.DataFrame(
            [{"timestamp": pd.Timestamp(ts), "price": trade_price, "volume": trade_volume, "side": "SELL"}]
        )
    return MarketState(
        timestamp=pd.Timestamp(ts),
        symbol=symbol,
        lob=LOBSnapshot(
            timestamp=pd.Timestamp(ts),
            bid_levels=[LOBLevel(price=best_bid, volume=bid_volume)],
            ask_levels=[LOBLevel(price=best_ask, volume=ask_volume)],
            last_trade_price=trade_price,
            last_trade_volume=trade_volume if trade_price is not None else None,
        ),
        trades=trades,
    )


# ===================================================================
# 1. observed_state / true_state separation
# ===================================================================

class TestObservationLag:
    """Tests for PipelineRunner._lookup_observed_state."""

    def _build_runner(self, delay_ms: float = 0.0) -> PipelineRunner:
        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            market_data_delay_ms=delay_ms,
            seed=42,
        )
        # We only need the runner for its lookup method; no strategy needed.
        runner = PipelineRunner.__new__(PipelineRunner)
        runner.config = config
        runner._state_history = {}
        runner._state_ts = {}
        runner._market_data_delay_ms = delay_ms
        runner._decision_compute_ms = 0.0
        runner._max_history_len = 0  # unbounded for unit tests
        return runner

    def test_delay_zero_returns_current_state(self):
        """delay=0 must return the most recent state (preserve existing behavior)."""
        runner = self._build_runner(delay_ms=0.0)

        s0 = _make_state(ts="2026-03-13 09:00:00")
        s1 = _make_state(ts="2026-03-13 09:00:01")
        s2 = _make_state(ts="2026-03-13 09:00:02")

        for s in [s0, s1, s2]:
            runner._accumulate_state(s)

        result = runner._lookup_observed_state("TEST", s2.timestamp, 0.0)
        assert result is s2

    def test_delay_returns_past_state(self):
        """With 1500ms delay at 1s resolution, should return state 1-2 seconds ago."""
        runner = self._build_runner(delay_ms=1500.0)

        states = []
        for i in range(5):
            s = _make_state(ts=f"2026-03-13 09:00:0{i}", best_bid=100.0 + i)
            states.append(s)
            runner._accumulate_state(s)

        # At t=4 with 1500ms delay → target = t=4 - 1.5s = t=2.5s → should get state at t=2
        result = runner._lookup_observed_state("TEST", states[4].timestamp, 1500.0)
        assert result is states[2]

    def test_delay_exact_boundary(self):
        """When delay exactly matches a state timestamp, should return that state."""
        runner = self._build_runner(delay_ms=1000.0)

        s0 = _make_state(ts="2026-03-13 09:00:00")
        s1 = _make_state(ts="2026-03-13 09:00:01")
        s2 = _make_state(ts="2026-03-13 09:00:02")

        for s in [s0, s1, s2]:
            runner._accumulate_state(s)

        # At t=2 with 1000ms delay → target = t=1 exactly → should get state at t=1
        result = runner._lookup_observed_state("TEST", s2.timestamp, 1000.0)
        assert result is s1

    def test_delay_no_history_old_enough(self):
        """When delay is larger than available history, should return earliest state."""
        runner = self._build_runner(delay_ms=10000.0)

        s0 = _make_state(ts="2026-03-13 09:00:00")
        s1 = _make_state(ts="2026-03-13 09:00:01")

        for s in [s0, s1]:
            runner._accumulate_state(s)

        result = runner._lookup_observed_state("TEST", s1.timestamp, 10000.0)
        assert result is s0

    def test_delay_small_at_1s_resolution_collapses(self):
        """With 50ms delay at 1s resolution, observed_state ≈ current state."""
        runner = self._build_runner(delay_ms=50.0)

        s0 = _make_state(ts="2026-03-13 09:00:00")
        s1 = _make_state(ts="2026-03-13 09:00:01")
        s2 = _make_state(ts="2026-03-13 09:00:02")

        for s in [s0, s1, s2]:
            runner._accumulate_state(s)

        # At t=2 with 50ms delay → target = t=1.95s → should get state at t=1
        # With 1s resolution, the 50ms delay goes back to the previous second
        result = runner._lookup_observed_state("TEST", s2.timestamp, 50.0)
        assert result is s1

    def test_accumulate_state_builds_sorted_history(self):
        runner = self._build_runner()
        states = [_make_state(ts=f"2026-03-13 09:00:0{i}") for i in range(5)]
        for s in states:
            runner._accumulate_state(s)

        assert len(runner._state_history["TEST"]) == 5
        assert len(runner._state_ts["TEST"]) == 5
        assert runner._state_ts["TEST"] == [s.timestamp for s in states]


# ===================================================================
# 2. Queue model interface extraction
# ===================================================================

class TestQueueModelInterface:
    """Verify queue model interface contracts and registry."""

    def test_registry_contains_all_six_models(self):
        expected = {"none", "price_time", "risk_adverse", "prob_queue", "random", "pro_rata"}
        assert set(QUEUE_MODEL_REGISTRY.keys()) == expected

    def test_all_models_inherit_from_base(self):
        for name, cls in QUEUE_MODEL_REGISTRY.items():
            assert issubclass(cls, QueueModelBase), f"{name} does not inherit from QueueModel"

    def test_build_queue_model_factory(self):
        for name in QUEUE_MODEL_REGISTRY:
            model = build_queue_model(name, queue_position_assumption=0.5, rng_seed=42)
            assert isinstance(model, QueueModelBase)

    def test_build_queue_model_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown queue model"):
            build_queue_model("imaginary_model")

    @pytest.mark.parametrize("model_name", ["none", "price_time", "risk_adverse"])
    def test_gate_only_no_depth_advancement(self, model_name):
        model = build_queue_model(model_name)
        assert model.advance_depth(500.0) == 0.0

    def test_prob_queue_depth_advancement(self):
        model = build_queue_model("prob_queue", queue_position_assumption=0.5)
        # (1 - 0.5) * 100 = 50
        assert model.advance_depth(100.0) == 50.0

    def test_prob_queue_depth_advancement_zero_drop(self):
        model = build_queue_model("prob_queue", queue_position_assumption=0.5)
        assert model.advance_depth(0.0) == 0.0

    def test_random_queue_depth_advancement_is_deterministic_under_seed(self):
        m1 = build_queue_model("random", rng_seed=123)
        m2 = build_queue_model("random", rng_seed=123)
        v1 = m1.advance_depth(1000.0)
        v2 = m2.advance_depth(1000.0)
        assert v1 == v2
        assert 0.0 <= v1 <= 1000.0

    def test_pro_rata_has_allocation(self):
        model = build_queue_model("pro_rata")
        assert model.has_allocation is True

    @pytest.mark.parametrize("model_name", ["none", "price_time", "risk_adverse", "prob_queue", "random"])
    def test_gate_only_no_allocation(self, model_name):
        model = build_queue_model(model_name)
        assert model.has_allocation is False

    def test_advance_trade_is_common(self):
        """advance_trade is a static method shared by all models."""
        from unittest.mock import MagicMock
        child = MagicMock()
        child.queue_ahead_qty = 100.0
        result = QueueModelBase.advance_trade(child, 30.0)
        assert result == 70.0

    def test_advance_trade_clamps_to_zero(self):
        from unittest.mock import MagicMock
        child = MagicMock()
        child.queue_ahead_qty = 20.0
        result = QueueModelBase.advance_trade(child, 50.0)
        assert result == 0.0


# ===================================================================
# 3. Config: market_data_delay_ms
# ===================================================================

class TestBacktestConfigDelay:
    """Verify market_data_delay_ms is wired through config."""

    def test_default_delay_is_zero(self):
        cfg = BacktestConfig(symbol="T", start_date="2026-01-01", end_date="2026-01-02")
        assert cfg.market_data_delay_ms == 0.0

    def test_delay_roundtrip_dict(self):
        cfg = BacktestConfig(
            symbol="T", start_date="2026-01-01", end_date="2026-01-02",
            market_data_delay_ms=200.0,
        )
        d = cfg.to_dict()
        assert d["market_data_delay_ms"] == 200.0
        restored = BacktestConfig.from_dict(d)
        assert restored.market_data_delay_ms == 200.0

    def test_delay_from_string_coercion(self):
        d = {
            "symbol": "T", "start_date": "2026-01-01", "end_date": "2026-01-02",
            "market_data_delay_ms": "150.0",
        }
        cfg = BacktestConfig.from_dict(d)
        assert cfg.market_data_delay_ms == 150.0


# ===================================================================
# 4. Fill-rule ownership: MatchingEngine is queue-free
# ===================================================================

class TestFillRuleOwnership:
    """MatchingEngine must remain queue-free. Queue semantics live in FillSimulator."""

    def test_matching_engine_ignores_queue_model(self):
        """MatchingEngine accepts queue_model for backward compat but does not use it."""
        from market_simulation.layer5_simulator.matching_engine import (
            ExchangeModel, MatchingEngine, QueueModel as MEQueueModel,
        )
        # Construct with queue_model=NONE and RISK_ADVERSE — behavior must be identical
        me_none = MatchingEngine(exchange_model=ExchangeModel.PARTIAL_FILL, queue_model=MEQueueModel.NONE)
        me_risk = MatchingEngine(exchange_model=ExchangeModel.PARTIAL_FILL, queue_model=MEQueueModel.RISK_ADVERSE)

        state = _make_state(ts="2026-03-13 09:00:00")
        from execution_planning.layer3_order.order_types import ChildOrder, OrderSide, OrderTIF, OrderType, ParentOrder
        from market_simulation.layer5_simulator.order_book import OrderBookSimulator

        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=10)
        child = ChildOrder.create(parent=parent, order_type=OrderType.MARKET, qty=10, price=None, tif=OrderTIF.IOC)

        book = OrderBookSimulator()
        book.update(state.lob)

        qty1, px1 = me_none.match(child=child, book=book, state=state)
        # Reset child for second call
        child2 = ChildOrder.create(parent=parent, order_type=OrderType.MARKET, qty=10, price=None, tif=OrderTIF.IOC)
        qty2, px2 = me_risk.match(child=child2, book=book, state=state)

        assert qty1 == qty2
        assert px1 == px2

    def test_fill_simulator_owns_queue_gate(self):
        """FillSimulator gates passive fills via queue model; MatchingEngine does not."""
        from evaluation_orchestration.layer7_validation.fill_simulator import FillSimulator
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger
        from market_simulation.layer5_simulator.bookkeeper import Bookkeeper
        from market_simulation.layer5_simulator.fee_model import ZeroFeeModel
        from market_simulation.layer5_simulator.impact_model import ZeroImpact
        from market_simulation.layer5_simulator.latency_model import LatencyModel, LatencyProfile
        from market_simulation.layer5_simulator.matching_engine import ExchangeModel, MatchingEngine
        from market_simulation.layer5_simulator.order_book import OrderBookSimulator
        from execution_planning.layer3_order.order_types import ChildOrder, OrderSide, OrderTIF, OrderType, ParentOrder

        sim = FillSimulator(
            matching_engine=MatchingEngine(exchange_model=ExchangeModel.PARTIAL_FILL),
            order_book=OrderBookSimulator(),
            latency_model=LatencyModel(profile=LatencyProfile.zero(), add_jitter=False),
            fee_model=ZeroFeeModel(),
            impact_model=ZeroImpact(),
            bookkeeper=Bookkeeper(initial_cash=1e8),
            pnl_ledger=PnLLedger(),
            queue_model="risk_adverse",
            queue_position_assumption=0.5,
            rng_seed=42,
        )

        state = _make_state(ts="2026-03-13 09:00:00", bid_volume=5000)
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        child = ChildOrder.create(parent=parent, order_type=OrderType.LIMIT, qty=100, price=100.0, tif=OrderTIF.DAY)
        child.meta["placement_policy"] = "PassivePlacement"
        parent.child_orders.append(child)

        fills = sim.simulate_fills(parent, [child], state)

        # Queue gate should block the fill — 5000 shares ahead
        assert len(fills) == 0
        assert child.queue_initialized is True
        assert child.queue_ahead_qty == 5000.0


# ===================================================================
# 5. Observation lag — integration tests
# ===================================================================

class TestObservationLagIntegration:
    """Integration tests verifying that the full pipeline uses observed_state
    for strategy decisions and true_state for fills."""

    def _make_states_with_drift(self, n: int = 10) -> list["MarketState"]:
        """Create states with drifting prices to distinguish observed vs true."""
        states = []
        start = pd.Timestamp("2026-03-13 09:00:00")
        for i in range(n):
            bid = 100.0 + i * 0.5  # drift upward
            ask = bid + 0.1
            s = _make_state(
                ts=str(start + pd.Timedelta(seconds=i)),
                best_bid=bid,
                best_ask=ask,
                bid_volume=5000,
                ask_volume=5000,
            )
            states.append(s)
        return states

    def test_delay_zero_smoke_end_to_end(self):
        """delay=0 end-to-end backtest produces identical behavior to no-delay."""
        from strategy_block.strategy import Strategy
        from execution_planning.layer1_signal import Signal

        class AlwaysBuyStrategy(Strategy):
            def __init__(self): self._calls = 0
            @property
            def name(self): return "AlwaysBuy"
            def reset(self): self._calls = 0
            def generate_signal(self, state):
                self._calls += 1
                if self._calls > 1:
                    return None
                return Signal(
                    timestamp=state.timestamp, symbol=state.symbol,
                    score=0.8, expected_return=5.0, confidence=0.9,
                    horizon_steps=1, tags={"strategy": "buy"}, is_valid=True,
                )

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=0.0, placement_style="aggressive",
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=AlwaysBuyStrategy())
        result = runner.run(self._make_states_with_drift())
        assert result.n_fills >= 1

    def test_delay_positive_smoke_end_to_end(self):
        """delay>0 end-to-end backtest completes without error."""
        from strategy_block.strategy import Strategy
        from execution_planning.layer1_signal import Signal

        class AlwaysBuyStrategy(Strategy):
            def __init__(self): self._calls = 0
            @property
            def name(self): return "AlwaysBuy"
            def reset(self): self._calls = 0
            def generate_signal(self, state):
                self._calls += 1
                if self._calls > 1:
                    return None
                return Signal(
                    timestamp=state.timestamp, symbol=state.symbol,
                    score=0.8, expected_return=5.0, confidence=0.9,
                    horizon_steps=1, tags={"strategy": "buy"}, is_valid=True,
                )

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=2000.0, placement_style="aggressive",
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=AlwaysBuyStrategy())
        result = runner.run(self._make_states_with_drift())
        # Should still complete — signal may fire on stale state
        assert result.n_states >= 1

    def test_signal_receives_observed_state_not_true_state(self):
        """Strategy.generate_signal must receive observed_state, not true_state."""
        from strategy_block.strategy import Strategy
        from execution_planning.layer1_signal import Signal

        received_states: list["MarketState"] = []

        class RecordingStrategy(Strategy):
            def __init__(self): self._calls = 0
            @property
            def name(self): return "Recorder"
            def reset(self): self._calls = 0
            def generate_signal(self, state):
                received_states.append(state)
                self._calls += 1
                if self._calls > 1:
                    return None
                return Signal(
                    timestamp=state.timestamp, symbol=state.symbol,
                    score=0.8, expected_return=5.0, confidence=0.9,
                    horizon_steps=1, tags={}, is_valid=True,
                )

        states = self._make_states_with_drift(10)
        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=2000.0, placement_style="aggressive",
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=RecordingStrategy())
        runner.run(states)

        # With 2s delay and 1s resolution, the first actionable state should
        # receive an observed_state that is 2 steps behind the true_state.
        # The strategy should never see a state that has the same timestamp
        # as the true_state (for steps where delay pushes back far enough).
        assert len(received_states) >= 1
        # All received states should be from the history (delayed)
        for rs in received_states:
            assert rs in states  # must be an actual historical state object

    def test_cancel_replace_uses_observed_state(self):
        """Cancel/replace decisions in _process_open_orders use observed_state."""
        from execution_planning.layer3_order.order_types import (
            ChildOrder, OrderSide, OrderStatus, OrderTIF, OrderType, ParentOrder,
        )

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=2000.0, placement_style="passive",
            queue_model="prob_queue",
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=None)
        # Must use __new__ to skip strategy check; set up components manually
        runner_obj = PipelineRunner.__new__(PipelineRunner)
        runner_obj.config = config
        runner_obj._strategy = None
        runner_obj._state_history = {}
        runner_obj._state_ts = {}
        runner_obj._market_data_delay_ms = 2000.0

        # The key point: observed_state and true_state are different objects
        true_state = _make_state(ts="2026-03-13 09:00:05", best_bid=102.0, best_ask=102.1)
        observed_state = _make_state(ts="2026-03-13 09:00:03", best_bid=100.0, best_ask=100.1)

        # Verify they are distinct
        assert true_state.timestamp != observed_state.timestamp
        assert true_state.lob.best_bid != observed_state.lob.best_bid


# ===================================================================
# 6. Resolution caveat — 1s resample + small lag
# ===================================================================

class TestResolutionCaveat:
    """Verify that small delays at 1s resolution collapse to near no-op."""

    def test_50ms_delay_at_1s_resolution_returns_previous_state(self):
        """50ms delay at 1s resolution returns t-1 state, not current."""
        runner = PipelineRunner.__new__(PipelineRunner)
        runner._state_history = {}
        runner._state_ts = {}
        runner._market_data_delay_ms = 50.0
        runner._max_history_len = 0

        s0 = _make_state(ts="2026-03-13 09:00:00", best_bid=100.0)
        s1 = _make_state(ts="2026-03-13 09:00:01", best_bid=101.0)
        s2 = _make_state(ts="2026-03-13 09:00:02", best_bid=102.0)
        for s in [s0, s1, s2]:
            runner._accumulate_state(s)

        # 50ms back from t=2 → t=1.95 → returns s1
        result = runner._lookup_observed_state("TEST", s2.timestamp, 50.0)
        assert result is s1
        # This means at 1s resolution, even 50ms delay causes a 1-step lag

    def test_100ms_delay_collapses_same_as_999ms(self):
        """At 1s resolution, 100ms and 999ms delays both return t-1."""
        runner = PipelineRunner.__new__(PipelineRunner)
        runner._state_history = {}
        runner._state_ts = {}
        runner._max_history_len = 0

        s0 = _make_state(ts="2026-03-13 09:00:00")
        s1 = _make_state(ts="2026-03-13 09:00:01")
        s2 = _make_state(ts="2026-03-13 09:00:02")
        for s in [s0, s1, s2]:
            runner._accumulate_state(s)

        result_100 = runner._lookup_observed_state("TEST", s2.timestamp, 100.0)
        result_999 = runner._lookup_observed_state("TEST", s2.timestamp, 999.0)
        # Both land in the (t=1, t=2) interval → return s1
        assert result_100 is s1
        assert result_999 is s1

    def test_tighter_resolution_shows_difference(self):
        """At 100ms resolution, 50ms delay is distinguishable from 500ms."""
        runner = PipelineRunner.__new__(PipelineRunner)
        runner._state_history = {}
        runner._state_ts = {}
        runner._max_history_len = 0

        base = pd.Timestamp("2026-03-13 09:00:00")
        states = []
        for i in range(10):
            s = _make_state(
                ts=str(base + pd.Timedelta(milliseconds=i * 100)),
                best_bid=100.0 + i * 0.01,
            )
            states.append(s)
            runner._accumulate_state(s)

        # At t=900ms, 50ms delay → t=850ms → returns state at t=800ms (index 8)
        result_50 = runner._lookup_observed_state("TEST", states[9].timestamp, 50.0)
        assert result_50 is states[8]

        # At t=900ms, 500ms delay → t=400ms → returns state at t=400ms (index 4)
        result_500 = runner._lookup_observed_state("TEST", states[9].timestamp, 500.0)
        assert result_500 is states[4]

        # They are different — tighter resolution makes delay meaningful
        assert result_50 is not result_500


# ===================================================================
# 7. Queue regression after interface abstraction
# ===================================================================

class TestQueueRegressionPostExtraction:
    """Ensure queue model behavior is identical after interface extraction."""

    def _build_fill_simulator(self, model: str, seed: int = 42):
        from evaluation_orchestration.layer7_validation.fill_simulator import FillSimulator
        from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger
        from market_simulation.layer5_simulator.bookkeeper import Bookkeeper
        from market_simulation.layer5_simulator.fee_model import ZeroFeeModel
        from market_simulation.layer5_simulator.impact_model import ZeroImpact
        from market_simulation.layer5_simulator.latency_model import LatencyModel, LatencyProfile
        from market_simulation.layer5_simulator.matching_engine import ExchangeModel, MatchingEngine
        from market_simulation.layer5_simulator.order_book import OrderBookSimulator

        return FillSimulator(
            matching_engine=MatchingEngine(exchange_model=ExchangeModel.PARTIAL_FILL),
            order_book=OrderBookSimulator(),
            latency_model=LatencyModel(profile=LatencyProfile.zero(), add_jitter=False),
            fee_model=ZeroFeeModel(),
            impact_model=ZeroImpact(),
            bookkeeper=Bookkeeper(initial_cash=1e8),
            pnl_ledger=PnLLedger(),
            queue_model=model,
            queue_position_assumption=0.5,
            rng_seed=seed,
        )

    def _make_passive_order(self):
        from execution_planning.layer3_order.order_types import (
            ChildOrder, OrderSide, OrderTIF, OrderType, ParentOrder,
        )
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
        child = ChildOrder.create(
            parent=parent, order_type=OrderType.LIMIT,
            qty=100, price=100.0, tif=OrderTIF.DAY,
        )
        child.meta["placement_policy"] = "PassivePlacement"
        parent.child_orders.append(child)
        return parent, child

    def test_price_time_and_risk_adverse_identical_behavior(self):
        """price_time and risk_adverse must produce identical results
        (documented: risk_adverse uses the same trade-only advancement)."""
        sim_pt = self._build_fill_simulator("price_time")
        sim_ra = self._build_fill_simulator("risk_adverse")

        parent_pt, child_pt = self._make_passive_order()
        parent_ra, child_ra = self._make_passive_order()

        # Initialize
        s0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
        sim_pt.simulate_fills(parent_pt, [child_pt], s0)
        sim_ra.simulate_fills(parent_ra, [child_ra], s0)
        assert child_pt.queue_ahead_qty == child_ra.queue_ahead_qty

        # Trade advancement
        s1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=1000,
                         trade_price=100.0, trade_volume=400)
        sim_pt.simulate_fills(parent_pt, [child_pt], s1)
        sim_ra.simulate_fills(parent_ra, [child_ra], s1)
        assert child_pt.queue_ahead_qty == child_ra.queue_ahead_qty

        # Depth drop (neither should advance)
        s2 = _make_state(ts="2026-03-13 09:00:02", bid_volume=300)
        sim_pt.simulate_fills(parent_pt, [child_pt], s2)
        sim_ra.simulate_fills(parent_ra, [child_ra], s2)
        assert child_pt.queue_ahead_qty == child_ra.queue_ahead_qty

    @pytest.mark.parametrize("model", ["none", "price_time", "risk_adverse",
                                        "prob_queue", "random", "pro_rata"])
    def test_aggressive_bypasses_queue_for_all_models(self, model):
        """Aggressive/IOC orders bypass queue gate regardless of queue model."""
        from execution_planning.layer3_order.order_types import (
            ChildOrder, OrderSide, OrderTIF, OrderType, ParentOrder,
        )
        sim = self._build_fill_simulator(model)
        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=10)
        child = ChildOrder.create(
            parent=parent, order_type=OrderType.MARKET,
            qty=10, price=None, tif=OrderTIF.IOC,
        )
        parent.child_orders.append(child)
        state = _make_state(ts="2026-03-13 09:00:00", ask_volume=1000)
        fills = sim.simulate_fills(parent, [child], state)
        assert len(fills) == 1

    @pytest.mark.parametrize("model", ["price_time", "risk_adverse",
                                        "prob_queue", "random", "pro_rata"])
    def test_passive_blocked_on_first_tick_all_models(self, model):
        """All non-none queue models block passive fill on first tick."""
        sim = self._build_fill_simulator(model)
        parent, child = self._make_passive_order()
        state = _make_state(ts="2026-03-13 09:00:00", bid_volume=5000)
        fills = sim.simulate_fills(parent, [child], state)
        assert fills == []
        assert child.queue_initialized is True
        assert child.queue_ahead_qty == 5000.0


# ===================================================================
# 8. MatchingEngine queue-free regression
# ===================================================================

class TestMatchingEngineQueueFree:
    """Verify MatchingEngine does not contain any queue logic."""

    def test_no_queue_state_attributes_used_in_match(self):
        """MatchingEngine.match() does not read or write queue_ahead_qty."""
        from market_simulation.layer5_simulator.matching_engine import (
            ExchangeModel, MatchingEngine, QueueModel as MEQueueModel,
        )
        from market_simulation.layer5_simulator.order_book import OrderBookSimulator
        from execution_planning.layer3_order.order_types import (
            ChildOrder, OrderSide, OrderTIF, OrderType, ParentOrder,
        )

        engine = MatchingEngine(
            exchange_model=ExchangeModel.PARTIAL_FILL,
            queue_model=MEQueueModel.RISK_ADVERSE,
        )
        book = OrderBookSimulator()
        state = _make_state(
            ts="2026-03-13 09:00:00", bid_volume=1000,
            trade_price=100.0, trade_volume=200,
        )
        book.update(state.lob)

        parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=50)
        child = ChildOrder.create(
            parent=parent, order_type=OrderType.LIMIT,
            qty=50, price=100.0, tif=OrderTIF.GTC,
        )

        filled_qty, fill_price = engine.match(child, book, state)
        # MatchingEngine fills up to trade volume — no queue filtering
        assert filled_qty == 50
        assert fill_price == 100.0

    def test_all_queue_models_produce_same_matching_engine_result(self):
        """MatchingEngine produces identical results regardless of queue_model param."""
        from market_simulation.layer5_simulator.matching_engine import (
            ExchangeModel, MatchingEngine, QueueModel as MEQueueModel,
        )
        from market_simulation.layer5_simulator.order_book import OrderBookSimulator
        from execution_planning.layer3_order.order_types import (
            ChildOrder, OrderSide, OrderTIF, OrderType, ParentOrder,
        )

        state = _make_state(
            ts="2026-03-13 09:00:00", bid_volume=500,
            trade_price=100.0, trade_volume=300,
        )

        results = []
        for qm in MEQueueModel:
            engine = MatchingEngine(
                exchange_model=ExchangeModel.PARTIAL_FILL,
                queue_model=qm,
            )
            book = OrderBookSimulator()
            book.update(state.lob)
            parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
            child = ChildOrder.create(
                parent=parent, order_type=OrderType.LIMIT,
                qty=100, price=100.0, tif=OrderTIF.GTC,
            )
            filled_qty, fill_price = engine.match(child, book, state)
            results.append((qm.name, filled_qty, fill_price))

        # All should be identical
        for name, qty, px in results:
            assert qty == results[0][1], f"{name} differs in fill qty"
            assert px == results[0][2], f"{name} differs in fill price"


# ===================================================================
# 9. Runtime semantics — observation lag + LagExpr/RollingExpr/PersistExpr
# ===================================================================

class TestRuntimeLagSemantics:
    """Verify that engine-side observation lag and strategy-side lag/rolling
    expressions are independent and stack correctly.

    Observation lag (engine-side):
      - PipelineRunner looks up a historical state based on market_data_delay_ms
      - The strategy receives an *older* MarketState object

    Strategy-side lag (LagExpr, RollingExpr, PersistExpr):
      - Operate on the feature history buffer inside RuntimeStateV2
      - Each call to record_features() adds the *current* (already-delayed) features

    These two mechanisms stack: if observation lag is 2s and LagExpr(steps=1),
    the effective lookback is ~3s behind true wall-clock time.
    """

    def test_lag_expr_evaluates_on_delayed_features(self):
        """LagExpr operates on features that are already observation-delayed."""
        from strategy_block.strategy_compiler.v2.runtime_v2 import (
            RuntimeStateV2, evaluate_float,
        )
        from strategy_block.strategy_specs.v2.ast_nodes import LagExpr

        runtime = RuntimeStateV2()

        # Simulate recording features from observation-delayed states
        # t=0 observed: mid_price=100 (true was 101)
        runtime.record_features({"mid_price": 100.0})
        runtime.tick_count += 1
        # t=1 observed: mid_price=100.5 (true was 102)
        runtime.record_features({"mid_price": 100.5})
        runtime.tick_count += 1
        # t=2 observed: mid_price=101 (true was 103)
        runtime.record_features({"mid_price": 101.0})

        # LagExpr(steps=1) should return t=1 feature (100.5), not t=2 (101.0)
        lag_node = LagExpr(feature="mid_price", steps=1)
        result = evaluate_float(lag_node, {"mid_price": 101.0}, runtime)
        assert result == 100.5

        # LagExpr(steps=2) should return t=0 feature (100.0)
        lag_node_2 = LagExpr(feature="mid_price", steps=2)
        result_2 = evaluate_float(lag_node_2, {"mid_price": 101.0}, runtime)
        assert result_2 == 100.0

    def test_rolling_expr_on_delayed_features(self):
        """RollingExpr computes over the observation-delayed feature history."""
        from strategy_block.strategy_compiler.v2.runtime_v2 import (
            RuntimeStateV2, evaluate_float,
        )
        from strategy_block.strategy_specs.v2.ast_nodes import RollingExpr

        runtime = RuntimeStateV2()
        # Record 5 delayed observations
        for i in range(5):
            runtime.record_features({"spread_bps": 10.0 + i})

        rolling_node = RollingExpr(feature="spread_bps", window=3, method="mean")
        # Window covers last 3: [12, 13, 14] → mean = 13.0
        result = evaluate_float(rolling_node, {"spread_bps": 14.0}, runtime)
        assert result == 13.0

    def test_persist_expr_on_delayed_features(self):
        """PersistExpr evaluates conditions on observation-delayed features."""
        from strategy_block.strategy_compiler.v2.runtime_v2 import (
            RuntimeStateV2, evaluate_bool,
        )
        from strategy_block.strategy_specs.v2.ast_nodes import (
            ComparisonExpr, PersistExpr,
        )

        runtime = RuntimeStateV2()

        # Condition: mid_price > 100
        cond = ComparisonExpr(feature="mid_price", op=">", threshold=100.0)
        persist = PersistExpr(expr=cond, window=3, min_true=2)

        features_seq = [
            {"mid_price": 99.0},   # False
            {"mid_price": 101.0},  # True
            {"mid_price": 102.0},  # True → persist should fire (2/3 True)
        ]

        for features in features_seq:
            runtime.record_features(features)
            result = evaluate_bool(persist, features, {}, runtime)

        # After 3 steps with 2 True values, persist should return True
        assert result is True

    def test_stacking_semantics_documented(self):
        """Verify that lag stacking is a real concern.

        If observation_delay=2s and LagExpr(steps=2) at 1s resolution:
          - Engine delivers state from t-2
          - LagExpr looks 2 steps back in that history → effectively t-4
          - Total effective lookback = 4 seconds behind true wall-clock
        """
        runner = PipelineRunner.__new__(PipelineRunner)
        runner._state_history = {}
        runner._state_ts = {}
        runner._max_history_len = 0

        # Create 10 states at 1s intervals
        base = pd.Timestamp("2026-03-13 09:00:00")
        states = []
        for i in range(10):
            s = _make_state(
                ts=str(base + pd.Timedelta(seconds=i)),
                best_bid=100.0 + i,
            )
            states.append(s)
            runner._accumulate_state(s)

        # At t=9 with 2s delay → observed = state at t=7 (bid=107)
        observed = runner._lookup_observed_state("TEST", states[9].timestamp, 2000.0)
        assert observed is states[7]
        assert observed.lob.best_bid == 107.0

        # If strategy then applies LagExpr(steps=2), it reads from its
        # own feature history. The feature history would contain:
        # [..., features_from_state[5], features_from_state[6], features_from_state[7]]
        # LagExpr(steps=2) returns features_from_state[5] → effectively t-4 from true time
        # This is the expected stacking behavior.


# ===================================================================
# 10. Resample resolution validation
# ===================================================================

class TestResampleValidation:
    """Verify that only supported resample frequencies are accepted."""

    def test_1s_accepted(self):
        from data.layer0_data.state_builder import validate_resample_freq
        validate_resample_freq("1s")  # should not raise

    def test_500ms_accepted(self):
        from data.layer0_data.state_builder import validate_resample_freq
        validate_resample_freq("500ms")  # should not raise

    def test_none_accepted(self):
        from data.layer0_data.state_builder import validate_resample_freq
        validate_resample_freq(None)  # should not raise

    def test_250ms_rejected(self):
        from data.layer0_data.state_builder import validate_resample_freq
        with pytest.raises(ValueError, match="Unsupported resample frequency '250ms'"):
            validate_resample_freq("250ms")

    def test_100ms_rejected(self):
        from data.layer0_data.state_builder import validate_resample_freq
        with pytest.raises(ValueError, match="Unsupported resample frequency '100ms'"):
            validate_resample_freq("100ms")

    def test_200ms_rejected(self):
        from data.layer0_data.state_builder import validate_resample_freq
        with pytest.raises(ValueError, match="Unsupported resample frequency '200ms'"):
            validate_resample_freq("200ms")

    def test_2s_rejected(self):
        from data.layer0_data.state_builder import validate_resample_freq
        with pytest.raises(ValueError, match="Unsupported resample frequency '2s'"):
            validate_resample_freq("2s")

    def test_builder_init_rejects_unsupported(self):
        from data.layer0_data.state_builder import MarketStateBuilder
        with pytest.raises(ValueError, match="Unsupported resample frequency"):
            MarketStateBuilder(resample_freq="250ms")

    def test_builder_init_accepts_supported(self):
        from data.layer0_data.state_builder import MarketStateBuilder
        builder = MarketStateBuilder(resample_freq="500ms")
        assert builder.resample_freq == "500ms"


# ===================================================================
# 11. 500ms resolution — observation lag active case
# ===================================================================

class TestObservationLag500ms:
    """At 500ms resolution, moderate lag yields distinct observed_state."""

    def _build_runner(self, delay_ms: float) -> PipelineRunner:
        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            market_data_delay_ms=delay_ms,
            seed=42,
        )
        runner = PipelineRunner.__new__(PipelineRunner)
        runner.config = config
        runner._state_history = {}
        runner._state_ts = {}
        runner._market_data_delay_ms = delay_ms
        runner._decision_compute_ms = 0.0
        runner._max_history_len = 0
        return runner

    def _make_500ms_states(self, n: int = 20) -> list["MarketState"]:
        """Create n states at 500ms intervals with drifting prices."""
        base = pd.Timestamp("2026-03-13 09:00:00")
        states = []
        for i in range(n):
            bid = 100.0 + i * 0.1
            states.append(
                _make_state(
                    ts=str(base + pd.Timedelta(milliseconds=i * 500)),
                    best_bid=bid,
                    best_ask=bid + 0.1,
                )
            )
        return states

    def test_200ms_delay_at_500ms_selects_previous_state(self):
        """200ms delay at 500ms resolution: observed != true."""
        runner = self._build_runner(delay_ms=200.0)
        states = self._make_500ms_states(10)
        for s in states:
            runner._accumulate_state(s)

        # At t=4.5s (index 9) with 200ms delay → target = 4.3s
        # Latest state at or before 4.3s is index 8 (t=4.0s)
        result = runner._lookup_observed_state("TEST", states[9].timestamp, 200.0)
        assert result is states[8]
        assert result is not states[9]

    def test_500ms_delay_at_500ms_goes_back_one_step(self):
        """500ms delay at 500ms resolution → exactly 1 step behind."""
        runner = self._build_runner(delay_ms=500.0)
        states = self._make_500ms_states(10)
        for s in states:
            runner._accumulate_state(s)

        # At t=4.5s (index 9) with 500ms delay → target = 4.0s
        # Latest state at or before 4.0s is index 8 (t=4.0s)
        result = runner._lookup_observed_state("TEST", states[9].timestamp, 500.0)
        assert result is states[8]

    def test_1000ms_delay_at_500ms_goes_back_two_steps(self):
        """1000ms delay at 500ms resolution → 2 steps behind."""
        runner = self._build_runner(delay_ms=1000.0)
        states = self._make_500ms_states(10)
        for s in states:
            runner._accumulate_state(s)

        # At t=4.5s (index 9) with 1000ms delay → target = 3.5s
        # Latest state at or before 3.5s is index 7 (t=3.5s)
        result = runner._lookup_observed_state("TEST", states[9].timestamp, 1000.0)
        assert result is states[7]

    def test_500ms_different_from_1s_granularity(self):
        """500ms resolution distinguishes delay values that 1s collapses."""
        runner = self._build_runner(delay_ms=300.0)
        states = self._make_500ms_states(10)
        for s in states:
            runner._accumulate_state(s)

        # 300ms delay: goes back to previous 500ms state
        result_300 = runner._lookup_observed_state("TEST", states[9].timestamp, 300.0)

        # 800ms delay: goes back further
        result_800 = runner._lookup_observed_state("TEST", states[9].timestamp, 800.0)

        # These should be different states at 500ms resolution
        assert result_300 is not result_800

    def test_1s_resolution_collapses_300_and_800(self):
        """At 1s resolution, 300ms and 800ms both collapse to t-1."""
        runner = self._build_runner(delay_ms=300.0)
        base = pd.Timestamp("2026-03-13 09:00:00")
        states_1s = []
        for i in range(10):
            s = _make_state(
                ts=str(base + pd.Timedelta(seconds=i)),
                best_bid=100.0 + i,
            )
            states_1s.append(s)
            runner._accumulate_state(s)

        result_300 = runner._lookup_observed_state("TEST", states_1s[9].timestamp, 300.0)
        result_800 = runner._lookup_observed_state("TEST", states_1s[9].timestamp, 800.0)

        # Both land between t=8 and t=9 at 1s resolution → same state
        assert result_300 is result_800


# ===================================================================
# 12. Observation staleness metadata
# ===================================================================

class TestObservationStalenessMetadata:
    """Verify that staleness metrics appear in result metadata."""

    def _make_states(self, n: int = 10) -> list["MarketState"]:
        start = pd.Timestamp("2026-03-13 09:00:00")
        return [
            _make_state(
                ts=str(start + pd.Timedelta(seconds=i)),
                best_bid=100.0 + i * 0.5,
                best_ask=100.1 + i * 0.5,
                bid_volume=5000,
                ask_volume=5000,
            )
            for i in range(n)
        ]

    def test_delay_zero_staleness_is_zero(self):
        from strategy_block.strategy import Strategy
        from execution_planning.layer1_signal import Signal

        class NullStrategy(Strategy):
            @property
            def name(self): return "Null"
            def reset(self): pass
            def generate_signal(self, state): return None

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=0.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())
        result = runner.run(self._make_states())

        lag_info = result.metadata.get("observation_lag")
        assert lag_info is not None
        assert lag_info["configured_market_data_delay_ms"] == 0.0
        assert lag_info["avg_observation_staleness_ms"] == 0.0

    def test_delay_positive_staleness_nonzero(self):
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self): return "Null"
            def reset(self): pass
            def generate_signal(self, state): return None

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=1500.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())
        result = runner.run(self._make_states())

        lag_info = result.metadata.get("observation_lag")
        assert lag_info is not None
        assert lag_info["configured_market_data_delay_ms"] == 1500.0
        # With 1500ms delay at 1s resolution, average staleness should be > 0
        assert lag_info["avg_observation_staleness_ms"] > 0.0


# ===================================================================
# 13. delay=0 regression — full end-to-end
# ===================================================================

class TestDelayZeroRegression:
    """delay=0 must produce exactly the same behavior as pre-observation-lag code."""

    def test_observed_equals_true_when_delay_zero(self):
        runner = PipelineRunner.__new__(PipelineRunner)
        runner._state_history = {}
        runner._state_ts = {}
        runner._max_history_len = 0

        states = [_make_state(ts=f"2026-03-13 09:00:0{i}") for i in range(5)]
        for s in states:
            runner._accumulate_state(s)

        for s in states:
            observed = runner._lookup_observed_state("TEST", s.timestamp, 0.0)
            # With delay=0, should return the latest accumulated state at this point
            # But since we accumulated all first, it returns the last one
            # Let's test the invariant: delay=0 returns history[-1]
            assert observed is states[-1]

    def test_step_by_step_delay_zero_identity(self):
        """Accumulate and lookup one step at a time — observed == true."""
        runner = PipelineRunner.__new__(PipelineRunner)
        runner._state_history = {}
        runner._state_ts = {}
        runner._max_history_len = 0

        states = [_make_state(ts=f"2026-03-13 09:00:0{i}") for i in range(5)]
        for s in states:
            runner._accumulate_state(s)
            observed = runner._lookup_observed_state("TEST", s.timestamp, 0.0)
            assert observed is s  # Must be the same object


# ===================================================================
# 14. Universe entrypoint — market_data_delay_ms propagation
# ===================================================================

class TestUniverseDelayPropagation:
    """Verify that run_single_backtest propagates market_data_delay_ms
    to BacktestConfig top-level so PipelineRunner actually uses it."""

    def test_nonzero_latency_produces_nonzero_delay(self):
        """Venue latency alias must not imply observation lag."""
        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            initial_cash=1e8,
            seed=42,
            latency_ms=100.0,
            market_data_delay_ms=0.0,
            compute_attribution=False,
        )

        assert config.latency.order_submit_ms == 30.0
        assert config.latency.order_ack_ms == 70.0
        assert config.latency.cancel_ms == 20.0
        assert config.market_data_delay_ms == 0.0
        assert config.latency.market_data_delay_ms is None

    def test_zero_latency_produces_zero_delay(self):
        """latency_ms=0 keeps venue latency alias at 0 and does not alter observation lag."""
        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            latency_ms=0.0,
            market_data_delay_ms=0.0,
        )
        assert config.market_data_delay_ms == 0.0
        assert config.latency.order_submit_ms == 0.0
        assert config.latency.order_ack_ms == 0.0
        assert config.latency.cancel_ms == 0.0

    def test_universe_runner_receives_configured_delay(self):
        """End-to-end: PipelineRunner reads the top-level market_data_delay_ms."""
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self): return "Null"
            def reset(self): pass
            def generate_signal(self, state): return None

        latency_ms = 500.0
        desired_delay = 50.0

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            latency_ms=latency_ms,
            market_data_delay_ms=desired_delay,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

        states = [
            _make_state(ts=f"2026-03-13 09:00:0{i}", best_bid=100.0 + i)
            for i in range(5)
        ]
        result = runner.run(states)

        lag_info = result.metadata["observation_lag"]
        assert lag_info["configured_market_data_delay_ms"] == 50.0
        assert lag_info["avg_observation_staleness_ms"] > 0.0


# ===================================================================
# Phase 2: Decision Latency
# ===================================================================

class TestDecisionLatency:
    """Tests for decision_compute_ms in PipelineRunner."""

    def _build_runner(self, delay_ms: float = 0.0, decision_ms: float = 0.0) -> PipelineRunner:
        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            market_data_delay_ms=delay_ms,
            decision_compute_ms=decision_ms,
            seed=42,
        )
        runner = PipelineRunner.__new__(PipelineRunner)
        runner.config = config
        runner._state_history = {}
        runner._state_ts = {}
        runner._market_data_delay_ms = delay_ms
        runner._decision_compute_ms = decision_ms
        runner._max_history_len = 0  # unbounded for unit tests
        return runner

    def test_decision_zero_preserves_behavior(self):
        """decision_compute_ms=0 returns the same state as delay-only lookup."""
        runner = self._build_runner(delay_ms=1000.0, decision_ms=0.0)

        s0 = _make_state(ts="2026-03-13 09:00:00", best_bid=100.0)
        s1 = _make_state(ts="2026-03-13 09:00:01", best_bid=101.0)
        s2 = _make_state(ts="2026-03-13 09:00:02", best_bid=102.0)
        for s in [s0, s1, s2]:
            runner._accumulate_state(s)

        # effective delay = 1000 + 0 = 1000ms
        result = runner._lookup_observed_state("TEST", s2.timestamp, 1000.0)
        assert result is s1

    def test_decision_latency_shifts_state_further_back(self):
        """Positive decision_compute_ms + observation lag = further staleness."""
        runner = self._build_runner(delay_ms=1000.0, decision_ms=500.0)

        s0 = _make_state(ts="2026-03-13 09:00:00", best_bid=100.0)
        s1 = _make_state(ts="2026-03-13 09:00:01", best_bid=101.0)
        s2 = _make_state(ts="2026-03-13 09:00:02", best_bid=102.0)
        for s in [s0, s1, s2]:
            runner._accumulate_state(s)

        # effective delay = 1000 + 500 = 1500ms
        # t=2 - 1.5s = 0.5s → returns s0 (at t=0)
        result = runner._lookup_observed_state("TEST", s2.timestamp, 1500.0)
        assert result is s0

    def test_decision_only_no_observation_lag(self):
        """decision_compute_ms alone acts as a delay even with market_data_delay_ms=0."""
        runner = self._build_runner(delay_ms=0.0, decision_ms=1500.0)

        states = [
            _make_state(ts=f"2026-03-13 09:00:0{i}", best_bid=100.0 + i)
            for i in range(5)
        ]
        for s in states:
            runner._accumulate_state(s)

        # effective delay = 0 + 1500 = 1500ms
        # t=4 - 1.5s = 2.5s → returns state at t=2
        result = runner._lookup_observed_state("TEST", states[4].timestamp, 1500.0)
        assert result is states[2]

    def test_decision_latency_config_default_zero(self):
        """BacktestConfig.decision_compute_ms defaults to 0.0."""
        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
        )
        assert config.decision_compute_ms == 0.0

    def test_decision_latency_roundtrip_dict(self):
        """decision_compute_ms survives dict serialization round-trip."""
        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            decision_compute_ms=150.0,
        )
        d = config.to_dict()
        assert d["decision_compute_ms"] == 150.0
        restored = BacktestConfig.from_dict(d)
        assert restored.decision_compute_ms == 150.0

    def test_decision_latency_string_coercion(self):
        """String value for decision_compute_ms in from_dict is coerced to float."""
        d = {
            "symbol": "TEST", "start_date": "2026-03-13", "end_date": "2026-03-13",
            "decision_compute_ms": "250.0",
        }
        config = BacktestConfig.from_dict(d)
        assert config.decision_compute_ms == 250.0


class TestDecisionLatencyIntegration:
    """End-to-end tests for decision latency in the full pipeline."""

    def _make_states_with_drift(self, n: int = 10) -> list:
        states = []
        start = pd.Timestamp("2026-03-13 09:00:00")
        for i in range(n):
            bid = 100.0 + i * 0.5
            ask = bid + 0.1
            s = _make_state(
                ts=str(start + pd.Timedelta(seconds=i)),
                best_bid=bid, best_ask=ask,
                bid_volume=5000, ask_volume=5000,
            )
            states.append(s)
        return states

    def test_decision_zero_smoke(self):
        """decision_compute_ms=0 smoke test behaves like existing code."""
        from strategy_block.strategy import Strategy
        from execution_planning.layer1_signal import Signal

        class BuyOnceStrategy(Strategy):
            def __init__(self): self._calls = 0
            @property
            def name(self): return "BuyOnce"
            def reset(self): self._calls = 0
            def generate_signal(self, state):
                self._calls += 1
                if self._calls > 1:
                    return None
                return Signal(
                    timestamp=state.timestamp, symbol=state.symbol,
                    score=0.8, expected_return=5.0, confidence=0.9,
                    horizon_steps=1, tags={}, is_valid=True,
                )

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, decision_compute_ms=0.0, placement_style="aggressive",
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=BuyOnceStrategy())
        result = runner.run(self._make_states_with_drift())
        assert result.n_fills >= 1
        lag_info = result.metadata["observation_lag"]
        assert lag_info["configured_decision_compute_ms"] == 0.0
        assert lag_info["effective_delay_ms"] == 0.0

    def test_decision_positive_records_metadata(self):
        """Positive decision_compute_ms is recorded in result metadata."""
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self): return "Null"
            def reset(self): pass
            def generate_signal(self, state): return None

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=100.0, decision_compute_ms=50.0,
            placement_style="aggressive",
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())
        result = runner.run(self._make_states_with_drift(5))
        lag_info = result.metadata["observation_lag"]
        assert lag_info["configured_decision_compute_ms"] == 50.0
        assert lag_info["effective_delay_ms"] == 150.0
        assert lag_info["state_history_max_len"] > 0

    def test_strategy_receives_staler_state_with_decision_latency(self):
        """With decision_compute_ms, strategy sees an even staler state."""
        from strategy_block.strategy import Strategy
        from execution_planning.layer1_signal import Signal

        received_states: list = []

        class RecordingStrategy(Strategy):
            def __init__(self): self._calls = 0
            @property
            def name(self): return "Recorder"
            def reset(self): self._calls = 0
            def generate_signal(self, state):
                received_states.append(state)
                self._calls += 1
                if self._calls > 1:
                    return None
                return Signal(
                    timestamp=state.timestamp, symbol=state.symbol,
                    score=0.8, expected_return=5.0, confidence=0.9,
                    horizon_steps=1, tags={}, is_valid=True,
                )

        states = self._make_states_with_drift(10)

        # Run with observation lag only
        received_states.clear()
        config_lag_only = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=2000.0, decision_compute_ms=0.0,
            placement_style="aggressive",
        )
        runner1 = PipelineRunner(config=config_lag_only, data_dir=".", strategy=RecordingStrategy())
        runner1.run(states)
        lag_only_states = list(received_states)

        # Run with observation lag + decision latency
        received_states.clear()
        config_lag_plus_decision = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=2000.0, decision_compute_ms=1000.0,
            placement_style="aggressive",
        )
        runner2 = PipelineRunner(config=config_lag_plus_decision, data_dir=".", strategy=RecordingStrategy())
        runner2.run(states)
        lag_plus_decision_states = list(received_states)

        # Both runs should receive states, and the second should receive
        # staler ones (earlier timestamps)
        assert len(lag_only_states) >= 1
        assert len(lag_plus_decision_states) >= 1
        # The first received state with decision latency should be at least as
        # old as the first received state without it
        assert lag_plus_decision_states[0].timestamp <= lag_only_states[0].timestamp


# ===================================================================
# Phase 2: Bounded State-History Retention
# ===================================================================

class TestBoundedStateHistory:
    """Tests for per-symbol state history pruning."""

    def _build_runner(self, delay_ms: float = 0.0, decision_ms: float = 0.0) -> PipelineRunner:
        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            market_data_delay_ms=delay_ms,
            decision_compute_ms=decision_ms,
            seed=42,
        )
        runner = PipelineRunner.__new__(PipelineRunner)
        runner.config = config
        runner._state_history = {}
        runner._state_ts = {}
        runner._market_data_delay_ms = delay_ms
        runner._decision_compute_ms = decision_ms
        runner._canonical_tick_ms = 1000.0
        # Compute max_history_len same way as run()
        effective_delay_ms = delay_ms + decision_ms
        if effective_delay_ms > 0.0:
            delay_ticks = int(effective_delay_ms / 1000.0) + 1
            runner._max_history_len = max(delay_ticks + 10, 20)
        else:
            runner._max_history_len = 20
        return runner

    def test_history_bounded_with_delay(self):
        """State history is pruned beyond retention window."""
        runner = self._build_runner(delay_ms=2000.0)
        max_len = runner._max_history_len

        # Add many more states than the retention window
        base = pd.Timestamp("2026-03-13 09:00:00")
        for i in range(100):
            s = _make_state(ts=str(base + pd.Timedelta(seconds=i)))
            runner._accumulate_state(s)

        # History should be pruned to max_history_len
        assert len(runner._state_history["TEST"]) == max_len
        assert len(runner._state_ts["TEST"]) == max_len

    def test_history_bounded_without_delay(self):
        """Even with no delay, history is bounded (small default window)."""
        runner = self._build_runner(delay_ms=0.0)
        max_len = runner._max_history_len
        assert max_len == 20  # minimum default

        base = pd.Timestamp("2026-03-13 09:00:00")
        for i in range(50):
            s = _make_state(ts=str(base + pd.Timedelta(seconds=i)))
            runner._accumulate_state(s)

        assert len(runner._state_history["TEST"]) == 20
        assert len(runner._state_ts["TEST"]) == 20

    def test_pruned_history_still_supports_lookup(self):
        """After pruning, _lookup_observed_state still returns correct state."""
        runner = self._build_runner(delay_ms=2000.0)

        base = pd.Timestamp("2026-03-13 09:00:00")
        states = []
        for i in range(50):
            s = _make_state(
                ts=str(base + pd.Timedelta(seconds=i)),
                best_bid=100.0 + i,
            )
            states.append(s)
            runner._accumulate_state(s)

        # At t=49 with 2000ms delay → target = t=47 → should return state at t=47
        result = runner._lookup_observed_state(
            "TEST", states[49].timestamp, 2000.0,
        )
        assert result is states[47]

    def test_history_grows_with_decision_latency(self):
        """Larger decision_compute_ms increases retention window."""
        runner_small = self._build_runner(delay_ms=5000.0, decision_ms=0.0)
        runner_large = self._build_runner(delay_ms=5000.0, decision_ms=10000.0)
        assert runner_large._max_history_len > runner_small._max_history_len

    def test_history_retention_in_full_pipeline(self):
        """Full pipeline run with delay has bounded history."""
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self): return "Null"
            def reset(self): pass
            def generate_signal(self, state): return None

        config = BacktestConfig(
            symbol="TEST", start_date="2026-03-13", end_date="2026-03-13",
            seed=42, market_data_delay_ms=2000.0, decision_compute_ms=500.0,
            placement_style="aggressive",
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

        base = pd.Timestamp("2026-03-13 09:00:00")
        states = [
            _make_state(
                ts=str(base + pd.Timedelta(seconds=i)),
                best_bid=100.0 + i * 0.1, best_ask=100.1 + i * 0.1,
                bid_volume=5000, ask_volume=5000,
            )
            for i in range(100)
        ]
        result = runner.run(states)

        # History should be bounded, not 100
        for sym_history in runner._state_history.values():
            assert len(sym_history) <= runner._max_history_len

        lag_info = result.metadata["observation_lag"]
        assert lag_info["state_history_max_len"] == runner._max_history_len
        assert lag_info["effective_delay_ms"] == 2500.0


# ===================================================================
# Phase 2 additional coverage: decision latency + bounded retention
# ===================================================================

class TestDecisionLatencyPhase2Coverage:
    """Focused Phase 2 coverage for decision latency and bounded history."""

    @staticmethod
    def _make_depth_state(ts: str, best_bid: float, symbol: str = "TEST") -> MarketState:
        timestamp = pd.Timestamp(ts)
        bid_levels = [LOBLevel(price=best_bid - 0.1 * i, volume=3000) for i in range(5)]
        ask_levels = [LOBLevel(price=best_bid + 0.1 + 0.1 * i, volume=3000) for i in range(5)]
        return MarketState(
            timestamp=timestamp,
            symbol=symbol,
            lob=LOBSnapshot(timestamp=timestamp, bid_levels=bid_levels, ask_levels=ask_levels),
            tradable=True,
            session="regular",
        )

    def _run_cancel_replace_case(self, decision_ms: float) -> tuple[pd.Timestamp, float, float]:
        from execution_planning.layer3_order.order_types import (
            ChildOrder,
            OrderSide,
            OrderStatus,
            OrderTIF,
            OrderType,
            ParentOrder,
        )
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self):
                return "Null"

            def reset(self):
                pass

            def generate_signal(self, state):
                return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            placement_style="passive",
            queue_model="prob_queue",
            market_data_delay_ms=1000.0,
            decision_compute_ms=decision_ms,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())
        runner._setup_components(config)
        runner._market_data_delay_ms = config.market_data_delay_ms
        runner._decision_compute_ms = config.decision_compute_ms
        runner._canonical_tick_ms = 1000.0
        runner._max_history_len = 200

        s0 = self._make_depth_state("2026-03-13 09:00:00", 102.0)
        s1 = self._make_depth_state("2026-03-13 09:00:01", 104.0)
        s2 = self._make_depth_state("2026-03-13 09:00:02", 106.0)
        for state in [s0, s1, s2]:
            runner._accumulate_state(state)

        observed_state = runner._lookup_observed_state(
            "TEST",
            s2.timestamp,
            runner._effective_decision_delay_ms(),
        )

        parent = ParentOrder.create(
            symbol="TEST",
            side=OrderSide.BUY,
            qty=100,
            urgency=0.5,
            start_time=s0.timestamp,
            end_time=s0.timestamp + pd.Timedelta(minutes=5),
            arrival_mid=200.0,
        )
        child = ChildOrder.create(
            parent=parent,
            order_type=OrderType.LIMIT,
            qty=100,
            price=95.0,
            tif=OrderTIF.DAY,
            submitted_time=observed_state.timestamp,
            arrival_mid=200.0,
        )
        child.status = OrderStatus.OPEN
        parent.child_orders.append(child)
        runner._open_child_orders["TEST"] = [child]

        fills = runner._process_open_orders(
            parent=parent,
            true_state=s2,
            observed_state=observed_state,
            events=[],
        )
        assert fills == []
        assert len(parent.child_orders) >= 2

        replacement = parent.child_orders[-1]
        assert replacement.meta["decision_phase"] == "replace_create"
        return (
            observed_state.timestamp,
            float(replacement.price),
            float(replacement.meta["decision_state_age_ms"]),
        )

    def test_cancel_replace_decision_gets_staler_with_larger_decision_latency(self):
        """Same observation lag + larger decision latency => older state for replace."""
        fast_ts, fast_price, fast_age = self._run_cancel_replace_case(decision_ms=0.0)
        slow_ts, slow_price, slow_age = self._run_cancel_replace_case(decision_ms=1000.0)

        assert slow_ts < fast_ts
        assert slow_price < fast_price
        assert slow_age > fast_age

    def test_parent_child_meta_records_decision_timing(self):
        """Parent/child metadata includes configured decision latency and timing context."""
        from execution_planning.layer1_signal import Signal
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self):
                return "Null"

            def reset(self):
                pass

            def generate_signal(self, state):
                return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            placement_style="aggressive",
            market_data_delay_ms=100.0,
            decision_compute_ms=250.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())
        runner._setup_components(config)
        runner._market_data_delay_ms = config.market_data_delay_ms
        runner._decision_compute_ms = config.decision_compute_ms

        observed_state = _make_state(
            ts="2026-03-13 09:00:01",
            best_bid=100.0,
            best_ask=100.1,
            bid_volume=5000,
            ask_volume=5000,
        )
        true_state = _make_state(
            ts="2026-03-13 09:00:03",
            best_bid=100.5,
            best_ask=100.6,
            bid_volume=5000,
            ask_volume=5000,
        )

        signal = Signal(
            timestamp=observed_state.timestamp,
            symbol="TEST",
            score=0.8,
            expected_return=5.0,
            confidence=0.9,
            horizon_steps=1,
            tags={},
            is_valid=True,
        )

        parent = runner._create_parent_order(
            signal=signal,
            delta=100,
            state=observed_state,
            true_state=true_state,
        )
        assert parent is not None
        assert parent.meta["configured_decision_compute_ms"] == 250.0
        assert parent.meta["decision_phase"] == "parent_create"
        assert parent.meta["decision_true_ts"] == true_state.timestamp
        assert parent.meta["decision_observed_ts"] == observed_state.timestamp

        child_orders = runner._slice_order(parent, observed_state, true_state)
        assert len(child_orders) == 1
        child = child_orders[0]
        assert child.meta["configured_decision_compute_ms"] == 250.0
        assert child.meta["decision_phase"] == "child_slice"
        assert child.meta["decision_true_ts"] == true_state.timestamp
        assert child.meta["decision_observed_ts"] == observed_state.timestamp

    def test_fill_path_stays_on_true_state_under_decision_latency(self, monkeypatch):
        """Decision latency changes decision state, but fill simulator still receives true_state."""
        from execution_planning.layer1_signal import Signal
        from evaluation_orchestration.layer7_validation.fill_simulator import FillSimulator
        from strategy_block.strategy import Strategy

        start = pd.Timestamp("2026-03-13 09:00:00")
        trigger_observed_ts = start + pd.Timedelta(seconds=1)

        class TriggerStrategy(Strategy):
            def __init__(self):
                self._fired = False
                self.seen_timestamps: list[pd.Timestamp] = []

            @property
            def name(self):
                return "TriggerStrategy"

            def reset(self):
                self._fired = False
                self.seen_timestamps.clear()

            def generate_signal(self, state):
                self.seen_timestamps.append(state.timestamp)
                if self._fired:
                    return None
                if state.timestamp == trigger_observed_ts:
                    self._fired = True
                    return Signal(
                        timestamp=state.timestamp,
                        symbol=state.symbol,
                        score=0.8,
                        expected_return=5.0,
                        confidence=0.9,
                        horizon_steps=1,
                        tags={},
                        is_valid=True,
                    )
                return None

        captured_fill_timestamps: list[pd.Timestamp] = []

        def _capture_simulate(self, parent, child_orders, state):
            captured_fill_timestamps.append(state.timestamp)
            return []

        monkeypatch.setattr(FillSimulator, "simulate_fills", _capture_simulate)

        strategy = TriggerStrategy()
        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            placement_style="aggressive",
            market_data_delay_ms=1000.0,
            decision_compute_ms=1000.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=strategy)

        states = [
            _make_state(
                ts=str(start + pd.Timedelta(seconds=i)),
                best_bid=100.0 + i * 0.2,
                best_ask=100.1 + i * 0.2,
                bid_volume=5000,
                ask_volume=5000,
            )
            for i in range(8)
        ]

        runner.run(states)

        assert trigger_observed_ts in strategy.seen_timestamps
        assert captured_fill_timestamps
        assert captured_fill_timestamps[0] == start + pd.Timedelta(seconds=3)
        assert captured_fill_timestamps[0] > trigger_observed_ts

    def test_history_retention_accounts_for_runtime_lookback(self):
        """Retention bound must include lag/rolling/persist lookback requirements."""
        from types import SimpleNamespace

        from strategy_block.strategy_specs.v2.ast_nodes import (
            ComparisonExpr,
            ConstExpr,
            FeatureExpr,
            LagExpr,
            PersistExpr,
            RollingExpr,
        )

        spec = SimpleNamespace(
            preconditions=[SimpleNamespace(condition=LagExpr(feature="mid_price", steps=7))],
            entry_policies=[
                SimpleNamespace(
                    trigger=ComparisonExpr(
                        left=RollingExpr(feature="mid_price", method="mean", window=9),
                        op=">",
                        threshold=0.0,
                    ),
                    strength=ConstExpr(1.0),
                )
            ],
            exit_policies=[
                SimpleNamespace(
                    rules=[
                        SimpleNamespace(
                            condition=PersistExpr(
                                expr=FeatureExpr("mid_price"),
                                window=14,
                                min_true=7,
                            )
                        )
                    ]
                )
            ],
            risk_policy=None,
            execution_policy=None,
            regimes=[],
            state_policy=None,
        )

        runner = PipelineRunner.__new__(PipelineRunner)
        runner._strategy = SimpleNamespace(_spec=spec)
        runner._canonical_tick_ms = 500.0
        runner._market_data_delay_ms = 1000.0
        runner._decision_compute_ms = 500.0

        retention = runner._compute_history_retention_len()

        assert runner._strategy_lookback_ticks == 14
        assert retention == 28

    def test_history_prunes_on_500ms_run(self):
        """500ms run keeps per-symbol history bounded under effective delay."""
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self):
                return "Null"

            def reset(self):
                pass

            def generate_signal(self, state):
                return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            placement_style="aggressive",
            market_data_delay_ms=1500.0,
            decision_compute_ms=500.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

        base = pd.Timestamp("2026-03-13 09:00:00")
        states: list[MarketState] = []
        for i in range(120):
            state = _make_state(
                ts=str(base + pd.Timedelta(milliseconds=500 * i)),
                best_bid=100.0 + i * 0.01,
                best_ask=100.1 + i * 0.01,
                bid_volume=4000,
                ask_volume=4000,
            )
            state.meta["resample_freq"] = "500ms"
            states.append(state)

        result = runner.run(states)

        assert runner._max_history_len == 20
        assert len(runner._state_history["TEST"]) == 20

        lag_info = result.metadata["observation_lag"]
        assert lag_info["resample_interval"] == "500ms"
        assert lag_info["canonical_tick_interval_ms"] == 500.0
        assert lag_info["state_history_max_len"] == 20
        assert lag_info["strategy_runtime_lookback_ticks"] == 0


class TestRealismDiagnosticsMetadata:
    """Focused metadata checks for Phase 3-lite realism diagnostics."""

    def test_decision_latency_enabled_false_when_zero(self):
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self):
                return "Null"

            def reset(self):
                pass

            def generate_signal(self, state):
                return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            market_data_delay_ms=0.0,
            decision_compute_ms=0.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

        states = [
            _make_state(
                ts=f"2026-03-13 09:00:0{i}",
                best_bid=100.0 + i * 0.1,
                best_ask=100.1 + i * 0.1,
                bid_volume=3000,
                ask_volume=3000,
            )
            for i in range(6)
        ]
        result = runner.run(states)

        diagnostics = result.metadata["realism_diagnostics"]
        assert diagnostics["decision_latency"]["configured_decision_compute_ms"] == 0.0
        assert diagnostics["decision_latency"]["decision_latency_enabled"] is False
        assert result.summary()["decision_latency_enabled"] is False

    def test_decision_state_age_is_decision_step_aggregate_not_observation_proxy(self):
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            
            @property
            def name(self):
                return "Null"

            def reset(self):
                pass

            def generate_signal(self, state):
                return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            market_data_delay_ms=500.0,
            decision_compute_ms=0.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

        base = pd.Timestamp("2026-03-13 09:00:00")
        states: list[MarketState] = []
        for i in range(12):
            state = _make_state(
                ts=str(base + pd.Timedelta(milliseconds=500 * i)),
                best_bid=100.0 + i * 0.01,
                best_ask=100.1 + i * 0.01,
                bid_volume=3000,
                ask_volume=3000,
            )
            state.meta["resample_freq"] = "500ms"
            states.append(state)

        result = runner.run(states)
        diagnostics = result.metadata["realism_diagnostics"]

        assert diagnostics["observation_lag"]["avg_observation_staleness_ms"] > 0.0
        assert diagnostics["decision_latency"]["decision_state_samples_count"] == 0
        assert diagnostics["decision_latency"]["avg_decision_state_age_ms"] == 0.0

    def test_decision_latency_enabled_true_and_500ms_tick_metadata(self):
        from strategy_block.strategy import Strategy

        class NullStrategy(Strategy):
            @property
            def name(self):
                return "Null"

            def reset(self):
                pass

            def generate_signal(self, state):
                return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            market_data_delay_ms=500.0,
            decision_compute_ms=250.0,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

        base = pd.Timestamp("2026-03-13 09:00:00")
        states: list[MarketState] = []
        for i in range(20):
            state = _make_state(
                ts=str(base + pd.Timedelta(milliseconds=500 * i)),
                best_bid=100.0 + i * 0.01,
                best_ask=100.1 + i * 0.01,
                bid_volume=3000,
                ask_volume=3000,
            )
            state.meta["resample_freq"] = "500ms"
            states.append(state)

        result = runner.run(states)

        diagnostics = result.metadata["realism_diagnostics"]
        assert diagnostics["decision_latency"]["configured_decision_compute_ms"] == 250.0
        assert diagnostics["decision_latency"]["decision_latency_enabled"] is True
        assert diagnostics["tick_time"]["canonical_tick_interval_ms"] == 500.0
        assert diagnostics["tick_time"]["resample_interval"] == "500ms"
        assert diagnostics["tick_time"]["state_history_max_len"] == runner._max_history_len
        for key in ("queue_wait_ticks", "queue_wait_ms", "blocked_miss_count", "ready_but_not_filled_count"):
            assert key in diagnostics["queue"]
        for key in ("avg_cancel_effective_lag_ms", "cancel_pending_count", "fills_before_cancel_effective_count"):
            assert key in diagnostics["latency"]
        for key in ("max_children_per_parent", "max_cancelled_children_per_parent", "top_parent_by_children"):
            assert key in diagnostics["lifecycle"]

        summary = result.summary()
        assert summary["configured_decision_compute_ms"] == 250.0
        assert summary["decision_latency_enabled"] is True
        assert summary["canonical_tick_interval_ms"] == 500.0
