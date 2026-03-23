"""StrategyCompiler가 현재 MarketState/LOBSnapshot 계약에 맞게 동작하는지 검증한다."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data.layer0_data.market_state import LOBLevel, LOBSnapshot, MarketState
from strategy_block.strategy_compiler.compiler import CompiledStrategy, StrategyCompiler
from strategy_block.strategy_specs.schema import StrategySpec, SignalRule, FilterRule, ExitRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lob(
    bid_price: float = 100.0,
    ask_price: float = 101.0,
    bid_vol: int = 500,
    ask_vol: int = 300,
    n_levels: int = 5,
    ts: str = "2026-03-18 09:00:00",
) -> LOBSnapshot:
    bids = [LOBLevel(price=bid_price - i, volume=bid_vol + 10 * i) for i in range(n_levels)]
    asks = [LOBLevel(price=ask_price + i, volume=ask_vol + 10 * i) for i in range(n_levels)]
    return LOBSnapshot(timestamp=pd.Timestamp(ts), bid_levels=bids, ask_levels=asks)


def _make_state(
    bid_price: float = 100.0,
    ask_price: float = 101.0,
    bid_vol: int = 500,
    ask_vol: int = 300,
    ts: str = "2026-03-18 09:00:00",
    features: dict | None = None,
    trades: pd.DataFrame | None = None,
) -> MarketState:
    lob = _make_lob(bid_price=bid_price, ask_price=ask_price,
                     bid_vol=bid_vol, ask_vol=ask_vol, ts=ts)
    return MarketState(
        timestamp=pd.Timestamp(ts),
        symbol="005930",
        lob=lob,
        features=features or {},
        trades=trades,
    )


def _make_simple_spec() -> StrategySpec:
    return StrategySpec(
        name="test_spec",
        version="1.0",
        signal_rules=[
            SignalRule(feature="order_imbalance", operator=">", threshold=0.1,
                       score_contribution=0.5),
        ],
        exit_rules=[
            ExitRule(exit_type="time_exit", timeout_ticks=100),
        ],
    )


# ---------------------------------------------------------------------------
# Tests: _extract_features
# ---------------------------------------------------------------------------

class TestExtractFeatures:
    """_extract_features()가 bid_levels/ask_levels/features dict/trades를 올바르게 읽는지 확인."""

    def test_basic_lob_features(self):
        """mid_price, spread_bps, order_imbalance, best_bid/ask가 계산된다."""
        state = _make_state(bid_price=100.0, ask_price=102.0, bid_vol=600, ask_vol=400)
        strategy = CompiledStrategy(_make_simple_spec())
        features = strategy._extract_features(state)

        assert features["mid_price"] == pytest.approx(101.0)
        assert features["best_bid"] == pytest.approx(100.0)
        assert features["best_ask"] == pytest.approx(102.0)
        assert features["spread_bps"] > 0
        assert "order_imbalance" in features

    def test_depth_features_use_bid_levels(self):
        """bid_depth_5, ask_depth_5, depth_imbalance가 bid_levels/ask_levels에서 계산된다."""
        state = _make_state(bid_vol=1000, ask_vol=500)
        strategy = CompiledStrategy(_make_simple_spec())
        features = strategy._extract_features(state)

        assert features["bid_depth_5"] > 0
        assert features["ask_depth_5"] > 0
        # bid_vol > ask_vol → depth_imbalance > 0
        assert features["depth_imbalance"] > 0

    def test_no_attribute_error_on_lob(self):
        """lob.bids/lob.asks 대신 lob.bid_levels/lob.ask_levels를 사용하므로 AttributeError가 나지 않는다."""
        state = _make_state()
        strategy = CompiledStrategy(_make_simple_spec())
        # This was the core bug: AttributeError: 'LOBSnapshot' object has no attribute 'bids'
        features = strategy._extract_features(state)
        assert isinstance(features, dict)
        assert len(features) > 0

    def test_features_dict_read(self):
        """state.features dict에서 값을 읽는다 (getattr 대신 .get())."""
        state = _make_state(features={
            "trade_flow_imbalance": 0.75,
            "volume_surprise": 1.5,
            "micro_price": 100.5,
        })
        strategy = CompiledStrategy(_make_simple_spec())
        features = strategy._extract_features(state)

        assert features["trade_flow_imbalance"] == pytest.approx(0.75)
        assert features["volume_surprise"] == pytest.approx(1.5)
        assert features["micro_price"] == pytest.approx(100.5)

    def test_trades_dataframe(self):
        """state.trades DataFrame에서 trade_count, recent_volume을 계산한다."""
        trades = pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-03-18 09:00:00", "2026-03-18 09:00:01"]),
            "price": [100.5, 101.0],
            "volume": [100, 200],
            "side": ["buy", "sell"],
        })
        state = _make_state(trades=trades)
        strategy = CompiledStrategy(_make_simple_spec())
        features = strategy._extract_features(state)

        assert features["trade_count"] == 2.0
        assert features["recent_volume"] == pytest.approx(300.0)

    def test_trade_flow_imbalance_derived(self):
        """trades에 side 컬럼이 있으면 trade_flow_imbalance를 자동 계산한다."""
        trades = pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-03-18 09:00:00"] * 4),
            "price": [100.0] * 4,
            "volume": [100] * 4,
            "side": ["buy", "buy", "buy", "sell"],  # 3 buy, 1 sell → 0.5
        })
        state = _make_state(trades=trades)
        strategy = CompiledStrategy(_make_simple_spec())
        features = strategy._extract_features(state)

        assert features["trade_flow_imbalance"] == pytest.approx(0.5)

    def test_empty_trades_no_error(self):
        """trades가 None이거나 빈 DataFrame일 때 에러 없이 작동한다."""
        state_none = _make_state(trades=None)
        state_empty = _make_state(trades=pd.DataFrame())
        strategy = CompiledStrategy(_make_simple_spec())

        f1 = strategy._extract_features(state_none)
        f2 = strategy._extract_features(state_empty)
        assert "trade_count" not in f1
        assert "trade_count" not in f2

    def test_features_dict_overrides_lob_computed(self):
        """state.features dict의 값이 LOB에서 직접 계산한 값을 덮어쓴다."""
        state = _make_state(features={"spread_bps": 42.0})
        strategy = CompiledStrategy(_make_simple_spec())
        features = strategy._extract_features(state)
        # The features dict value should override the LOB-computed spread_bps
        assert features["spread_bps"] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# Tests: Compile-time feature warning
# ---------------------------------------------------------------------------

class TestCompileWarnings:
    """컴파일 시 알 수 없는 feature에 대해 경고가 발생하는지 확인."""

    def test_unknown_feature_warns(self, caplog):
        spec = StrategySpec(
            name="test_unknown",
            signal_rules=[
                SignalRule(feature="nonexistent_feature_xyz", operator=">",
                           threshold=0.5, score_contribution=1.0),
            ],
        )
        import logging
        with caplog.at_level(logging.WARNING):
            StrategyCompiler.compile(spec)
        assert "nonexistent_feature_xyz" in caplog.text

    def test_known_feature_no_warning(self, caplog):
        spec = StrategySpec(
            name="test_known",
            signal_rules=[
                SignalRule(feature="order_imbalance", operator=">",
                           threshold=0.1, score_contribution=0.5),
            ],
        )
        import logging
        with caplog.at_level(logging.WARNING):
            StrategyCompiler.compile(spec)
        assert "unknown features" not in caplog.text


# ---------------------------------------------------------------------------
# Tests: End-to-end signal generation
# ---------------------------------------------------------------------------

class TestCompiledStrategySignal:
    """CompiledStrategy.generate_signal()이 실제 MarketState에서 동작하는지 확인."""

    def test_generates_signal_on_imbalance(self):
        """order_imbalance > 0.1 일 때 bullish signal을 생성한다."""
        spec = StrategySpec(
            name="imb_test",
            signal_rules=[
                SignalRule(feature="order_imbalance", operator=">", threshold=0.1,
                           score_contribution=0.8),
            ],
            exit_rules=[ExitRule(exit_type="time_exit", timeout_ticks=100)],
        )
        strategy = StrategyCompiler.compile(spec)
        # bid_vol=800, ask_vol=200 → imbalance = 0.6 > 0.1
        state = _make_state(bid_vol=800, ask_vol=200)
        signal = strategy.generate_signal(state)
        assert signal is not None
        assert signal.score > 0

    def test_filter_blocks_signal(self):
        """spread_bps > 30 filter가 신호를 차단한다."""
        spec = StrategySpec(
            name="filter_test",
            signal_rules=[
                SignalRule(feature="order_imbalance", operator=">", threshold=0.1,
                           score_contribution=0.8),
            ],
            filters=[
                FilterRule(feature="spread_bps", operator=">", threshold=5.0,
                           action="block"),
            ],
            exit_rules=[ExitRule(exit_type="time_exit", timeout_ticks=100)],
        )
        strategy = StrategyCompiler.compile(spec)
        # spread = 101-100=1, mid=100.5 → spread_bps ≈ 99.5 > 5.0 → blocked
        state = _make_state(bid_price=100.0, ask_price=101.0, bid_vol=800, ask_vol=200)
        signal = strategy.generate_signal(state)
        assert signal is None
