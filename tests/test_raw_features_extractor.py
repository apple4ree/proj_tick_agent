"""
tests/test_raw_features_extractor.py
--------------------------------------
Raw L1-L10 orderbook feature extractor tests + BUILTIN_FEATURES contract tests.

검증 항목:
  1. 10-level book에서 raw 40개 key와 값이 정확히 노출된다.
  2. shallow book에서 깊은 level key가 포함되지 않는다.
  3. tick_size가 주입되면 feature dict에 들어온다.
  4. tick_size 미지정 시 기본값 1.0이 들어온다.
  5. 기존 summary feature는 그대로 유지된다.
  6. BUILTIN_FEATURES canonical contract 검증.
  7. prompt $features_list에 raw feature가 실제로 포함된다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ── 픽스처 헬퍼 ───────────────────────────────────────────────────────────────

def _make_lob_level(price: float, volume: int):
    lv = MagicMock()
    lv.price = price
    lv.volume = volume
    return lv


def _make_state_with_book(n_bid: int, n_ask: int, base_bid: float = 99000.0, tick: float = 5.0):
    """n_bid / n_ask 레벨을 가진 MarketState mock을 생성한다."""
    bid_levels = [_make_lob_level(base_bid - i * tick, 100 + i * 10) for i in range(n_bid)]
    ask_levels = [_make_lob_level(base_bid + tick + i * tick, 80 + i * 8) for i in range(n_ask)]

    lob = MagicMock()
    lob.bid_levels = bid_levels
    lob.ask_levels = ask_levels
    lob.mid_price = (bid_levels[0].price + ask_levels[0].price) / 2 if (bid_levels and ask_levels) else 0.0
    lob.order_imbalance = 0.1
    lob.best_bid = bid_levels[0].price if bid_levels else None
    lob.best_ask = ask_levels[0].price if ask_levels else None

    state = MagicMock()
    state.lob = lob
    state.spread_bps = 10.0
    state.features = {}
    state.trades = None
    return state


# ── 1. 10-level book raw 40개 key 노출 ───────────────────────────────────────

class TestFullBookExtraction:

    def test_bid_price_keys_present(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(10, 10)
        features = extract_builtin_features(state)

        for i in range(1, 11):
            assert f"bid_{i}_price" in features, f"bid_{i}_price missing"
            assert f"ask_{i}_price" in features, f"ask_{i}_price missing"

    def test_bid_volume_keys_present(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(10, 10)
        features = extract_builtin_features(state)

        for i in range(1, 11):
            assert f"bid_{i}_volume" in features, f"bid_{i}_volume missing"
            assert f"ask_{i}_volume" in features, f"ask_{i}_volume missing"

    def test_raw_values_correct(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        base_bid = 99000.0
        tick = 5.0
        state = _make_state_with_book(10, 10, base_bid=base_bid, tick=tick)
        features = extract_builtin_features(state)

        # bid_1_price = base_bid - 0*tick = 99000
        assert features["bid_1_price"] == pytest.approx(99000.0)
        # bid_3_price = base_bid - 2*tick = 98990
        assert features["bid_3_price"] == pytest.approx(98990.0)
        # ask_1_price = base_bid + tick = 99005
        assert features["ask_1_price"] == pytest.approx(99005.0)

        # bid_1_volume = 100 + 0*10 = 100
        assert features["bid_1_volume"] == pytest.approx(100.0)
        # bid_3_volume = 100 + 2*10 = 120
        assert features["bid_3_volume"] == pytest.approx(120.0)

    def test_total_raw_key_count(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(10, 10)
        features = extract_builtin_features(state)

        # bid_N_price, bid_N_volume, ask_N_price, ask_N_volume — 4 types × 10 levels = 40
        raw_keys = [
            k for k in features
            if (k.startswith("bid_") or k.startswith("ask_"))
            and k.endswith(("_price", "_volume"))
        ]
        assert len(raw_keys) == 40


# ── 2. shallow book — 깊은 level key 없음 ────────────────────────────────────

class TestShallowBookExtraction:

    def test_missing_deep_levels_absent(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(3, 3)
        features = extract_builtin_features(state)

        # 존재하는 레벨 (1-3) 있어야 함
        for i in range(1, 4):
            assert f"bid_{i}_price" in features
            assert f"ask_{i}_price" in features

        # 존재하지 않는 레벨 (4-10) 없어야 함
        for i in range(4, 11):
            assert f"bid_{i}_price" not in features, f"bid_{i}_price should be absent"
            assert f"ask_{i}_price" not in features, f"ask_{i}_price should be absent"
            assert f"bid_{i}_volume" not in features
            assert f"ask_{i}_volume" not in features

    def test_single_level_book(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(1, 1)
        features = extract_builtin_features(state)

        assert "bid_1_price" in features
        assert "ask_1_price" in features
        assert "bid_2_price" not in features
        assert "ask_2_price" not in features

    def test_empty_book_no_raw_keys(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(0, 0)
        features = extract_builtin_features(state)

        raw_level_keys = [
            k for k in features
            if (k.startswith("bid_") or k.startswith("ask_"))
            and k.endswith(("_price", "_volume"))
        ]
        assert raw_level_keys == [], f"Expected no raw level keys but got: {raw_level_keys}"


# ── 3. tick_size 주입 ─────────────────────────────────────────────────────────

class TestTickSizeInjection:

    def test_tick_size_injected_when_provided(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(2, 2)
        features = extract_builtin_features(state, tick_size=5.0)
        assert features["tick_size"] == pytest.approx(5.0)

    def test_tick_size_default_is_1(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(2, 2)
        features = extract_builtin_features(state)
        assert "tick_size" in features
        assert features["tick_size"] == pytest.approx(1.0)

    def test_tick_size_always_present(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(0, 0)
        features = extract_builtin_features(state)
        assert "tick_size" in features

    def test_spread_ticks_formula_works(self):
        """spread_ticks = (ask_1 - bid_1) / tick_size는 raw feature로 계산 가능하다."""
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        base_bid = 99000.0
        tick = 5.0
        state = _make_state_with_book(3, 3, base_bid=base_bid, tick=tick)
        features = extract_builtin_features(state, tick_size=tick)

        spread_ticks = (features["ask_1_price"] - features["bid_1_price"]) / features["tick_size"]
        assert spread_ticks == pytest.approx(1.0)  # 1 tick spread


# ── 4. 기존 summary feature 유지 확인 ────────────────────────────────────────

class TestSummaryFeaturesPreserved:

    def test_summary_features_still_present(self):
        from strategy_block.strategy_compiler.v2.features import extract_builtin_features

        state = _make_state_with_book(5, 5)
        features = extract_builtin_features(state)

        for key in ("mid_price", "spread_bps", "order_imbalance", "best_bid", "best_ask",
                    "bid_depth_5", "ask_depth_5", "depth_imbalance"):
            assert key in features, f"Summary feature '{key}' missing"


# ── 5. BUILTIN_FEATURES canonical contract ────────────────────────────────────

class TestBuiltinFeaturesContract:
    """BUILTIN_FEATURES가 실제 extractor contract의 canonical source-of-truth임을 검증한다."""

    def test_tick_size_in_builtin_features(self):
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        assert "tick_size" in BUILTIN_FEATURES

    def test_bid_1_price_in_builtin_features(self):
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        assert "bid_1_price" in BUILTIN_FEATURES

    def test_ask_10_volume_in_builtin_features(self):
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        assert "ask_10_volume" in BUILTIN_FEATURES

    def test_raw_key_count_is_40(self):
        """bid/ask × 10 levels × price/volume = 40개."""
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        raw_keys = {
            k for k in BUILTIN_FEATURES
            if (k.startswith("bid_") or k.startswith("ask_"))
            and k.endswith(("_price", "_volume"))
        }
        assert len(raw_keys) == 40

    def test_all_10_bid_price_levels_present(self):
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        for i in range(1, 11):
            assert f"bid_{i}_price" in BUILTIN_FEATURES
            assert f"bid_{i}_volume" in BUILTIN_FEATURES

    def test_all_10_ask_levels_present(self):
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        for i in range(1, 11):
            assert f"ask_{i}_price" in BUILTIN_FEATURES
            assert f"ask_{i}_volume" in BUILTIN_FEATURES

    def test_summary_features_preserved_in_builtin(self):
        """기존 summary feature들이 BUILTIN_FEATURES에 유지된다."""
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
        legacy = {
            "mid_price", "spread_bps", "order_imbalance", "best_bid", "best_ask",
            "bid_depth_5", "ask_depth_5", "depth_imbalance",
            "order_imbalance_ema", "order_imbalance_delta",
            "trade_flow_imbalance_ema", "depth_imbalance_ema", "spread_bps_ema",
        }
        for key in legacy:
            assert key in BUILTIN_FEATURES, f"Legacy key '{key}' missing from BUILTIN_FEATURES"

    def test_extractor_full_book_keys_subset_of_builtin(self):
        """10-level book에서 extractor가 반환하는 raw key는 모두 BUILTIN_FEATURES에 속한다."""
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES, extract_builtin_features

        state = _make_state_with_book(10, 10)
        features = extract_builtin_features(state, tick_size=5.0)

        extractor_keys = set(features.keys())
        # tick_size와 raw level keys는 BUILTIN_FEATURES에 있어야 함
        missing = extractor_keys - BUILTIN_FEATURES
        # summary feature 중 state.features가 비어 있어 실제로 안 나오는 key는 허용
        # 하지만 BUILTIN_FEATURES에 없는 key가 extractor에서 나오면 안 됨
        assert not missing, f"Extractor returned keys not in BUILTIN_FEATURES: {missing}"

    def test_builtin_features_raw_keys_cover_extractor_with_full_book(self):
        """BUILTIN_FEATURES의 raw key 집합은 10-level book extractor 결과를 완전히 커버한다."""
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES, extract_builtin_features

        state = _make_state_with_book(10, 10)
        features = extract_builtin_features(state, tick_size=5.0)

        for key in features:
            assert key in BUILTIN_FEATURES, (
                f"Extractor key '{key}' is not in BUILTIN_FEATURES — "
                "BUILTIN_FEATURES is out of sync with the extractor contract."
            )


# ── 6. prompt $features_list에 raw feature 포함 ───────────────────────────────

class TestPromptFeaturesListRawInclusion:
    """prompt의 $features_list 치환 결과에 raw feature 이름이 실제로 포함되는지 검증한다."""

    def test_features_list_contains_tick_size(self):
        from strategy_loop.prompt_builder import _CODE_GEN_SYSTEM
        assert "tick_size" in _CODE_GEN_SYSTEM

    def test_features_list_contains_bid_1_price(self):
        from strategy_loop.prompt_builder import _CODE_GEN_SYSTEM
        assert "bid_1_price" in _CODE_GEN_SYSTEM

    def test_features_list_contains_ask_10_volume(self):
        from strategy_loop.prompt_builder import _CODE_GEN_SYSTEM
        assert "ask_10_volume" in _CODE_GEN_SYSTEM

    def test_no_literal_features_list_placeholder(self):
        """$features_list가 치환되지 않은 채 남아 있으면 안 된다."""
        from strategy_loop.prompt_builder import _CODE_GEN_SYSTEM
        assert "$features_list" not in _CODE_GEN_SYSTEM
