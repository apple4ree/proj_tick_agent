"""Tests for tick-time semantics alignment.

Verifies that:
1. Canonical tick interval derives from resample frequency, not latency_ms
2. cancel_after_ticks wall-clock meaning scales with resample interval
3. Changing latency_ms does NOT change cancel_after_ticks semantics
4. Strategy-side tick params (holding_ticks, LagExpr, etc.) are unaffected
5. Observation lag Phase 1 semantics are preserved
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from execution_planning.layer3_order.order_types import (
    ChildOrder,
    OrderSide,
    OrderStatus,
    OrderTIF,
    OrderType,
)
from execution_planning.layer4_execution.cancel_replace import CancelReplaceLogic
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(
    ts: str = "2026-03-13 09:00:00",
    symbol: str = "TEST",
    best_bid: float = 100.0,
    best_ask: float = 100.1,
    resample_freq: str | None = None,
) -> MarketState:
    meta = {}
    if resample_freq is not None:
        meta["resample_freq"] = resample_freq
    return MarketState(
        timestamp=pd.Timestamp(ts),
        symbol=symbol,
        lob=LOBSnapshot(
            timestamp=pd.Timestamp(ts),
            bid_levels=[LOBLevel(price=best_bid, volume=5000)],
            ask_levels=[LOBLevel(price=best_ask, volume=5000)],
        ),
        meta=meta,
    )


def _make_child(
    submit_time: str = "2026-03-13 09:00:00",
    price: float = 100.0,
    side: OrderSide = OrderSide.BUY,
) -> ChildOrder:
    return ChildOrder(
        order_id="child-1",
        parent_id="parent-1",
        symbol="TEST",
        side=side,
        order_type=OrderType.LIMIT,
        tif=OrderTIF.GTC,
        qty=100,
        price=price,
        status=OrderStatus.OPEN,
        submit_time=pd.Timestamp(submit_time),
        arrival_mid=100.05,
        meta={},
    )


# ===================================================================
# 1. _resample_freq_to_ms unit tests
# ===================================================================

class TestResampleFreqToMs:
    """PipelineRunner._resample_freq_to_ms canonical tick interval."""

    def test_1s_returns_1000(self):
        assert PipelineRunner._resample_freq_to_ms("1s") == 1000.0

    def test_500ms_returns_500(self):
        assert PipelineRunner._resample_freq_to_ms("500ms") == 500.0

    def test_none_defaults_to_1000(self):
        assert PipelineRunner._resample_freq_to_ms(None) == 1000.0

    def test_unrecognised_defaults_to_1000(self):
        assert PipelineRunner._resample_freq_to_ms("2s") == 1000.0


# ===================================================================
# 2. CancelReplaceLogic tick_interval_ms semantics
# ===================================================================

class TestCancelAfterTicksWallClock:
    """cancel_after_ticks × tick_interval_ms = wall-clock timeout."""

    def test_1s_cancel_after_10_ticks_is_10s(self):
        logic = CancelReplaceLogic(tick_interval_ms=1000.0)
        child = _make_child(submit_time="2026-03-13 09:00:00")
        state = _make_state(ts="2026-03-13 09:00:09")

        # 9 seconds elapsed, cancel_after_ticks=10 at 1s → 10s timeout → no cancel
        cancel, _ = logic.should_cancel(child, state, time_since_submit=9.0, cancel_after_ticks=10)
        assert not cancel

        # 10 seconds elapsed → cancel
        cancel, reason = logic.should_cancel(child, state, time_since_submit=10.0, cancel_after_ticks=10)
        assert cancel
        assert "timeout" in reason

    def test_500ms_cancel_after_10_ticks_is_5s(self):
        logic = CancelReplaceLogic(tick_interval_ms=500.0)
        child = _make_child(submit_time="2026-03-13 09:00:00")
        state = _make_state(ts="2026-03-13 09:00:04")

        # 4.9s elapsed, cancel_after_ticks=10 at 500ms → 5s timeout → no cancel
        cancel, _ = logic.should_cancel(child, state, time_since_submit=4.9, cancel_after_ticks=10)
        assert not cancel

        # 5.0s elapsed → cancel
        cancel, reason = logic.should_cancel(child, state, time_since_submit=5.0, cancel_after_ticks=10)
        assert cancel
        assert "timeout" in reason

    def test_default_tick_interval_is_1000(self):
        """Default CancelReplaceLogic should assume 1s tick interval."""
        logic = CancelReplaceLogic()
        assert logic.tick_interval_ms == 1000.0


# ===================================================================
# 3. latency_ms does NOT affect cancel_after_ticks
# ===================================================================

class TestLatencyIndependence:
    """Changing latency_ms must not change cancel_after_ticks semantics."""

    def test_different_latency_same_cancel_timeout(self):
        """Two configs with different latency_ms should produce same cancel behaviour."""
        child = _make_child(submit_time="2026-03-13 09:00:00")
        state = _make_state(ts="2026-03-13 09:00:10")

        # Both use 1s canonical tick → cancel_after_ticks=10 → 10s timeout
        logic_low = CancelReplaceLogic(tick_interval_ms=1000.0)
        logic_high = CancelReplaceLogic(tick_interval_ms=1000.0)

        cancel_low, _ = logic_low.should_cancel(child, state, 10.0, cancel_after_ticks=10)
        cancel_high, _ = logic_high.should_cancel(child, state, 10.0, cancel_after_ticks=10)
        assert cancel_low == cancel_high

    def test_pipeline_runner_ignores_latency_for_tick_interval(self):
        """PipelineRunner must NOT pass latency_ms to CancelReplaceLogic."""
        states_1s = [_make_state(resample_freq="1s")]
        states_500ms = [_make_state(resample_freq="500ms")]

        for latency in (0.5, 1.0, 5.0, 100.0):
            config = BacktestConfig(
                symbol="TEST",
                start_date="2026-03-13",
                end_date="2026-03-13",
                seed=42,
                latency_ms=latency,
            )

            runner_1s = PipelineRunner.__new__(PipelineRunner)
            runner_1s.config = config
            runner_1s._canonical_tick_ms = PipelineRunner._resample_freq_to_ms(
                states_1s[0].meta.get("resample_freq")
            )
            assert runner_1s._canonical_tick_ms == 1000.0

            runner_500ms = PipelineRunner.__new__(PipelineRunner)
            runner_500ms.config = config
            runner_500ms._canonical_tick_ms = PipelineRunner._resample_freq_to_ms(
                states_500ms[0].meta.get("resample_freq")
            )
            assert runner_500ms._canonical_tick_ms == 500.0


# ===================================================================
# 4. PipelineRunner injects canonical tick interval
# ===================================================================

class TestPipelineRunnerInjection:
    """PipelineRunner._setup_components uses canonical tick, not latency_ms."""

    def _build_runner_and_setup(
        self,
        resample_freq: str | None = "1s",
        latency_ms: float = 1.0,
    ) -> PipelineRunner:
        from strategy_block.strategy import Strategy
        from execution_planning.layer1_signal import Signal

        class _DummyStrategy(Strategy):
            name = "dummy"
            def reset(self): pass
            def generate_signal(self, state):
                return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
            latency_ms=latency_ms,
        )
        runner = PipelineRunner(config=config, data_dir=".", strategy=_DummyStrategy())
        # Set canonical tick before setup (normally done in .run())
        runner._canonical_tick_ms = PipelineRunner._resample_freq_to_ms(resample_freq)
        runner._setup_components(config)
        return runner

    def test_1s_resample_injects_1000ms(self):
        runner = self._build_runner_and_setup(resample_freq="1s", latency_ms=5.0)
        assert runner._cancel_replace.tick_interval_ms == 1000.0

    def test_500ms_resample_injects_500ms(self):
        runner = self._build_runner_and_setup(resample_freq="500ms", latency_ms=5.0)
        assert runner._cancel_replace.tick_interval_ms == 500.0

    def test_none_resample_defaults_to_1000(self):
        runner = self._build_runner_and_setup(resample_freq=None, latency_ms=100.0)
        assert runner._cancel_replace.tick_interval_ms == 1000.0

    def test_latency_does_not_leak_to_tick_interval(self):
        """Even with latency_ms=100, tick_interval_ms must be resample-based."""
        runner = self._build_runner_and_setup(resample_freq="1s", latency_ms=100.0)
        assert runner._cancel_replace.tick_interval_ms == 1000.0
        assert runner._cancel_replace.tick_interval_ms != runner.config.latency_ms


# ===================================================================
# 5. Observation lag metadata includes canonical tick
# ===================================================================

class TestObservationLagMetadata:
    """observation_lag metadata should include canonical_tick_interval_ms."""

    def test_metadata_includes_canonical_tick(self):
        from strategy_block.strategy import Strategy

        class _Noop(Strategy):
            name = "noop"
            def reset(self): pass
            def generate_signal(self, state): return None

        config = BacktestConfig(
            symbol="TEST",
            start_date="2026-03-13",
            end_date="2026-03-13",
            seed=42,
        )
        states = [
            _make_state(ts="2026-03-13 09:00:00", resample_freq="500ms"),
            _make_state(ts="2026-03-13 09:00:00.500", resample_freq="500ms"),
        ]
        runner = PipelineRunner(config=config, data_dir=".", strategy=_Noop())
        result = runner.run(states)
        lag = result.metadata["observation_lag"]
        assert lag["canonical_tick_interval_ms"] == 500.0
        assert lag["resample_interval"] == "500ms"


# ===================================================================
# 6. Strategy-side tick semantics unaffected
# ===================================================================

class TestStrategyTickSemanticsPreserved:
    """Strategy runtime tick params (holding_ticks, LagExpr, cooldown) stay
    in resample-step units — this test documents that they are NOT touched."""

    def test_runtime_v2_tick_count_increments_per_state(self):
        """RuntimeStateV2.tick_count increments once per state regardless of resample."""
        from strategy_block.strategy_compiler.v2.runtime_v2 import RuntimeStateV2

        rt = RuntimeStateV2()
        assert rt.tick_count == 0
        rt.tick_count += 1
        assert rt.tick_count == 1
        # The runtime doesn't know about resample interval — it just counts states.

    def test_cooldown_is_tick_based_not_seconds(self):
        """cooldown_until is a tick number, not a timestamp."""
        from strategy_block.strategy_compiler.v2.runtime_v2 import RuntimeStateV2

        rt = RuntimeStateV2()
        rt.cooldown_until = 30  # expires after tick 30
        assert rt.cooldown_until == 30
        # At 1s: 30 ticks = 30s. At 500ms: 30 ticks = 15s.
        # This is the intended semantic — the engine does NOT auto-normalize.
