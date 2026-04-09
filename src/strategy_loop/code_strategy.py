"""
strategy_loop/code_strategy.py
---------------------------------
CodeStrategy: LLM이 생성한 Python 코드를 Strategy 인터페이스로 래핑.

생성된 코드가 정의해야 하는 인터페이스:
  - UPPER_CASE 모듈 수준 상수 (Optuna 최적화 대상)
  - generate_signal(features: dict, position: dict) -> int | None

generate_signal 반환값:
  1    — long 진입 (in_position=False 일 때만 유효)
  -1   — 포지션 청산 (in_position=True 일 때만 유효)
  None — 아무 액션 없음 (현 상태 유지)

position dict:
  "holding_ticks" : float  — 현재 트레이드 진입 후 틱 수 (미보유 시 0)
  "in_position"   : bool   — 현재 포지션 보유 여부
  "position_side" : str    — "long" 또는 ""

예시 생성 코드:
    ORDER_IMBALANCE_THRESHOLD = 0.30
    HOLDING_TICKS_EXIT = 20
    SPREAD_MAX_BPS = 50.0

    def generate_signal(features, position):
        if position["in_position"]:
            if position["holding_ticks"] >= HOLDING_TICKS_EXIT:
                return -1
            return None
        oi = features.get("order_imbalance", 0.0)
        spread = features.get("spread_bps", 999.0)
        if oi > ORDER_IMBALANCE_THRESHOLD and spread < SPREAD_MAX_BPS:
            return 1
        return None
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import logging

from strategy_block.strategy.base import Strategy
from strategy_block.strategy_compiler.v2.features import extract_builtin_features
from strategy_loop.code_sandbox import CodeSandboxError, exec_strategy_code

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer1_signal import Signal


class CodeStrategy(Strategy):
    """LLM이 생성한 Python 코드 문자열을 실행하는 Strategy 구현체."""

    def __init__(
        self,
        code: str,
        name: str = "code_strategy",
        *,
        tick_size: float = 1.0,
    ) -> None:
        """
        Args:
            code: exec 가능한 Python 전략 코드 문자열
            name: 전략 이름 (로그/저장에 사용)
            tick_size: 종목 틱 크기 (매 tick feature dict에 주입됨). 기본값 1.0.

        Raises:
            CodeSandboxError: AST 검증 실패 또는 실행 오류
            ValueError: generate_signal 함수가 없는 경우
        """
        self._code = code
        self._name = name
        self._tick_size: float = float(tick_size)
        self._in_position: bool = False
        self._position_side: str | None = None
        self._holding_ticks: int = 0
        self._error_count: int = 0          # 런타임 오류 누적 수

        # 최초 exec — generate_signal 존재 여부 확인
        self._ns: dict = exec_strategy_code(code)
        if "generate_signal" not in self._ns:
            raise ValueError(
                "Generated code must define a 'generate_signal(features, position)' function"
            )

    @property
    def name(self) -> str:
        return self._name

    def reset(self) -> None:
        """포지션 상태 초기화 + 모듈 수준 가변 상태 리셋."""
        self._in_position = False
        self._position_side = None
        self._holding_ticks = 0
        self._error_count = 0
        # 코드를 재실행해 모듈 수준 가변 상태(리스트 버퍼 등)를 초기화
        self._ns = exec_strategy_code(self._code)

    def generate_signal(self, state: "MarketState") -> "Signal | None":
        from execution_planning.layer1_signal import Signal

        features = extract_builtin_features(state, tick_size=self._tick_size)
        position = {
            "holding_ticks": float(self._holding_ticks),
            "in_position": self._in_position,
            "position_side": self._position_side or "",
        }

        try:
            result = self._ns["generate_signal"](features, position)
        except Exception as exc:
            self._error_count += 1
            _MAX_LOGGED = 5
            if self._error_count <= _MAX_LOGGED:
                logger.warning(
                    "code_runtime_error: CodeStrategy '%s' generate_signal failed #%d (%s: %s)%s",
                    self._name,
                    self._error_count,
                    type(exc).__name__,
                    exc,
                    " [further errors suppressed]" if self._error_count == _MAX_LOGGED else "",
                    exc_info=self._error_count == 1,
                )
            if self._in_position:
                self._holding_ticks += 1
            return None

        if result is None:
            if self._in_position:
                self._holding_ticks += 1
            return None

        action = int(result)

        if action == 1 and not self._in_position:
            # Long 진입
            self._in_position = True
            self._position_side = "long"
            self._holding_ticks = 0
            return Signal(
                timestamp=state.timestamp,
                symbol=state.symbol,
                score=1.0,
                expected_return=0.0,
                confidence=1.0,
                horizon_steps=1,
                tags={"action": "entry", "side": "long"},
            )

        if action == -1 and self._in_position:
            # 청산
            close_score = -1.0 if self._position_side == "long" else 1.0
            self._in_position = False
            self._position_side = None
            self._holding_ticks = 0
            return Signal(
                timestamp=state.timestamp,
                symbol=state.symbol,
                score=close_score,
                expected_return=0.0,
                confidence=1.0,
                horizon_steps=1,
                tags={"action": "exit"},
            )

        # 유효하지 않은 액션 (예: 이미 in_position인데 1 반환)은 무시
        if self._in_position:
            self._holding_ticks += 1
        return None
