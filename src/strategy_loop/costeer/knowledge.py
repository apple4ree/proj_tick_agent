"""
strategy_loop/costeer/knowledge.py
------------------------------------
CoSTEER 스타일 knowledge 타입.

AlphaAgent의 CoSTEERKnowledge를 tick 백테스트 시스템에 맞게 단순화했다.
- FBWorkspace → StrategyWorkspace (in-memory code string)
- CoSTEERSingleFeedback → 백테스트 verdict dict
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CodeKnowledge:
    """하나의 코드 생성 시도 + 백테스트 피드백 기록.

    MemoryStore (파일 기반)와는 별개로 세션 내에서만 유지된다.
    RagMemoryV1에 저장되어 다음 generation 프롬프트에 주입된다.
    """
    task_name: str           # 전략 이름 (e.g. "order_imbalance_momentum_v1")
    code: str                # 생성된 Python 코드
    verdict: str             # "pass" | "retry" | "fail" | "dist_filter"
    diagnosis_code: str = ""
    net_pnl: float = 0.0
    entry_frequency: float = 0.0
    primary_issue: str = ""
    suggestions: list[str] = field(default_factory=list)

    def get_implementation_and_feedback_str(self) -> str:
        """프롬프트 주입용 문자열 표현."""
        lines = [
            f"--- Strategy: {self.task_name} ---",
            "Code:",
            self.code,
            "---",
            (
                f"Verdict: {self.verdict}  diagnosis={self.diagnosis_code or 'n/a'}  "
                f"net_pnl={self.net_pnl:.1f}  entry_freq={self.entry_frequency:.4f}"
            ),
        ]
        if self.primary_issue:
            lines.append(f"Primary issue: {self.primary_issue}")
        if self.suggestions:
            lines.append("Suggestions:")
            for s in self.suggestions:
                lines.append(f"  - {s}")
        return "\n".join(lines)
