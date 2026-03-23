from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from execution_planning.layer3_order.order_types import ChildOrder, OrderSide, OrderStatus, OrderTIF, OrderType, ParentOrder
from market_simulation.layer5_simulator import ExchangeModel, MatchingEngine, OrderBookSimulator, QueueModel
from market_simulation.layer5_simulator.bookkeeper import FillEvent
from evaluation_orchestration.layer6_evaluator.execution_metrics import ExecutionMetrics


def _make_state() -> MarketState:
    ts = pd.Timestamp("2026-03-12 09:00:00")
    return MarketState(
        timestamp=ts,
        symbol="TEST",
        lob=LOBSnapshot(
            timestamp=ts,
            bid_levels=[LOBLevel(price=100.0, volume=100)],
            ask_levels=[LOBLevel(price=100.1, volume=20), LOBLevel(price=100.2, volume=20)],
            last_trade_price=100.0,
            last_trade_volume=40,
        ),
        trades=pd.DataFrame(
            {
                "timestamp": [ts, ts],
                "price": [100.0, 100.0],
                "volume": [20, 20],
            }
        ),
    )


def test_matching_engine_prob_queue_allows_partial_touch_fill():
    state = _make_state()
    book = OrderBookSimulator()
    book.update(state.lob)
    parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=50, arrival_mid=state.mid)
    child = ChildOrder.create(
        parent=parent,
        order_type=OrderType.LIMIT,
        qty=50,
        price=100.0,
        tif=OrderTIF.GTC,
        submitted_time=state.timestamp,
    )

    risk_adverse = MatchingEngine(
        exchange_model=ExchangeModel.PARTIAL_FILL,
        queue_model=QueueModel.RISK_ADVERSE,
        queue_position_assumption=0.5,
    )
    prob_queue = MatchingEngine(
        exchange_model=ExchangeModel.PARTIAL_FILL,
        queue_model=QueueModel.PROB_QUEUE,
        queue_position_assumption=0.5,
    )

    risk_fill_qty, _ = risk_adverse.match(child, book, state)
    prob_fill_qty, prob_fill_price = prob_queue.match(child, book, state)

    assert risk_fill_qty == 0
    assert prob_fill_qty == 30
    assert prob_fill_price == 100.0


def test_matching_engine_no_partial_exchange_fills_marketable_order_in_full():
    state = _make_state()
    book = OrderBookSimulator()
    book.update(state.lob)
    parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=50, arrival_mid=state.mid)
    child = ChildOrder.create(
        parent=parent,
        order_type=OrderType.LIMIT,
        qty=50,
        price=100.2,
        tif=OrderTIF.GTC,
        submitted_time=state.timestamp,
    )

    engine = MatchingEngine(
        exchange_model=ExchangeModel.NO_PARTIAL_FILL,
        queue_model=QueueModel.PROB_QUEUE,
    )
    fill_qty, fill_price = engine.match(child, book, state)

    assert fill_qty == 50
    assert fill_price == 100.1


def test_execution_metrics_include_partial_and_latency_stats():
    ts = pd.Timestamp("2026-03-12 09:00:00")
    parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100, arrival_mid=100.05)
    child_partial = ChildOrder.create(
        parent=parent,
        order_type=OrderType.LIMIT,
        qty=100,
        price=100.0,
        tif=OrderTIF.GTC,
        submitted_time=ts,
    )
    child_partial.status = OrderStatus.PARTIAL
    child_partial.filled_qty = 40
    child_full = ChildOrder.create(
        parent=parent,
        order_type=OrderType.LIMIT,
        qty=60,
        price=100.1,
        tif=OrderTIF.IOC,
        submitted_time=ts,
    )
    child_full.status = OrderStatus.FILLED
    child_full.filled_qty = 60
    parent.child_orders = [child_partial, child_full]

    fills = [
        FillEvent(
            timestamp=ts,
            order_id=child_partial.child_id,
            parent_id=parent.order_id,
            symbol="TEST",
            side=OrderSide.BUY,
            filled_qty=40,
            fill_price=100.0,
            fee=1.0,
            slippage_bps=2.0,
            market_impact_bps=0.5,
            latency_ms=1.5,
            is_maker=True,
        ),
        FillEvent(
            timestamp=ts + pd.Timedelta(seconds=1),
            order_id=child_full.child_id,
            parent_id=parent.order_id,
            symbol="TEST",
            side=OrderSide.BUY,
            filled_qty=60,
            fill_price=100.1,
            fee=2.0,
            slippage_bps=4.0,
            market_impact_bps=1.0,
            latency_ms=2.5,
            is_maker=False,
        ),
    ]

    states = [
        MarketState(
            timestamp=ts,
            symbol="TEST",
            lob=LOBSnapshot(
                timestamp=ts,
                bid_levels=[LOBLevel(price=100.0, volume=100)],
                ask_levels=[LOBLevel(price=100.1, volume=100)],
            ),
        ),
        MarketState(
            timestamp=ts + pd.Timedelta(seconds=1),
            symbol="TEST",
            lob=LOBSnapshot(
                timestamp=ts + pd.Timedelta(seconds=1),
                bid_levels=[LOBLevel(price=100.0, volume=100)],
                ask_levels=[LOBLevel(price=100.1, volume=100)],
            ),
        ),
    ]

    report = ExecutionMetrics.compute(fills=fills, parent_orders=[parent], market_states=states)

    assert report.partial_fill_rate == 0.5
    assert report.maker_fill_ratio == 0.4
    assert report.avg_latency_ms == 2.0
    assert report.p95_latency_ms >= 2.0
