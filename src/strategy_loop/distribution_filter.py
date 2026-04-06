"""
strategy_loop/distribution_filter.py
--------------------------------------
Pre-backtest distribution filter (code-only).

백테스트 실행 전, generate_signal()이 현실적인 빈도로 진입 신호를 내는지 검증한다.
- 너무 희귀한 조건 (entry_frequency < MIN_ENTRY_FREQ): 사실상 시그널 없음 → 스킵
- 너무 자주 발생하는 조건 (entry_frequency > MAX_ENTRY_FREQ): 거의 항상 진입 → 스킵
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from strategy_block.strategy_compiler.v2.features import extract_builtin_features

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState

# Entry frequency thresholds (inclusive bounds)
MIN_ENTRY_FREQ: float = 0.001   # at least 1 signal per 1000 states (~83 /day at 83k states/day)
MAX_ENTRY_FREQ: float = 0.50    # at most 50% of states — beyond that it's basically always-on

# Number of states to sample for the check (first N, for reproducibility)
SAMPLE_SIZE: int = 2000


@dataclass
class FilterResult:
    passed: bool
    entry_frequency: float
    reason: str = ""


class DistributionFilterError(Exception):
    """Raised when the pre-backtest distribution filter rejects the strategy code."""

    def __init__(self, reason: str, entry_frequency: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.entry_frequency = entry_frequency


def check_code_entry_frequency(
    code: str,
    states: "list[MarketState]",
    sample_size: int = SAMPLE_SIZE,
    min_freq: float = MIN_ENTRY_FREQ,
    max_freq: float = MAX_ENTRY_FREQ,
) -> FilterResult:
    """Python 코드 전략의 진입 빈도를 샘플로 검증한다.

    코드를 sandbox에서 실행 후, generate_signal(features, position)==1 이 되는
    빈도를 측정한다 (포지션 미보유 상태로 고정).

    Args:
        code: LLM이 생성한 Python 전략 코드
        states: 검증에 사용할 MarketState 목록
        sample_size: 샘플 크기 (첫 N개 상태)
        min_freq: 최소 허용 빈도 (inclusive)
        max_freq: 최대 허용 빈도 (inclusive)

    Returns:
        FilterResult — passed=False면 DistributionFilterError를 raise할 것
    """
    from strategy_loop.code_sandbox import CodeSandboxError, exec_strategy_code

    # 코드를 sandbox에서 실행해 generate_signal 추출
    try:
        ns = exec_strategy_code(code)
    except CodeSandboxError as exc:
        return FilterResult(
            passed=False,
            entry_frequency=0.0,
            reason=f"code_exec_error: {exc}",
        )

    generate_signal = ns.get("generate_signal")
    if generate_signal is None or not callable(generate_signal):
        return FilterResult(
            passed=False,
            entry_frequency=0.0,
            reason="code has no callable generate_signal function",
        )

    n = min(sample_size, len(states))
    if n == 0:
        return FilterResult(passed=False, entry_frequency=0.0, reason="no_states")

    sample = states[:n]
    base_position = {"holding_ticks": 0.0, "in_position": False, "position_side": ""}

    n_signals = 0
    for idx, s in enumerate(sample):
        features = extract_builtin_features(s)
        try:
            result = generate_signal(features, dict(base_position))
        except Exception as exc:
            return FilterResult(
                passed=False,
                entry_frequency=0.0,
                reason=(
                    "code_runtime_error: generate_signal raised at "
                    f"sample_idx={idx} ({type(exc).__name__}: {exc})"
                ),
            )
        if result == 1:
            n_signals += 1

    freq = n_signals / n

    if freq < min_freq:
        return FilterResult(
            passed=False,
            entry_frequency=freq,
            reason=(
                f"entry_too_sparse: freq={freq:.5f} < {min_freq} "
                f"({n_signals}/{n} states triggered entry)"
            ),
        )
    if freq > max_freq:
        return FilterResult(
            passed=False,
            entry_frequency=freq,
            reason=(
                f"entry_too_frequent: freq={freq:.4f} > {max_freq} "
                f"({n_signals}/{n} states triggered entry)"
            ),
        )
    return FilterResult(passed=True, entry_frequency=freq, reason="ok")
