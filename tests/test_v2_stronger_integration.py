"""Stronger integration/regression tests for v2 runtime + execution path.

These tests are intentionally heavier than smoke checks:
- they run compiled v2 strategies through PipelineRunner
- they require real fill generation (latency/impact populated)
- they validate regime/state/position_attr/execution-hint interaction
"""
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
from execution_planning.layer4_execution.cancel_replace import CancelReplaceLogic
from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy import Strategy
from strategy_block.strategy_specs.v2.ast_nodes import (
    ComparisonExpr,
    ConstExpr,
    PositionAttrExpr,
    StateVarExpr,
)
from strategy_block.strategy_specs.v2.schema_v2 import (
    EntryConstraints,
    EntryPolicyV2,
    ExecutionAdaptationOverrideV2,
    ExecutionAdaptationRuleV2,
    ExecutionPolicyV2,
    ExitActionV2,
    ExitPolicyV2,
    ExitRuleV2,
    PositionSizingV2,
    PreconditionV2,
    RegimeV2,
    RiskDegradationActionV2,
    RiskDegradationRuleV2,
    RiskPolicyV2,
    StateEventV2,
    StateGuardV2,
    StatePolicyV2,
    StateUpdateV2,
    StrategySpecV2,
)


def _make_state(
    ts: pd.Timestamp,
    *,
    best_bid: float,
    best_ask: float,
    bid_vol: int = 7000,
    ask_vol: int = 2000,
) -> MarketState:
    return MarketState(
        timestamp=ts,
        symbol="TEST",
        lob=LOBSnapshot(
            timestamp=ts,
            bid_levels=[
                LOBLevel(price=best_bid, volume=bid_vol),
                LOBLevel(price=best_bid - 0.1, volume=max(1000, bid_vol // 2)),
            ],
            ask_levels=[
                LOBLevel(price=best_ask, volume=ask_vol),
                LOBLevel(price=best_ask + 0.1, volume=max(1000, ask_vol // 2)),
            ],
        ),
        tradable=True,
        session="regular",
    )


def _make_states_for_stronger_run() -> list[MarketState]:
    start = pd.Timestamp("2026-03-12 09:00:00")
    states: list[MarketState] = []

    # Early ticks: tight spread + strong bid imbalance => regime active + entry likely.
    for i in range(4):
        bid = 100.0 + 0.01 * i
        states.append(
            _make_state(
                start + pd.Timedelta(seconds=i),
                best_bid=bid,
                best_ask=bid + 0.01,
                bid_vol=8000,
                ask_vol=1800,
            )
        )

    # Later ticks: wider spread (regime likely off) while in-position exits must still work.
    for i in range(4, 10):
        bid = 100.0 + 0.01 * i
        states.append(
            _make_state(
                start + pd.Timedelta(seconds=i),
                best_bid=bid,
                best_ask=bid + 0.20,
                bid_vol=5000,
                ask_vol=4000,
            )
        )

    return states


def _build_v2_integration_spec() -> StrategySpecV2:
    return StrategySpecV2(
        name="stronger_v2_integration",
        version="2.0",
        preconditions=[
            PreconditionV2(
                name="spread_gate",
                condition=ComparisonExpr(feature="spread_bps", op="<", threshold=30.0),
            )
        ],
        entry_policies=[
            EntryPolicyV2(
                name="long_entry",
                side="long",
                trigger=ComparisonExpr(feature="order_imbalance", op=">", threshold=0.25),
                strength=ConstExpr(0.9),
                constraints=EntryConstraints(cooldown_ticks=1, no_reentry_until_flat=True),
            )
        ],
        exit_policies=[
            ExitPolicyV2(
                name="exits",
                rules=[
                    ExitRuleV2(
                        name="pnl_stop",
                        priority=1,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("unrealized_pnl_bps"),
                            op="<=",
                            threshold=-15.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                    ExitRuleV2(
                        name="time_exit",
                        priority=2,
                        condition=ComparisonExpr(
                            left=PositionAttrExpr("holding_ticks"),
                            op=">=",
                            threshold=3.0,
                        ),
                        action=ExitActionV2(type="close_all"),
                    ),
                ],
            )
        ],
        risk_policy=RiskPolicyV2(
            max_position=200,
            inventory_cap=200,
            position_sizing=PositionSizingV2(mode="fixed", base_size=100, max_size=100),
            degradation_rules=[
                RiskDegradationRuleV2(
                    condition=ComparisonExpr(left=StateVarExpr("loss_streak"), op=">=", threshold=3.0),
                    action=RiskDegradationActionV2(type="block_new_entries"),
                )
            ],
        ),
        regimes=[
            RegimeV2(
                name="tight_spread",
                priority=1,
                when=ComparisonExpr(feature="spread_bps", op="<", threshold=15.0),
                entry_policy_refs=["long_entry"],
                exit_policy_ref="exits",
            )
        ],
        execution_policy=ExecutionPolicyV2(
            placement_mode="adaptive",
            cancel_after_ticks=15,
            max_reprices=3,
            adaptation_rules=[
                ExecutionAdaptationRuleV2(
                    condition=ComparisonExpr(feature="spread_bps", op="<", threshold=3.0),
                    override=ExecutionAdaptationOverrideV2(
                        placement_mode="aggressive_cross",
                        cancel_after_ticks=1,
                        max_reprices=0,
                    ),
                )
            ],
        ),
        state_policy=StatePolicyV2(
            vars={
                "cooldown_until_tick": 0.0,
                "loss_streak": 0.0,
                "entry_count": 0.0,
                "flatten_count": 0.0,
            },
            guards=[
                StateGuardV2(
                    name="cooldown_guard",
                    condition=ComparisonExpr(
                        left=StateVarExpr("cooldown_until_tick"),
                        op=">",
                        threshold=0.0,
                    ),
                    effect="block_entry",
                )
            ],
            events=[
                StateEventV2(
                    name="mark_entry",
                    on="on_entry",
                    updates=[
                        StateUpdateV2(var="entry_count", op="increment", value=1.0),
                        StateUpdateV2(var="cooldown_until_tick", op="set", value=2.0),
                    ],
                ),
                StateEventV2(
                    name="on_flatten",
                    on="on_flatten",
                    updates=[
                        StateUpdateV2(var="flatten_count", op="increment", value=1.0),
                        StateUpdateV2(var="cooldown_until_tick", op="reset"),
                    ],
                ),
            ],
        ),
    )


def test_execution_hint_consumed_with_real_fill():
    spec = _build_v2_integration_spec()
    strategy = compile_strategy(spec)

    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=7,
        placement_style="passive",  # should be overridden by execution hint tags
        impact_model="linear",
        latency_ms=1.0,
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=strategy)
    runner._setup_components(config)

    state0 = _make_states_for_stronger_run()[0]
    signal = strategy.generate_signal(state0)
    assert signal is not None
    assert signal.tags.get("placement_mode") == "aggressive_cross"
    assert signal.tags.get("cancel_after_ticks") == 1
    # max_reprices=0 may be omitted from tags by runtime (equivalent to no repricing).
    assert signal.tags.get("max_reprices") in (None, 0)

    parent = runner._create_parent_order(signal=signal, delta=100, state=state0)
    assert parent is not None
    assert parent.meta.get("execution_hints", {}).get("placement_mode") == "aggressive_cross"

    children = runner._slice_order(parent, state0)
    assert children
    assert children[0].meta.get("placement_policy") == "AggressivePlacement"

    fills = runner._fill_simulator.simulate_fills(parent, children, state0)
    assert fills
    assert fills[0].latency_ms > 0.0
    assert fills[0].market_impact_bps > 0.0


def test_stronger_runner_regression_with_position_attr_regime_state_policy():
    spec = _build_v2_integration_spec()
    strategy = compile_strategy(spec)

    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=11,
        placement_style="spread_adaptive",
        impact_model="linear",
        latency_ms=1.0,
    )
    runner = PipelineRunner(config=config, data_dir=".", strategy=strategy)

    states = _make_states_for_stronger_run()
    result = runner.run(states)
    summary = result.summary()

    assert result.n_fills >= 2  # entry + at least one exit fill
    assert summary["fill_rate"] > 0.0
    assert summary["avg_latency_ms"] > 0.0
    assert summary["avg_market_impact_bps"] > 0.0

    rt = strategy._states["TEST"]
    assert rt.state_vars["entry_count"] >= 1.0
    assert rt.state_vars["flatten_count"] >= 1.0
    assert rt.position_size == 0.0


class _PassiveJoinOneShotStrategy(Strategy):
    def __init__(self) -> None:
        self._emitted = False

    @property
    def name(self) -> str:
        return "passive_join_one_shot"

    def reset(self) -> None:
        self._emitted = False

    def generate_signal(self, state: MarketState):
        if self._emitted:
            return None
        self._emitted = True
        return Signal(
            timestamp=state.timestamp,
            symbol=state.symbol,
            score=0.9,
            expected_return=4.0,
            confidence=0.95,
            horizon_steps=1,
            tags={"placement_mode": "passive_join", "cancel_after_ticks": 1, "max_reprices": 1},
            is_valid=True,
        )


class _LegacyTimeoutCancelReplace(CancelReplaceLogic):
    # Legacy behavior: timeout is a hard cancel even for passive_join.

    def should_cancel(
        self,
        child,
        state,
        time_since_submit: float,
        cancel_after_ticks: int | None = None,
        placement_mode: str | None = None,
    ) -> tuple[bool, str]:
        timeout_seconds = self.timeout_seconds
        if cancel_after_ticks is not None and cancel_after_ticks > 0:
            timeout_seconds = cancel_after_ticks * (self.tick_interval_ms / 1000.0)
        if time_since_submit >= timeout_seconds:
            return True, f"timeout ({time_since_submit:.1f}s >= {timeout_seconds}s)"
        return super().should_cancel(
            child=child,
            state=state,
            time_since_submit=time_since_submit,
            cancel_after_ticks=cancel_after_ticks,
            placement_mode=placement_mode,
        )


def _make_states_for_passive_join_timeout_compare() -> list[MarketState]:
    start = pd.Timestamp("2026-03-12 09:00:00")
    states: list[MarketState] = []

    states.append(
        MarketState(
            timestamp=start,
            symbol="TEST",
            lob=LOBSnapshot(
                timestamp=start,
                bid_levels=[LOBLevel(price=100.0, volume=8000), LOBLevel(price=99.9, volume=4000)],
                ask_levels=[LOBLevel(price=100.01, volume=7000), LOBLevel(price=100.11, volume=4000)],
            ),
            tradable=True,
            session="regular",
        )
    )

    states.append(
        MarketState(
            timestamp=start + pd.Timedelta(seconds=1),
            symbol="TEST",
            lob=LOBSnapshot(
                timestamp=start + pd.Timedelta(seconds=1),
                bid_levels=[LOBLevel(price=100.0, volume=8000), LOBLevel(price=99.9, volume=4000)],
                ask_levels=[LOBLevel(price=100.01, volume=7000), LOBLevel(price=100.11, volume=4000)],
            ),
            tradable=True,
            session="regular",
        )
    )

    states.append(
        MarketState(
            timestamp=start + pd.Timedelta(seconds=2),
            symbol="TEST",
            lob=LOBSnapshot(
                timestamp=start + pd.Timedelta(seconds=2),
                bid_levels=[LOBLevel(price=100.0, volume=8000), LOBLevel(price=99.9, volume=4000)],
                ask_levels=[LOBLevel(price=100.01, volume=7000), LOBLevel(price=100.11, volume=4000)],
                last_trade_price=100.0,
                last_trade_volume=20000,
            ),
            tradable=True,
            session="regular",
        )
    )

    states.append(
        MarketState(
            timestamp=start + pd.Timedelta(seconds=3),
            symbol="TEST",
            lob=LOBSnapshot(
                timestamp=start + pd.Timedelta(seconds=3),
                bid_levels=[LOBLevel(price=100.0, volume=8000), LOBLevel(price=99.9, volume=4000)],
                ask_levels=[LOBLevel(price=100.01, volume=7000), LOBLevel(price=100.11, volume=4000)],
                last_trade_price=100.0,
                last_trade_volume=20000,
            ),
            tradable=True,
            session="regular",
        )
    )

    return states


def test_passive_join_old_vs_new_policy_comparison_on_same_signal():
    states = _make_states_for_passive_join_timeout_compare()

    config = BacktestConfig(
        symbol="TEST",
        start_date="2026-03-12",
        end_date="2026-03-12",
        seed=31,
        placement_style="passive",
        impact_model="linear",
        latency_ms=100.0,
    )

    old_runner = PipelineRunner(config=config, data_dir=".", strategy=_PassiveJoinOneShotStrategy())
    old_setup = old_runner._setup_components

    def _old_setup_with_override(cfg):
        old_setup(cfg)
        old_runner._timing_logic.interval_seconds = 10.0
        old_runner._timing_logic.deadline_urgency_seconds = -1.0
        old_runner._cancel_replace = _LegacyTimeoutCancelReplace(tick_interval_ms=config.latency_ms)

    old_runner._setup_components = _old_setup_with_override
    old_result = old_runner.run(states)

    new_runner = PipelineRunner(config=config, data_dir=".", strategy=_PassiveJoinOneShotStrategy())
    new_setup = new_runner._setup_components

    def _new_setup_with_override(cfg):
        new_setup(cfg)
        new_runner._timing_logic.interval_seconds = 10.0
        new_runner._timing_logic.deadline_urgency_seconds = -1.0

    new_runner._setup_components = _new_setup_with_override
    new_result = new_runner.run(states)

    assert new_result.execution_report.cancel_rate < old_result.execution_report.cancel_rate
    assert new_result.n_fills > old_result.n_fills
