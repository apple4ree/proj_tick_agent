from __future__ import annotations

import pandas as pd

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from evaluation_orchestration.layer7_validation.backtest_config import LatencyConfig
from evaluation_orchestration.layer7_validation.fill_simulator import FillSimulator
from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger
from market_simulation.layer5_simulator.bookkeeper import Bookkeeper
from market_simulation.layer5_simulator.fee_model import ZeroFeeModel
from market_simulation.layer5_simulator.latency_model import LatencyModel, LatencyProfile
from market_simulation.layer5_simulator.matching_engine import ExchangeModel, MatchingEngine
from market_simulation.layer5_simulator.order_book import OrderBookSimulator
from execution_planning.layer3_order.order_types import ChildOrder, OrderSide, OrderStatus, OrderTIF, OrderType, ParentOrder


def _make_state(ts: pd.Timestamp, symbol: str = "TEST", bid: float = 100.0, ask: float = 100.1) -> MarketState:
    return MarketState(
        timestamp=ts,
        symbol=symbol,
        lob=LOBSnapshot(
            timestamp=ts,
            bid_levels=[LOBLevel(price=bid, volume=5000)],
            ask_levels=[LOBLevel(price=ask, volume=5000)],
        ),
    )


def _build_fill_simulator(profile: LatencyProfile) -> FillSimulator:
    return FillSimulator(
        matching_engine=MatchingEngine(exchange_model=ExchangeModel.PARTIAL_FILL),
        order_book=OrderBookSimulator(),
        latency_model=LatencyModel(profile=profile, add_jitter=False),
        fee_model=ZeroFeeModel(),
        bookkeeper=Bookkeeper(initial_cash=1e8),
        pnl_ledger=PnLLedger(),
        queue_model="none",
        queue_position_assumption=0.5,
        rng_seed=42,
    )


def _make_parent_child(order_type: OrderType = OrderType.MARKET) -> tuple[ParentOrder, ChildOrder]:
    parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=10)
    child = ChildOrder.create(
        parent=parent,
        order_type=order_type,
        qty=10,
        price=None if order_type == OrderType.MARKET else 100.0,
        tif=OrderTIF.IOC if order_type == OrderType.MARKET else OrderTIF.DAY,
        submitted_time=pd.Timestamp("2026-03-13 09:00:00"),
        arrival_mid=100.05,
    )
    parent.child_orders.append(child)
    return parent, child


def test_submit_latency_blocks_fill_before_arrival() -> None:
    sim = _build_fill_simulator(
        LatencyProfile(order_submit_ms=1000.0, order_ack_ms=0.0, cancel_ms=0.0, market_data_delay_ms=0.0)
    )
    parent, child = _make_parent_child(order_type=OrderType.MARKET)
    t0 = pd.Timestamp("2026-03-13 09:00:00")
    sim.register_submit_request(child, t0)

    fills_before = sim.simulate_fills(parent, [child], _make_state(t0))
    assert fills_before == []
    assert child.status == OrderStatus.PENDING

    fills_after = sim.simulate_fills(parent, [child], _make_state(t0 + pd.Timedelta(milliseconds=1000)))
    assert len(fills_after) == 1


def test_ack_latency_does_not_gate_fill() -> None:
    sim = _build_fill_simulator(
        LatencyProfile(order_submit_ms=200.0, order_ack_ms=10000.0, cancel_ms=0.0, market_data_delay_ms=0.0)
    )
    parent, child = _make_parent_child(order_type=OrderType.MARKET)
    t0 = pd.Timestamp("2026-03-13 09:00:00")
    sim.register_submit_request(child, t0)

    fills = sim.simulate_fills(parent, [child], _make_state(t0 + pd.Timedelta(milliseconds=200)))
    assert len(fills) == 1


def test_cancel_latency_keeps_order_live_until_effective() -> None:
    sim = _build_fill_simulator(
        LatencyProfile(order_submit_ms=0.0, order_ack_ms=0.0, cancel_ms=1000.0, market_data_delay_ms=0.0)
    )
    parent, child = _make_parent_child(order_type=OrderType.LIMIT)
    child.status = OrderStatus.OPEN
    t0 = pd.Timestamp("2026-03-13 09:00:00")

    sim.register_cancel_request(child, t0, reason="timeout")
    assert child.status == OrderStatus.OPEN

    assert sim.finalize_cancel_if_due(child, t0 + pd.Timedelta(milliseconds=999)) is False
    assert child.status == OrderStatus.OPEN

    assert sim.finalize_cancel_if_due(child, t0 + pd.Timedelta(milliseconds=1000)) is True
    assert child.status == OrderStatus.CANCELLED


def test_fill_can_happen_before_cancel_effective() -> None:
    sim = _build_fill_simulator(
        LatencyProfile(order_submit_ms=0.0, order_ack_ms=0.0, cancel_ms=1000.0, market_data_delay_ms=0.0)
    )
    parent, child = _make_parent_child(order_type=OrderType.MARKET)
    t0 = pd.Timestamp("2026-03-13 09:00:00")

    sim.register_submit_request(child, t0)
    sim.register_cancel_request(child, t0, reason="timeout")

    fills = sim.simulate_fills(parent, [child], _make_state(t0 + pd.Timedelta(milliseconds=500)))
    assert len(fills) == 1

    latency_diag = sim.latency_diagnostics()
    assert latency_diag["fills_before_cancel_effective_count"] >= 1


def test_pipeline_exposes_latency_diagnostics_and_summary_fields() -> None:
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
        latency_ms=100.0,
        market_data_delay_ms=50.0,
        decision_compute_ms=25.0,
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

    states = [
        _make_state(pd.Timestamp("2026-03-13 09:00:00") + pd.Timedelta(seconds=i))
        for i in range(6)
    ]

    result = runner.run(states)
    diagnostics = result.metadata["realism_diagnostics"]
    assert "latency" in diagnostics
    assert diagnostics["latency"]["configured_order_submit_ms"] == 30.0
    assert diagnostics["latency"]["configured_order_ack_ms"] == 70.0
    assert diagnostics["latency"]["configured_cancel_ms"] == 20.0
    assert diagnostics["latency"]["latency_alias_applied"] is True

    summary = result.summary()
    assert summary["configured_order_submit_ms"] == 30.0
    assert summary["configured_cancel_ms"] == 20.0
    assert summary["latency_alias_applied"] is True

def test_pipeline_reports_alias_disabled_for_explicit_nested_profile() -> None:
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
        latency_ms=100.0,
        latency=LatencyConfig(profile="retail"),
        market_data_delay_ms=50.0,
        decision_compute_ms=25.0,
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())

    states = [
        _make_state(pd.Timestamp("2026-03-13 09:00:00") + pd.Timedelta(seconds=i))
        for i in range(4)
    ]

    result = runner.run(states)
    diagnostics = result.metadata["realism_diagnostics"]
    assert diagnostics["latency"]["latency_alias_applied"] is False
    # Retail profile defaults (no flat alias backfill)
    assert diagnostics["latency"]["configured_order_submit_ms"] == 5.0
    assert diagnostics["latency"]["configured_order_ack_ms"] == 15.0
    assert diagnostics["latency"]["configured_cancel_ms"] == 3.0

    summary = result.summary()
    assert summary["latency_alias_applied"] is False


def test_replace_uses_immediate_cancel_exception_and_new_submit_lifecycle() -> None:
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
        latency_ms=100.0,
        market_data_delay_ms=0.0,
        decision_compute_ms=0.0,
        placement_style="passive",
        queue_model="prob_queue",
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=NullStrategy())
    runner._setup_components(config)

    observed_state = _make_state(pd.Timestamp("2026-03-13 09:00:01"), bid=100.0, ask=100.1)
    true_state = _make_state(pd.Timestamp("2026-03-13 09:00:02"), bid=100.2, ask=100.3)

    parent = ParentOrder.create(
        symbol="TEST",
        side=OrderSide.BUY,
        qty=10,
        arrival_mid=observed_state.lob.mid_price,
        start_time=observed_state.timestamp,
        end_time=observed_state.timestamp + pd.Timedelta(minutes=5),
    )
    child = ChildOrder.create(
        parent=parent,
        order_type=OrderType.LIMIT,
        qty=10,
        price=99.9,
        tif=OrderTIF.DAY,
        submitted_time=observed_state.timestamp,
        arrival_mid=observed_state.lob.mid_price,
    )
    child.status = OrderStatus.OPEN
    child.meta["reprice_count"] = 0
    parent.child_orders.append(child)

    replacement = runner._replace_child_order(
        parent=parent,
        child=child,
        true_state=true_state,
        observed_state=observed_state,
        new_price=100.0,
        reason="stale_price",
    )

    assert replacement is not None
    # Intentional minimal exception: replace uses immediate cancel on old child.
    assert child.status == OrderStatus.CANCELLED
    assert child.meta["cancel_reason"].startswith("replace:")

    # New replacement child starts a fresh submit lifecycle.
    assert replacement.meta["replaces"] == child.child_id
    assert replacement.meta.get("submit_request_time") == true_state.timestamp
    assert replacement.meta.get("venue_arrival_time") is not None
