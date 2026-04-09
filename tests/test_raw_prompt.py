"""
tests/test_raw_prompt.py
--------------------------
System prompt에 raw feature 문서가 포함되는지 검증한다.

검증 항목:
  1. tick_size, bid_1_price, ask_10_volume 등이 시스템 프롬프트에 포함된다.
  2. raw price는 절대 threshold 대신 상대식에 쓰라는 규칙이 포함된다.
  3. features.get 패턴 사용 지침이 포함된다.
  4. BUILTIN_FEATURES 기반 $features_list 치환 구조는 여전히 동작한다.
"""
from __future__ import annotations


class TestSystemPromptRawFeatures:

    @staticmethod
    def _get_system_prompt() -> str:
        from strategy_loop.prompt_builder import _CODE_GEN_SYSTEM
        return _CODE_GEN_SYSTEM

    def test_tick_size_mentioned(self):
        prompt = self._get_system_prompt()
        assert "tick_size" in prompt

    def test_bid_price_levels_mentioned(self):
        prompt = self._get_system_prompt()
        assert "bid_1_price" in prompt

    def test_ask_volume_levels_mentioned(self):
        prompt = self._get_system_prompt()
        assert "ask_10_volume" in prompt or "ask_1_volume" in prompt

    def test_no_absolute_threshold_rule(self):
        """절대가격 threshold 금지 문구가 포함되어야 한다."""
        prompt = self._get_system_prompt()
        # 상대식 사용 요구 문구 확인
        assert "relative" in prompt.lower() or "ratio" in prompt.lower() or "spread_ticks" in prompt.lower()

    def test_features_get_pattern_guidance(self):
        """features.get 패턴 사용 지침이 포함되어야 한다."""
        prompt = self._get_system_prompt()
        assert "features.get" in prompt

    def test_spread_ticks_formula_example(self):
        """spread_ticks = ... / tick_size 공식 예시가 포함되어야 한다."""
        prompt = self._get_system_prompt()
        assert "spread_ticks" in prompt

    def test_deeper_level_absent_warning(self):
        """깊은 level이 없을 수 있다는 경고가 포함되어야 한다."""
        prompt = self._get_system_prompt()
        # "may not exist" 또는 "Deeper levels" 류 문구
        lower = prompt.lower()
        assert "may not" in lower or "deeper" in lower

    def test_builtin_features_list_still_substituted(self):
        """기존 BUILTIN_FEATURES 목록이 $features_list로 치환되어 포함되어야 한다."""
        from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES

        prompt = self._get_system_prompt()
        # 치환 미완료 시 리터럴 '$features_list'가 남아 있으면 실패
        assert "$features_list" not in prompt
        # 적어도 하나의 BUILTIN_FEATURES 이름이 포함되어 있어야 함
        assert any(f in prompt for f in BUILTIN_FEATURES)

    def test_spread_ticks_optuna_range_documented(self):
        """SPREAD_TICKS 관련 Optuna range 가이드가 포함되어야 한다."""
        prompt = self._get_system_prompt()
        assert "SPREAD" in prompt and "TICKS" in prompt

    def test_ratio_factor_multiplier_range_documented(self):
        """RATIO/FACTOR/MULTIPLIER range 가이드가 포함되어야 한다."""
        prompt = self._get_system_prompt()
        assert "RATIO" in prompt or "FACTOR" in prompt or "MULTIPLIER" in prompt
