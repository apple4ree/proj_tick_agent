"""
tests/test_optuna_range_raw.py
--------------------------------
threshold_optimizer의 _CODE_CONST_RANGES에서
raw formula 관련 상수명이 기대한 range spec으로 파싱되는지 검증한다.

검증 항목:
  1. SPREAD_MAX_TICKS  → int range [1, 10]
  2. TICKS_SPREAD_MAX  → int range [1, 10]
  3. ASK_WALL_MAX_RATIO → float range [0.1, 5.0]
  4. L1_BID_COLLAPSE_RATIO → float range [0.1, 5.0]
  5. HOLDING_TICKS_EXIT → int range [5, 120]  (기존 유지 확인)
"""
from __future__ import annotations

import pytest


def _get_range(name: str):
    """extract_code_params로 상수명에 매핑된 range를 반환한다."""
    from strategy_loop.threshold_optimizer import extract_code_params

    code = f"{name} = 1.0\n\ndef generate_signal(f, p):\n    return None\n"
    params = extract_code_params(code)
    assert name in params, f"'{name}' not found in extracted params"
    lo, hi, log, is_int, _current = params[name]
    return lo, hi, log, is_int


class TestSpreadTicksRange:

    def test_spread_max_ticks_is_int(self):
        lo, hi, log, is_int = _get_range("SPREAD_MAX_TICKS")
        assert is_int is True

    def test_spread_max_ticks_range(self):
        lo, hi, log, is_int = _get_range("SPREAD_MAX_TICKS")
        assert lo == pytest.approx(1.0)
        assert hi == pytest.approx(10.0)

    def test_ticks_spread_max_is_int(self):
        lo, hi, log, is_int = _get_range("TICKS_SPREAD_MAX")
        assert is_int is True

    def test_ticks_spread_max_range(self):
        lo, hi, log, is_int = _get_range("TICKS_SPREAD_MAX")
        assert lo == pytest.approx(1.0)
        assert hi == pytest.approx(10.0)


class TestRatioFactorRange:

    def test_ask_wall_max_ratio_is_float(self):
        lo, hi, log, is_int = _get_range("ASK_WALL_MAX_RATIO")
        assert is_int is False

    def test_ask_wall_max_ratio_range(self):
        lo, hi, log, is_int = _get_range("ASK_WALL_MAX_RATIO")
        assert lo == pytest.approx(0.1)
        assert hi == pytest.approx(5.0)

    def test_l1_bid_collapse_ratio_range(self):
        lo, hi, log, is_int = _get_range("L1_BID_COLLAPSE_RATIO")
        assert lo == pytest.approx(0.1)
        assert hi == pytest.approx(5.0)

    def test_multiplier_range(self):
        lo, hi, log, is_int = _get_range("DEPTH_MULTIPLIER")
        assert lo == pytest.approx(0.1)
        assert hi == pytest.approx(5.0)
        assert is_int is False

    def test_factor_range(self):
        lo, hi, log, is_int = _get_range("WALL_FACTOR")
        assert lo == pytest.approx(0.1)
        assert hi == pytest.approx(5.0)


class TestExistingRangesPreserved:

    def test_holding_ticks_still_int_5_120(self):
        lo, hi, log, is_int = _get_range("HOLDING_TICKS_EXIT")
        assert is_int is True
        assert lo == pytest.approx(5.0)
        assert hi == pytest.approx(120.0)

    def test_imbalance_range_unchanged(self):
        lo, hi, log, is_int = _get_range("ORDER_IMBALANCE_THRESHOLD")
        assert lo == pytest.approx(-0.9)
        assert hi == pytest.approx(0.9)
        assert is_int is False

    def test_spread_bps_range_unchanged(self):
        lo, hi, log, is_int = _get_range("SPREAD_MAX_BPS")
        assert lo == pytest.approx(1.0)
        assert hi == pytest.approx(200.0)
        assert is_int is False
