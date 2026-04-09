"""
tests/test_raw_runtime.py
---------------------------
Raw formula를 사용하는 코드가 distribution filter와 CodeStrategy 양쪽에서
runtime error 없이 동작하는지 검증한다.

검증 항목:
  1. spread_ticks 공식을 쓰는 코드가 filter를 통과한다.
  2. L1-L3 imbalance 공식을 쓰는 코드가 filter를 통과한다.
  3. ask wall ratio 공식을 쓰는 코드가 filter를 통과한다.
  4. 없는 level에 features.get(..., default)를 쓰면 KeyError 없이 실행된다.
  5. CodeStrategy가 tick_size를 전달하면 feature dict에 들어온다.
  6. 동일한 코드가 distribution filter와 CodeStrategy 양쪽에서 같은 feature
     contract(tick_size 포함)로 실행된다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ── 픽스처 ────────────────────────────────────────────────────────────────────

def _make_lob_level(price: float, volume: int):
    lv = MagicMock()
    lv.price = price
    lv.volume = volume
    return lv


def _make_state(n_bid: int = 5, n_ask: int = 5,
                base_bid: float = 99000.0, tick: float = 5.0,
                order_imbalance: float = 0.2):
    bid_levels = [_make_lob_level(base_bid - i * tick, 200 + i * 20) for i in range(n_bid)]
    ask_levels = [_make_lob_level(base_bid + tick + i * tick, 150 + i * 15) for i in range(n_ask)]

    lob = MagicMock()
    lob.bid_levels = bid_levels
    lob.ask_levels = ask_levels
    lob.mid_price = (bid_levels[0].price + ask_levels[0].price) / 2 if (bid_levels and ask_levels) else 0.0
    lob.order_imbalance = order_imbalance
    lob.best_bid = bid_levels[0].price if bid_levels else None
    lob.best_ask = ask_levels[0].price if ask_levels else None

    state = MagicMock()
    state.lob = lob
    state.spread_bps = 10.0
    state.features = {"order_imbalance": order_imbalance}
    state.trades = None
    state.timestamp = MagicMock()
    state.symbol = "TEST"
    return state


def _make_states(n: int, **kwargs):
    return [_make_state(**kwargs) for _ in range(n)]


# ── 코드 스니펫 ───────────────────────────────────────────────────────────────

# spread_ticks 공식
_SPREAD_TICKS_CODE = """\
SPREAD_MAX_TICKS = 3
HOLDING_TICKS_EXIT = 20

def generate_signal(features, position):
    if position["in_position"]:
        if position["holding_ticks"] >= HOLDING_TICKS_EXIT:
            return -1
        return None
    tick_size = features.get("tick_size", 1.0)
    bid1 = features.get("bid_1_price", 0.0)
    ask1 = features.get("ask_1_price", 0.0)
    if tick_size <= 0:
        return None
    spread_ticks = (ask1 - bid1) / tick_size
    if spread_ticks <= SPREAD_MAX_TICKS:
        return 1
    return None
"""

# L1-L3 imbalance 공식
_L1_L3_IMBALANCE_CODE = """\
L1L3_THRESHOLD = 0.1
HOLDING_TICKS_EXIT = 20

def generate_signal(features, position):
    if position["in_position"]:
        if position["holding_ticks"] >= HOLDING_TICKS_EXIT:
            return -1
        return None
    b1 = features.get("bid_1_volume", 0.0)
    b2 = features.get("bid_2_volume", 0.0)
    b3 = features.get("bid_3_volume", 0.0)
    a1 = features.get("ask_1_volume", 0.0)
    a2 = features.get("ask_2_volume", 0.0)
    a3 = features.get("ask_3_volume", 0.0)
    total = b1 + b2 + b3 + a1 + a2 + a3
    if total <= 0:
        return None
    imbalance = (b1 + b2 + b3 - a1 - a2 - a3) / total
    if imbalance > L1L3_THRESHOLD:
        return 1
    return None
"""

# ask wall ratio 공식
_ASK_WALL_RATIO_CODE = """\
ASK_WALL_MAX_RATIO = 2.0
HOLDING_TICKS_EXIT = 20

def generate_signal(features, position):
    if position["in_position"]:
        if position["holding_ticks"] >= HOLDING_TICKS_EXIT:
            return -1
        return None
    a1 = features.get("ask_1_volume", 1.0)
    a2 = features.get("ask_2_volume", 1.0)
    if a2 <= 0:
        return None
    wall_ratio = a1 / a2
    if wall_ratio < ASK_WALL_MAX_RATIO:
        return 1
    return None
"""

# 없는 level에 대한 안전한 fallback
_DEEP_LEVEL_SAFE_CODE = """\
HOLDING_TICKS_EXIT = 20

def generate_signal(features, position):
    if position["in_position"]:
        if position["holding_ticks"] >= HOLDING_TICKS_EXIT:
            return -1
        return None
    # 없는 level → default로 처리
    b8 = features.get("bid_8_volume", 0.0)
    a8 = features.get("ask_8_volume", 0.0)
    b1 = features.get("bid_1_volume", 1.0)
    if b1 <= 0:
        return None
    depth_ratio = (b8 + a8) / b1
    if depth_ratio < 0.5:
        return 1
    return None
"""


# ── 1. distribution filter 통과 ──────────────────────────────────────────────

class TestDistributionFilterRawFormulas:

    def _make_states(self, **kw):
        return _make_states(200, **kw)

    def test_spread_ticks_formula_no_runtime_error(self):
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = self._make_states(n_bid=5, n_ask=5, tick=5.0)
        result = check_code_entry_frequency(
            _SPREAD_TICKS_CODE, states, sample_size=100, tick_size=5.0
        )
        # runtime error가 아닌 일반적인 filter 결과여야 한다
        assert "code_runtime_error" not in result.reason
        assert "code_exec_error" not in result.reason

    def test_l1_l3_imbalance_formula_no_runtime_error(self):
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = self._make_states(n_bid=5, n_ask=5)
        result = check_code_entry_frequency(
            _L1_L3_IMBALANCE_CODE, states, sample_size=100
        )
        assert "code_runtime_error" not in result.reason

    def test_ask_wall_ratio_formula_no_runtime_error(self):
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = self._make_states(n_bid=5, n_ask=5)
        result = check_code_entry_frequency(
            _ASK_WALL_RATIO_CODE, states, sample_size=100
        )
        assert "code_runtime_error" not in result.reason

    def test_deep_level_safe_fallback_no_runtime_error(self):
        """얕은 book(3레벨)에서 8레벨을 features.get으로 안전하게 조회한다."""
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = _make_states(200, n_bid=3, n_ask=3)
        result = check_code_entry_frequency(
            _DEEP_LEVEL_SAFE_CODE, states, sample_size=100
        )
        assert "code_runtime_error" not in result.reason


# ── 2. tick_size가 filter와 CodeStrategy 양쪽에서 동일하게 주입된다 ──────────

class TestTickSizeConsistency:

    _TICK_SIZE_CODE = """\
HOLDING_TICKS_EXIT = 20

def generate_signal(features, position):
    if position["in_position"]:
        if position["holding_ticks"] >= HOLDING_TICKS_EXIT:
            return -1
        return None
    ts = features.get("tick_size", -1.0)
    if ts > 0:
        return 1
    return None
"""

    def test_tick_size_reaches_filter(self):
        """distribution filter가 tick_size를 feature dict에 주입한다."""
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = _make_states(100, n_bid=2, n_ask=2)
        result = check_code_entry_frequency(
            self._TICK_SIZE_CODE, states, sample_size=50, tick_size=5.0
        )
        # tick_size=5.0 > 0이므로 신호 발생 → passed 또는 too_frequent
        assert "code_runtime_error" not in result.reason
        assert result.entry_frequency > 0.0

    def test_code_strategy_receives_tick_size(self):
        """CodeStrategy가 tick_size를 feature dict에 주입한다."""
        from strategy_loop.code_strategy import CodeStrategy

        strategy = CodeStrategy(self._TICK_SIZE_CODE, name="tick_test", tick_size=7.0)
        state = _make_state(n_bid=2, n_ask=2)

        captured_tick_size = None

        _PROBE_CODE = """\
HOLDING_TICKS_EXIT = 20
_captured = []

def generate_signal(features, position):
    _captured.append(features.get("tick_size", None))
    return None
"""
        strategy2 = CodeStrategy(_PROBE_CODE, name="probe", tick_size=7.0)
        strategy2.generate_signal(state)
        assert strategy2._ns["_captured"][-1] == pytest.approx(7.0)
