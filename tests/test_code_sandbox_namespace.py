"""
tests/test_code_sandbox_namespace.py
--------------------------------------
코드 샌드박스의 모듈 수준 상수 참조 버그 회귀 테스트.

핵심 검증:
  1. exec_strategy_code() 후 generate_signal()이 모듈 상수를 정상 참조한다.
  2. check_code_entry_frequency()가 모듈 상수를 사용하는 코드에 대해 올바른 frequency를 계산한다.
  3. runtime error가 entry_too_sparse가 아니라 code_runtime_error로 노출된다.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest


# ── 테스트 픽스처 ─────────────────────────────────────────────────────────────

_GOOD_CODE = """\
ORDER_IMBALANCE_THRESHOLD = 0.10

def generate_signal(features, position):
    if features.get("order_imbalance", 0.0) > ORDER_IMBALANCE_THRESHOLD:
        return 1
    return None
"""

_ALWAYS_ENTER_CODE = """\
THRESHOLD = -999.0

def generate_signal(features, position):
    if features.get("order_imbalance", 0.0) > THRESHOLD:
        return 1
    return None
"""

_BROKEN_CODE = """\
def generate_signal(features, position):
    return undefined_name_xyz  # NameError every call
"""

_PARTIAL_RUNTIME_ERROR_CODE = """\
CALL_COUNT = 0

def generate_signal(features, position):
    global CALL_COUNT
    CALL_COUNT += 1
    if CALL_COUNT == 1:
        raise RuntimeError("boom-once")
    return 1
"""


def _make_mock_state(order_imbalance: float = 0.5):
    """テスト用 MarketState mock."""
    state = MagicMock()
    state.lob.mid_price = 100.0
    state.lob.order_imbalance = order_imbalance
    state.lob.best_bid = 99.9
    state.lob.best_ask = 100.1
    state.lob.bid_levels = []
    state.lob.ask_levels = []
    state.spread_bps = 20.0
    state.features = {"order_imbalance": order_imbalance, "spread_bps": 20.0}
    state.trades = None
    return state


# ── 1. exec_strategy_code() 네임스페이스 픽스 ────────────────────────────────

class TestExecStrategyCodeNamespace:

    def test_module_constant_accessible_in_function(self):
        """generate_signal이 모듈 수준 상수를 NameError 없이 참조한다."""
        from strategy_loop.code_sandbox import exec_strategy_code

        ns = exec_strategy_code(_GOOD_CODE)
        fn = ns["generate_signal"]

        # order_imbalance=0.5 > 0.10 → 1 반환
        result = fn({"order_imbalance": 0.5}, {"holding_ticks": 0.0, "in_position": False, "position_side": ""})
        assert result == 1

        # order_imbalance=0.05 < 0.10 → None 반환
        result = fn({"order_imbalance": 0.05}, {"holding_ticks": 0.0, "in_position": False, "position_side": ""})
        assert result is None

    def test_module_constant_not_in_builtins_before_fix(self):
        """구 방식(분리 exec)이라면 NameError가 났을 것임을 명시적으로 검증."""
        import ast
        from strategy_loop.code_sandbox import _SAFE_BUILTINS

        # 구 방식: separate globals/locals
        safe_globals = {"__builtins__": _SAFE_BUILTINS}
        old_namespace: dict = {}
        exec(compile(_GOOD_CODE, "<test>", "exec"), safe_globals, old_namespace)  # noqa: S102

        fn_old = old_namespace["generate_signal"]
        with pytest.raises(NameError):
            fn_old({"order_imbalance": 0.5}, {"holding_ticks": 0.0, "in_position": False, "position_side": ""})

    def test_builtins_removed_from_returned_namespace(self):
        """반환된 namespace에 __builtins__가 없다."""
        from strategy_loop.code_sandbox import exec_strategy_code

        ns = exec_strategy_code(_GOOD_CODE)
        assert "__builtins__" not in ns

    def test_generate_signal_key_in_namespace(self):
        from strategy_loop.code_sandbox import exec_strategy_code

        ns = exec_strategy_code(_GOOD_CODE)
        assert "generate_signal" in ns
        assert callable(ns["generate_signal"])


# ── 2. distribution_filter 정상 frequency 계산 ──────────────────────────────

class TestCheckCodeEntryFrequency:

    def _make_states(self, n: int, oi: float = 0.5):
        return [_make_mock_state(order_imbalance=oi) for _ in range(n)]

    def test_correct_frequency_with_module_constant(self):
        """모듈 상수를 사용하는 코드가 올바른 entry_frequency를 반환한다."""
        from strategy_loop.distribution_filter import check_code_entry_frequency

        # order_imbalance=0.5, threshold=0.10 → 항상 진입 (freq≈1.0)
        states = self._make_states(100, oi=0.5)
        result = check_code_entry_frequency(_ALWAYS_ENTER_CODE, states, sample_size=100)

        assert result.passed is False  # >0.50이므로 too_frequent
        assert result.entry_frequency > 0.9
        assert "entry_too_frequent" in result.reason

    def test_sparse_signal_code(self):
        """order_imbalance=-0.5 → threshold 0.10 미만 → 진입 없음 → too_sparse."""
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = self._make_states(500, oi=-0.5)
        result = check_code_entry_frequency(_GOOD_CODE, states, sample_size=500)

        assert result.passed is False
        assert result.entry_frequency == 0.0
        assert "entry_too_sparse" in result.reason

    def test_valid_frequency_range(self):
        """일부는 threshold 이상, 일부는 미만인 경우 passed=True."""
        from strategy_loop.distribution_filter import check_code_entry_frequency

        # oi=0.5 인 state 40개 + oi=-0.5 인 state 60개 → freq≈0.4 → passed
        states = (
            self._make_states(40, oi=0.5)
            + self._make_states(60, oi=-0.5)
        )
        result = check_code_entry_frequency(_GOOD_CODE, states, sample_size=100)

        assert result.passed is True
        assert 0.001 <= result.entry_frequency <= 0.50


# ── 3. runtime error가 code_runtime_error로 노출 ────────────────────────────

class TestRuntimeErrorExposure:

    def _make_states(self, n: int, oi: float = 0.5):
        return [_make_mock_state(order_imbalance=oi) for _ in range(n)]

    def test_nameerror_surfaces_as_code_runtime_error(self):
        """NameError가 발생하는 코드가 entry_too_sparse가 아닌 code_runtime_error를 반환한다."""
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = self._make_states(100, oi=0.5)
        result = check_code_entry_frequency(_BROKEN_CODE, states, sample_size=100)

        assert result.passed is False
        assert "code_runtime_error" in result.reason, (
            f"Expected 'code_runtime_error' in reason, got: {result.reason!r}"
        )
        # entry_too_sparse로 오인되면 안 된다
        assert "entry_too_sparse" not in result.reason

    def test_single_runtime_error_is_reported_immediately(self):
        """일부 호출만 실패해도 code_runtime_error로 즉시 노출한다."""
        from strategy_loop.distribution_filter import check_code_entry_frequency

        states = self._make_states(50, oi=0.5)
        result = check_code_entry_frequency(_PARTIAL_RUNTIME_ERROR_CODE, states, sample_size=50)

        assert result.passed is False
        assert "code_runtime_error" in result.reason
        assert "RuntimeError" in result.reason
        assert "sample_idx=0" in result.reason
        assert "entry_too_sparse" not in result.reason


# ── 4. CodeStrategy 로깅 확인 (연기 테스트) ─────────────────────────────────

class TestCodeStrategyErrorLogging:

    def test_error_is_logged_not_silenced(self, caplog):
        """generate_signal에서 NameError 발생 시 WARNING 로그가 기록된다."""
        import logging
        from strategy_loop.code_strategy import CodeStrategy

        strategy = CodeStrategy(_BROKEN_CODE, name="broken_test")
        state = _make_mock_state(order_imbalance=0.5)

        with caplog.at_level(logging.WARNING, logger="strategy_loop.code_strategy"):
            strategy.generate_signal(state)

        assert any(
            "code_runtime_error" in r.message and "NameError" in r.message
            for r in caplog.records
        ), "Expected code_runtime_error + NameError in WARNING log"
