"""Tests for limited execution hint consumption in layer4/pipeline."""
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
from execution_planning.layer3_order.order_types import ChildOrder, OrderSide, OrderStatus, OrderType, OrderTIF
from execution_planning.layer4_execution.cancel_replace import CancelReplaceLogic
from execution_planning.layer4_execution.placement_policy import (
    AggressivePlacement,
    PassivePlacement,
    resolve_placement_policy,
)
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from strategy_block.strategy import Strategy


class _NoSignalStrategy(Strategy):
    @property
    def name(self) -> str:
        return "NoSignal"

    def reset(self) -> None:
        return None

    def generate_signal(self, state: MarketState):
        return None


def _make_state(ts: pd.Timestamp, *, best_bid: float = 100.0, best_ask: float = 100.01) -> MarketState:
    return MarketState(
        timestamp=ts,
        symbol="TEST",
        lob=LOBSnapshot(
            timestamp=ts,
            bid_levels=[LOBLevel(price=best_bid, volume=5000), LOBLevel(price=best_bid - 0.1, volume=2000)],
            ask_levels=[LOBLevel(price=best_ask, volume=5000), LOBLevel(price=best_ask + 0.1, volume=2000)],
        ),
        tradable=True,
        session="regular",
    )


def test_placement_mode_tag_overrides_policy_in_slice():
    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=1,
        placement_style="aggressive",
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=_NoSignalStrategy())
    runner._setup_components(config)

    ts = pd.Timestamp("2026-03-12 09:00:00")
    state = _make_state(ts)

    signal = Signal(
        timestamp=ts,
        symbol="TEST",
        score=0.8,
        expected_return=5.0,
        confidence=0.9,
        horizon_steps=1,
        tags={"placement_mode": "passive_only"},
        is_valid=True,
    )
    parent = runner._create_parent_order(signal=signal, delta=100, state=state)
    assert parent is not None

    children = runner._slice_order(parent, state)
    assert len(children) == 1
    child = children[0]
    assert child.tif == OrderTIF.DAY
    assert child.meta.get("placement_policy") == "PassivePlacement"


def test_cancel_after_ticks_override_applies_to_cancel_replace():
    logic = CancelReplaceLogic(timeout_seconds=30.0)
    ts0 = pd.Timestamp("2026-03-12 09:00:00")
    ts1 = ts0 + pd.Timedelta(seconds=3)
    state = _make_state(ts1)

    child = ChildOrder(
        parent_id="p",
        symbol="TEST",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=10,
        price=100.0,
        tif=OrderTIF.DAY,
        status=OrderStatus.OPEN,
        submit_time=ts0,
        submitted_time=ts0,
        arrival_mid=101.0,
        meta={"reprice_count": 0},
    )

    actions = logic.process_open_orders(
        open_orders=[child],
        state=state,
        current_time=ts1,
        cancel_after_ticks=2,
    )
    assert actions[0]["action"] == "cancel"
    assert "timeout" in actions[0]["reason"]


def test_max_reprices_override_applies_to_cancel_replace():
    logic = CancelReplaceLogic(timeout_seconds=30.0, stale_levels=1)
    ts0 = pd.Timestamp("2026-03-12 09:00:00")
    ts1 = ts0 + pd.Timedelta(seconds=1)
    state = _make_state(ts1, best_bid=101.0, best_ask=101.01)

    child = ChildOrder(
        parent_id="p",
        symbol="TEST",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=10,
        price=99.5,
        tif=OrderTIF.DAY,
        status=OrderStatus.OPEN,
        submit_time=ts0,
        submitted_time=ts0,
        arrival_mid=101.0,
        meta={"reprice_count": 1},
    )

    actions = logic.process_open_orders(
        open_orders=[child],
        state=state,
        current_time=ts1,
        max_reprices=1,
    )
    assert actions[0]["action"] == "keep"
    assert actions[0]["reason"] == "max_reprices_reached_keep"



def test_passive_join_short_horizon_keeps_child_alive_past_timeout_checkpoint():
    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=1,
        placement_style="passive",
        latency_ms=100.0,
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=_NoSignalStrategy())
    runner._setup_components(config)

    ts0 = pd.Timestamp("2026-03-12 09:00:00")
    state0 = _make_state(ts0)
    signal = Signal(
        timestamp=ts0,
        symbol="TEST",
        score=0.9,
        expected_return=6.0,
        confidence=0.9,
        horizon_steps=1,
        tags={"placement_mode": "passive_join", "cancel_after_ticks": 1, "max_reprices": 1},
        is_valid=True,
    )

    parent = runner._create_parent_order(signal=signal, delta=100, state=state0)
    assert parent is not None
    assert parent.meta.get("execution_hints", {}).get("cancel_after_ticks") == 3

    children = runner._slice_order(parent, state0)
    assert len(children) == 1
    child = children[0]
    runner._open_child_orders[parent.symbol] = [child]

    state1 = _make_state(ts0 + pd.Timedelta(seconds=1))
    fills = runner._process_open_orders(parent=parent, true_state=state1, observed_state=state1, events=[])

    assert not fills
    assert child.is_active
    assert "cancel_reason" not in child.meta

def test_no_tag_fallback_keeps_default_policy():
    fallback = AggressivePlacement(use_market_orders=False)
    resolved = resolve_placement_policy(fallback, signal_tags={})
    assert resolved is fallback

    resolved2 = resolve_placement_policy(fallback, signal_tags={"placement_mode": "unknown_mode"})
    assert resolved2 is fallback

    passive = resolve_placement_policy(fallback, signal_tags={"placement_mode": "passive_only"})
    assert isinstance(passive, PassivePlacement)
