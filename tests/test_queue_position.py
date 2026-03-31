from __future__ import annotations

import pandas as pd

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from evaluation_orchestration.layer6_evaluator.pnl_ledger import PnLLedger
from evaluation_orchestration.layer7_validation.fill_simulator import FillSimulator
from execution_planning.layer3_order.order_types import ChildOrder, OrderSide, OrderTIF, OrderType, ParentOrder
from market_simulation.layer5_simulator.bookkeeper import Bookkeeper
from market_simulation.layer5_simulator.fee_model import ZeroFeeModel
from market_simulation.layer5_simulator.latency_model import LatencyModel, LatencyProfile
from market_simulation.layer5_simulator.matching_engine import ExchangeModel, MatchingEngine, QueueModel
from market_simulation.layer5_simulator.order_book import OrderBookSimulator


def _make_state(
    *,
    ts: str,
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
        symbol="TEST",
        lob=LOBSnapshot(
            timestamp=pd.Timestamp(ts),
            bid_levels=[LOBLevel(price=best_bid, volume=bid_volume)],
            ask_levels=[LOBLevel(price=best_ask, volume=ask_volume)],
            last_trade_price=trade_price,
            last_trade_volume=trade_volume if trade_price is not None else None,
        ),
        trades=trades,
    )


def _build_fill_simulator(
    queue_model_name: str,
    queue_position_assumption: float = 0.5,
    rng_seed: int = 7,
) -> FillSimulator:
    queue_map = {
        "none": QueueModel.NONE,
        "price_time": QueueModel.PRICE_TIME,
        "risk_adverse": QueueModel.RISK_ADVERSE,
        "prob_queue": QueueModel.PROB_QUEUE,
        "pro_rata": QueueModel.PRO_RATA,
        "random": QueueModel.RANDOM,
    }
    return FillSimulator(
        matching_engine=MatchingEngine(
            exchange_model=ExchangeModel.PARTIAL_FILL,
            queue_model=queue_map[queue_model_name],
            queue_position_assumption=queue_position_assumption,
            rng_seed=rng_seed,
        ),
        order_book=OrderBookSimulator(),
        latency_model=LatencyModel(profile=LatencyProfile.zero(), add_jitter=False),
        fee_model=ZeroFeeModel(),
        bookkeeper=Bookkeeper(initial_cash=1e8),
        pnl_ledger=PnLLedger(),
        queue_model=queue_model_name,
        queue_position_assumption=queue_position_assumption,
        rng_seed=rng_seed,
    )


def _make_parent_child(*, qty: int = 100, side: OrderSide = OrderSide.BUY, price: float = 100.0, tif: OrderTIF = OrderTIF.DAY) -> tuple[ParentOrder, ChildOrder]:
    parent = ParentOrder.create(symbol="TEST", side=side, qty=qty)
    child = ChildOrder.create(parent=parent, order_type=OrderType.LIMIT, qty=qty, price=price, tif=tif)
    child.meta["placement_policy"] = "PassivePlacement"
    parent.child_orders.append(child)
    return parent, child


def test_queue_initialization_passive_buy_uses_best_bid_qty():
    sim = _build_fill_simulator("risk_adverse")
    state = _make_state(ts="2026-03-13 09:00:00", bid_volume=3200, trade_price=None)
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    fills = sim.simulate_fills(parent, [child], state)

    assert fills == []
    assert child.queue_initialized is True
    assert child.initial_level_qty == 3200.0
    assert child.queue_ahead_qty == 3200.0


def test_risk_adverse_no_fill_before_queue_consumed():
    sim = _build_fill_simulator("risk_adverse")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=1000, trade_price=100.0, trade_volume=300)
    fills = sim.simulate_fills(parent, [child], state1)

    assert fills == []
    assert child.queue_ahead_qty == 700.0


def test_risk_adverse_fill_after_sufficient_same_level_trade():
    sim = _build_fill_simulator("risk_adverse")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=1000, trade_price=100.0, trade_volume=1200)
    fills = sim.simulate_fills(parent, [child], state1)

    assert len(fills) == 1
    assert fills[0].filled_qty == 100
    assert child.queue_ahead_qty == 0.0


def test_prob_queue_reflects_partial_depth_decrease():
    sim = _build_fill_simulator("prob_queue", queue_position_assumption=0.25)
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=600)
    fills = sim.simulate_fills(parent, [child], state1)

    assert fills == []
    assert child.queue_ahead_qty == 700.0


def test_aggressive_marketable_child_bypasses_queue_gate():
    sim = _build_fill_simulator("risk_adverse")
    parent = ParentOrder.create(symbol="TEST", side=OrderSide.BUY, qty=100)
    child = ChildOrder.create(
        parent=parent,
        order_type=OrderType.LIMIT,
        qty=100,
        price=100.1,
        tif=OrderTIF.IOC,
    )
    child.meta["placement_policy"] = "AggressivePlacement"
    parent.child_orders.append(child)

    state = _make_state(ts="2026-03-13 09:00:00", best_bid=100.0, best_ask=100.1, ask_volume=1000)
    fills = sim.simulate_fills(parent, [child], state)

    assert len(fills) == 1
    assert child.queue_initialized is False


def test_queue_model_on_off_comparison_is_more_conservative_for_passive_fill():
    state = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000, trade_price=100.0, trade_volume=500)

    parent_none, child_none = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)
    sim_none = _build_fill_simulator("none")
    fills_none = sim_none.simulate_fills(parent_none, [child_none], state)

    parent_ra, child_ra = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)
    sim_ra = _build_fill_simulator("risk_adverse")
    fills_ra = sim_ra.simulate_fills(parent_ra, [child_ra], state)

    assert len(fills_none) == 1
    assert fills_none[0].filled_qty == 100
    assert fills_ra == []


def test_no_double_count_fill_after_queue_consumed():
    """Regression: once FillSimulator queue gate is consumed, MatchingEngine
    must NOT re-apply queue filtering.

    Scenario:
      - bid_volume=500 → queue_ahead initialized to 500
      - Step 1: 500 traded at 100.0 → queue_ahead consumed to 0
      - Step 2: 200 traded at 100.0 → queue_ahead still 0, order should fill

    Before this refactor, MatchingEngine would re-compute queue_ahead from
    resting volume and block the fill even though FillSimulator gate passed.
    """
    sim = _build_fill_simulator("risk_adverse", queue_position_assumption=0.5)
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    # Step 0: initialize queue — queue_ahead = 500
    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=500)
    fills0 = sim.simulate_fills(parent, [child], state0)
    assert fills0 == []
    assert child.queue_ahead_qty == 500.0

    # Step 1: 500 traded → queue consumed
    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=500, trade_price=100.0, trade_volume=500)
    fills1 = sim.simulate_fills(parent, [child], state1)
    assert child.queue_ahead_qty == 0.0
    assert len(fills1) == 1
    assert fills1[0].filled_qty == 100

    # Verify the fill was recorded on the child
    assert child.filled_qty == 100


def test_no_double_count_partial_queue_then_fill():
    """After partial queue burn, the remaining fill reflects FillSimulator
    gate only — no secondary queue filter from MatchingEngine."""
    sim = _build_fill_simulator("risk_adverse", queue_position_assumption=0.5)
    parent, child = _make_parent_child(qty=50, side=OrderSide.BUY, price=100.0)

    # Initialize queue with bid_volume=300
    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=300)
    sim.simulate_fills(parent, [child], state0)
    assert child.queue_ahead_qty == 300.0

    # Partial burn: 200 traded → queue_ahead = 100, no fill yet
    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=300, trade_price=100.0, trade_volume=200)
    fills1 = sim.simulate_fills(parent, [child], state1)
    assert fills1 == []
    assert child.queue_ahead_qty == 100.0

    # Full burn: 150 traded → queue_ahead = 0, fill occurs
    state2 = _make_state(ts="2026-03-13 09:00:02", bid_volume=300, trade_price=100.0, trade_volume=150)
    fills2 = sim.simulate_fills(parent, [child], state2)
    assert child.queue_ahead_qty == 0.0
    assert len(fills2) == 1
    assert fills2[0].filled_qty == 50


def test_matching_engine_standalone_no_queue_filtering():
    """MatchingEngine alone should not apply queue filtering for resting fills."""
    engine = MatchingEngine(
        exchange_model=ExchangeModel.PARTIAL_FILL,
        queue_model=QueueModel.RISK_ADVERSE,
        queue_position_assumption=0.5,
    )
    book = OrderBookSimulator()
    state = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000, trade_price=100.0, trade_volume=200)
    book.update(state.lob)

    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    filled_qty, fill_price = engine.match(child, book, state)

    # Without queue filtering, fill = min(child.qty=100, trade_touch=200) = 100
    assert filled_qty == 100
    assert fill_price == 100.0


# ======================================================================
# price_time model tests
# ======================================================================

def test_price_time_trade_only_advancement():
    """price_time uses trade-only queue advancement, same as risk_adverse."""
    sim = _build_fill_simulator("price_time")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)
    assert child.queue_ahead_qty == 1000.0

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=1000, trade_price=100.0, trade_volume=400)
    fills = sim.simulate_fills(parent, [child], state1)
    assert fills == []
    assert child.queue_ahead_qty == 600.0


def test_price_time_depth_drop_alone_does_not_advance():
    """price_time ignores depth drop — only same-level trades reduce queue."""
    sim = _build_fill_simulator("price_time")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)

    # Depth drops by 400 but no trades → queue should NOT advance
    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=600)
    fills = sim.simulate_fills(parent, [child], state1)
    assert fills == []
    assert child.queue_ahead_qty == 1000.0


def test_price_time_fill_after_queue_consumed():
    """price_time fills after sufficient same-level trade volume."""
    sim = _build_fill_simulator("price_time")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=500)
    sim.simulate_fills(parent, [child], state0)

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=500, trade_price=100.0, trade_volume=600)
    fills = sim.simulate_fills(parent, [child], state1)
    assert len(fills) == 1
    assert fills[0].filled_qty == 100


# ======================================================================
# random model tests
# ======================================================================

def test_random_deterministic_with_seed():
    """random model is deterministic given a fixed seed."""
    results = []
    for _ in range(3):
        sim = _build_fill_simulator("random", rng_seed=42)
        parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

        state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
        sim.simulate_fills(parent, [child], state0)

        # Depth drops by 400 (no trades) → random fraction of depth drop
        state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=600)
        sim.simulate_fills(parent, [child], state1)
        results.append(child.queue_ahead_qty)

    # All three runs with same seed should produce identical results
    assert results[0] == results[1] == results[2]


def test_random_depth_drop_partially_advances_queue():
    """random model stochastically advances queue on depth drop."""
    sim = _build_fill_simulator("random", rng_seed=42)
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)
    assert child.queue_ahead_qty == 1000.0

    # Depth drops by 400 with no trades → stochastic fraction advances queue
    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=600)
    sim.simulate_fills(parent, [child], state1)

    # Queue should advance by some fraction of 400 (but not the full 400)
    # With seed=42, we expect some advancement
    assert child.queue_ahead_qty < 1000.0
    # Conservative check: still some queue remaining (400 depth drop, 1000 queue)
    assert child.queue_ahead_qty > 0.0


def test_random_trade_always_advances():
    """random model always advances queue with same-level trades (like all models)."""
    sim = _build_fill_simulator("random", rng_seed=42)
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=500)
    sim.simulate_fills(parent, [child], state0)

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=500, trade_price=100.0, trade_volume=300)
    sim.simulate_fills(parent, [child], state1)

    assert child.queue_ahead_qty == 200.0  # 500 - 300


def test_random_more_aggressive_than_risk_adverse():
    """random model is generally more aggressive than risk_adverse on depth drops."""
    # With depth drops and no trades, risk_adverse gives 0 advancement while
    # random gives some stochastic advancement.
    sim_ra = _build_fill_simulator("risk_adverse")
    parent_ra, child_ra = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)
    sim_rand = _build_fill_simulator("random", rng_seed=42)
    parent_rand, child_rand = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim_ra.simulate_fills(parent_ra, [child_ra], state0)
    sim_rand.simulate_fills(parent_rand, [child_rand], state0)

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=600)
    sim_ra.simulate_fills(parent_ra, [child_ra], state1)
    sim_rand.simulate_fills(parent_rand, [child_rand], state1)

    # risk_adverse: no depth advancement → queue stays at 1000
    assert child_ra.queue_ahead_qty == 1000.0
    # random: some stochastic depth advancement → queue < 1000
    assert child_rand.queue_ahead_qty < child_ra.queue_ahead_qty


# ======================================================================
# pro_rata model tests
# ======================================================================

def test_pro_rata_queue_gate_same_as_risk_adverse():
    """pro_rata uses trade-only queue advancement (risk_adverse-style gate)."""
    sim = _build_fill_simulator("pro_rata")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)
    assert child.queue_ahead_qty == 1000.0

    # Partial trade → queue not yet consumed
    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=1000, trade_price=100.0, trade_volume=300)
    fills = sim.simulate_fills(parent, [child], state1)
    assert fills == []
    assert child.queue_ahead_qty == 700.0


def test_pro_rata_depth_drop_ignored():
    """pro_rata ignores depth drop for queue advancement (conservative gate)."""
    sim = _build_fill_simulator("pro_rata")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000)
    sim.simulate_fills(parent, [child], state0)

    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=500)
    fills = sim.simulate_fills(parent, [child], state1)
    assert fills == []
    assert child.queue_ahead_qty == 1000.0


def test_pro_rata_fill_qty_proportional_to_order_size():
    """After gate passes, pro_rata caps fill qty by size-proportional share.

    Setup: resting_volume=900, child_qty=100, trade=1200
    pro_rata share = 100 / (900 + 100) = 0.10
    pro_rata fillable = int(0.10 * 1200) = 120 → but child.qty=100, so capped to 100
    """
    sim = _build_fill_simulator("pro_rata")
    parent, child = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)

    # Initialize with small queue so it gets consumed quickly
    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=100)
    sim.simulate_fills(parent, [child], state0)

    # Trade volume 200 consumes queue (100) and fills
    # resting_volume=100, child.remaining_qty=100
    # share = 100 / (100 + 100) = 0.5
    # pro_rata_fillable = int(0.5 * 200) = 100
    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=100, trade_price=100.0, trade_volume=200)
    fills = sim.simulate_fills(parent, [child], state1)
    assert len(fills) == 1
    assert fills[0].filled_qty == 100


def test_pro_rata_small_share_reduces_fill():
    """pro_rata with small order vs large resting volume produces smaller fills.

    Setup: resting_volume=900, child_qty=10, trade=1000
    After queue consumed: share = 10 / (900 + 10) ≈ 0.011
    pro_rata fillable = int(0.011 * 1000) = 10 → full fill
    But with trade=100: pro_rata fillable = int(0.011 * 100) = 1
    """
    sim = _build_fill_simulator("pro_rata")
    parent, child = _make_parent_child(qty=10, side=OrderSide.BUY, price=100.0)

    # Small initial queue to consume quickly
    state0 = _make_state(ts="2026-03-13 09:00:00", bid_volume=50)
    sim.simulate_fills(parent, [child], state0)

    # Trade 100 consumes queue (50) and we try to fill.
    # resting_volume=900, child.remaining_qty=10
    # share = 10 / (900 + 10) ≈ 0.011
    # pro_rata_fillable = max(1, int(0.011 * 100)) = 1
    state1 = _make_state(ts="2026-03-13 09:00:01", bid_volume=900, trade_price=100.0, trade_volume=100)
    fills = sim.simulate_fills(parent, [child], state1)
    assert len(fills) == 1
    assert fills[0].filled_qty == 1  # pro-rata capped


def test_pro_rata_more_conservative_than_none():
    """pro_rata produces smaller or equal fills compared to none model."""
    state = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000, trade_price=100.0, trade_volume=500)

    parent_none, child_none = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)
    sim_none = _build_fill_simulator("none")
    fills_none = sim_none.simulate_fills(parent_none, [child_none], state)

    parent_pr, child_pr = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)
    sim_pr = _build_fill_simulator("pro_rata")
    fills_pr = sim_pr.simulate_fills(parent_pr, [child_pr], state)

    assert len(fills_none) == 1
    assert fills_none[0].filled_qty == 100
    # pro_rata: queue gate blocks on first call (queue_ahead=1000, trade=500 → ahead=500)
    assert fills_pr == []


# ======================================================================
# Cross-model comparison tests
# ======================================================================

def test_all_queue_models_more_conservative_than_none():
    """All non-none models produce fewer or equal fills compared to none."""
    state = _make_state(ts="2026-03-13 09:00:00", bid_volume=1000, trade_price=100.0, trade_volume=500)

    parent_none, child_none = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)
    sim_none = _build_fill_simulator("none")
    fills_none = sim_none.simulate_fills(parent_none, [child_none], state)
    assert len(fills_none) == 1

    for model in ("price_time", "risk_adverse", "prob_queue", "random", "pro_rata"):
        parent_m, child_m = _make_parent_child(qty=100, side=OrderSide.BUY, price=100.0)
        sim_m = _build_fill_simulator(model, rng_seed=42)
        fills_m = sim_m.simulate_fills(parent_m, [child_m], state)
        # All should be more conservative than none on first tick
        assert fills_m == [], f"{model} should not fill on first tick with queue_ahead=1000"
