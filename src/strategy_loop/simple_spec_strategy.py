"""
strategy_loop/simple_spec_strategy.py
---------------------------------------
Strategy implementation backed by a simple JSON spec.

- 진입: entry.condition 이 True 이고 현재 포지션 없을 때 Signal 발생
- 청산: 포지션 보유 중 exit.condition 이 True 이면 청산 Signal 발생
- position 상태(holding_ticks)를 내부적으로 추적
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from strategy_block.strategy.base import Strategy
from strategy_block.strategy_compiler.v2.features import extract_builtin_features
from strategy_loop.spec_simple import evaluate

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer1_signal import Signal


class SimpleSpecStrategy(Strategy):
    """Evaluates a simple JSON spec against MarketState to produce Signals."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec
        self._in_position: bool = False
        self._position_side: str | None = None   # "long" | "short"
        self._holding_ticks: int = 0

    @property
    def name(self) -> str:
        return self._spec.get("name", "simple_spec_strategy")

    def reset(self) -> None:
        self._in_position = False
        self._position_side = None
        self._holding_ticks = 0

    def generate_signal(self, state: "MarketState") -> "Signal | None":
        from execution_planning.layer1_signal import Signal

        features = extract_builtin_features(state)
        position = {"holding_ticks": float(self._holding_ticks)}

        entry_cfg = self._spec["entry"]
        exit_cfg = self._spec["exit"]

        if self._in_position:
            # ── check exit ────────────────────────────────────────────
            if evaluate(exit_cfg["condition"], features, position):
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
            self._holding_ticks += 1
            return None

        # ── check entry ───────────────────────────────────────────────
        if evaluate(entry_cfg["condition"], features, position):
            side = entry_cfg.get("side", "long")
            score = 1.0 if side == "long" else -1.0
            self._in_position = True
            self._position_side = side
            self._holding_ticks = 0
            return Signal(
                timestamp=state.timestamp,
                symbol=state.symbol,
                score=score,
                expected_return=0.0,
                confidence=1.0,
                horizon_steps=1,
                tags={"action": "entry", "side": side},
            )

        return None
