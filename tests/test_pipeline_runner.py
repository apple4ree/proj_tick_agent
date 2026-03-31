from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from execution_planning.layer1_signal import Signal
from execution_planning.layer3_order.order_types import OrderSide, OrderStatus, ParentOrder
from market_simulation.layer5_simulator.micro_events import MicroEventType
from strategy_block.strategy import Strategy
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner


def _make_states(n_steps: int = 12) -> list[MarketState]:
    states: list[MarketState] = []
    start_ts = pd.Timestamp("2026-03-12 09:00:00")

    for step in range(n_steps):
        timestamp = start_ts + pd.Timedelta(seconds=step)
        bid_levels = [
            LOBLevel(price=100.0, volume=5_000),
            LOBLevel(price=99.9, volume=3_000),
        ]
        ask_levels = [
            LOBLevel(price=100.01, volume=1_200),
            LOBLevel(price=100.10, volume=800),
        ]
        snapshot = LOBSnapshot(
            timestamp=timestamp,
            bid_levels=bid_levels,
            ask_levels=ask_levels,
        )
        states.append(
            MarketState(
                timestamp=timestamp,
                symbol="TEST",
                lob=snapshot,
                tradable=True,
                session="regular",
            )
        )

    return states


class DummyBuyStrategy(Strategy):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "DummyBuyStrategy"

    def reset(self) -> None:
        self.calls = 0

    def generate_signal(self, state: MarketState):
        self.calls += 1
        if self.calls > 1:
            return None
        return Signal(
            timestamp=state.timestamp,
            symbol=state.symbol,
            score=0.8,
            expected_return=5.0,
            confidence=0.9,
            horizon_steps=1,
            tags={"strategy": self.name},
            is_valid=True,
        )


def test_pipeline_runner_executes_end_to_end():
    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=123,
        placement_style="aggressive",
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=DummyBuyStrategy())

    result = runner.run(_make_states())
    summary = result.summary()

    assert result.n_fills >= 1
    assert summary["fill_rate"] > 0.0
    assert summary["is_bps"] > 0.0


def _make_state(
    timestamp: pd.Timestamp,
    best_bid: float,
    best_ask: float,
    bid_volumes: list[int],
    ask_volumes: list[int],
    tradable: bool = True,
    session: str = "regular",
) -> MarketState:
    bid_levels = [
        LOBLevel(price=best_bid - 0.1 * idx, volume=bid_volumes[idx])
        for idx in range(len(bid_volumes))
    ]
    ask_levels = [
        LOBLevel(price=best_ask + 0.1 * idx, volume=ask_volumes[idx])
        for idx in range(len(ask_volumes))
    ]
    return MarketState(
        timestamp=timestamp,
        symbol="TEST",
        lob=LOBSnapshot(
            timestamp=timestamp,
            bid_levels=bid_levels,
            ask_levels=ask_levels,
        ),
        tradable=tradable,
        session=session,
    )


def test_pipeline_runner_manages_open_orders_and_halts():
    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=7,
        placement_style="passive",
        queue_model="prob_queue",
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=DummyBuyStrategy())
    runner._setup_components(config)

    start_ts = pd.Timestamp("2026-03-12 09:00:00")
    state0 = _make_state(
        timestamp=start_ts,
        best_bid=10_000.0,
        best_ask=10_005.0,
        bid_volumes=[4_000, 3_000, 2_000, 1_000],
        ask_volumes=[100, 100, 100, 100],
    )
    parent = ParentOrder.create(
        symbol="TEST",
        side=OrderSide.BUY,
        qty=100,
        urgency=0.5,
        start_time=state0.timestamp,
        end_time=state0.timestamp + pd.Timedelta(minutes=5),
        arrival_mid=state0.mid,
    )
    parent = runner._order_constraints.apply_all(parent, state0)

    runner._active_parent_orders["TEST"] = parent
    child_orders = runner._slice_order(parent, state0)
    assert len(child_orders) == 1
    assert runner._simulate_fills(parent, child_orders, state0) == []
    runner._sync_open_children("TEST", parent)
    assert len(runner._open_child_orders["TEST"]) == 1

    state1 = _make_state(
        timestamp=start_ts + pd.Timedelta(seconds=1),
        best_bid=10_000.3,
        best_ask=10_005.3,
        bid_volumes=[4_500, 3_500, 2_500, 1_500],
        ask_volumes=[100, 100, 100, 100],
    )
    fills_after_replace = runner._process_open_orders(
        parent, true_state=state1, observed_state=state1, events=[],
    )
    assert fills_after_replace == []

    if len(parent.child_orders) == 2:
        original_child, replacement_child = parent.child_orders
        assert original_child.status == OrderStatus.CANCELLED
        assert original_child.meta["cancel_reason"].startswith("replace:")
        assert replacement_child.is_active
        assert replacement_child.price == state1.lob.best_bid
    else:
        assert len(parent.child_orders) == 1
        replacement_child = parent.child_orders[0]
        assert replacement_child.is_active

    state2 = _make_state(
        timestamp=start_ts + pd.Timedelta(seconds=2),
        best_bid=10_000.25,
        best_ask=10_005.25,
        bid_volumes=[4_000, 3_000, 2_000, 1_000],
        ask_volumes=[100, 100, 100, 100],
        tradable=False,
        session="halted",
    )
    events = runner._process_micro_events(state1, state2)
    fills_after_halt = runner._process_open_orders(
        parent, true_state=state2, observed_state=state2, events=events,
    )
    assert fills_after_halt == []
    if replacement_child.status == OrderStatus.CANCELLED:
        assert "micro_event_block" in replacement_child.meta["cancel_reason"]
    else:
        assert replacement_child.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.PARTIAL}
        assert replacement_child.meta.get("cancel_pending") is True
        assert replacement_child.meta.get("cancel_request_reason") == "micro_event_block"
        assert replacement_child.meta.get("cancel_effective_time") is not None

        state3 = _make_state(
            timestamp=start_ts + pd.Timedelta(seconds=3),
            best_bid=10_000.2,
            best_ask=10_005.2,
            bid_volumes=[4_000, 3_000, 2_000, 1_000],
            ask_volumes=[100, 100, 100, 100],
            tradable=False,
            session="halted",
        )
        events3 = runner._process_micro_events(state2, state3)
        fills_after_halt2 = runner._process_open_orders(
            parent, true_state=state3, observed_state=state3, events=events3,
        )
        assert fills_after_halt2 == []
        assert replacement_child.status == OrderStatus.CANCELLED
        assert "micro_event_block" in replacement_child.meta["cancel_reason"]

    assert any(event.event_type == MicroEventType.TRADING_HALT for event in events)


def test_pipeline_runner_accepts_strategy_interface():
    strategy = DummyBuyStrategy()
    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=11,
        placement_style="aggressive",
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=strategy)

    result = runner.run(_make_states())

    assert strategy.calls >= 1
    assert result.n_fills >= 1

