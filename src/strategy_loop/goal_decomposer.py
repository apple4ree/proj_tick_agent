"""
strategy_loop/goal_decomposer.py
----------------------------------
Research goal → 구조화된 GoalDecomposition.

사용자가 입력한 자유 형식 research goal을 분석해서:
  - 타겟 아키타입 (1-4) 선택
  - 관련 피처 목록 추천
  - 경제적 근거 (rationale) 문장 생성

이 정보는 build_code_generation_messages() 에 주입되어 LLM이
매 iteration 마다 일관된 방향으로 전략을 생성하도록 안내한다.

규칙 기반 키워드 매칭 방식 — LLM 호출 없음.
매칭이 안 될 경우 'free-form' 모드로 LLM에게 아키타입 선택을 위임.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class GoalDecomposition:
    archetype: int | None               # 1-4, None = not specified
    archetype_name: str                 # e.g. "liquidity imbalance continuation"
    suggested_features: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_prompt_section(self) -> str:
        """Render as a plain-text block for injection into the generation prompt."""
        lines = []
        if self.archetype is not None:
            lines.append(
                f"Target archetype: {self.archetype} — {self.archetype_name}"
            )
        if self.suggested_features:
            lines.append(
                "Suggested entry features: " + ", ".join(self.suggested_features)
            )
        if self.rationale:
            lines.append(f"Rationale: {self.rationale}")
        return "\n".join(lines)


# ── keyword → archetype mapping ────────────────────────────────────────────

_ARCHETYPE_KEYWORDS: list[tuple[int, list[str]]] = [
    # archetype 1: liquidity imbalance continuation / momentum
    (1, [
        "imbalance momentum", "order imbalance momentum", "imbalance continuation",
        "liquidity imbalance", "buy pressure", "order book momentum",
        "depth momentum", "bid ask imbalance",
    ]),
    # archetype 2: dislocation reversion / mean reversion
    (2, [
        "mean reversion", "spread reversion", "dislocation", "spread mean reversion",
        "spread widening", "spread normalisation", "spread normalization",
        "reversion", "revert",
    ]),
    # archetype 3: exhaustion reversal
    (3, [
        "exhaustion", "exhaustion reversal", "flow exhaustion",
        "trade flow exhaustion", "one-sided flow", "reversal",
        "momentum reversal", "overbought", "oversold",
    ]),
    # archetype 4: depth + flow momentum
    (4, [
        "depth flow", "depth momentum", "flow momentum",
        "depth skew momentum", "depth flow confirmation",
        "volume momentum", "flow confirmation",
    ]),
]

_ARCHETYPE_NAMES: dict[int, str] = {
    1: "liquidity imbalance continuation",
    2: "temporary dislocation reversion",
    3: "trade-flow exhaustion reversal",
    4: "depth skew + flow confirmation momentum",
}

# Per-archetype feature suggestions (ordered by relevance)
_ARCHETYPE_FEATURES: dict[int, list[str]] = {
    1: [
        "order_imbalance", "order_imbalance_ema",
        "trade_flow_imbalance", "order_imbalance_delta",
        "spread_bps",
    ],
    2: [
        "spread_bps", "spread_bps_ema",
        "order_imbalance_delta", "depth_imbalance_ema",
        "price_impact_buy_bps",
    ],
    3: [
        "trade_flow_imbalance", "trade_flow_imbalance_ema",
        "order_imbalance_delta", "depth_imbalance",
        "volume_surprise",
    ],
    4: [
        "depth_imbalance", "depth_imbalance_ema",
        "trade_flow_imbalance", "trade_flow_imbalance_ema",
        "price_impact_buy_bps",
    ],
}

_ARCHETYPE_RATIONALE: dict[int, str] = {
    1: (
        "Exploit sustained buy-side order book pressure confirmed by trade flow. "
        "Enter when imbalance persists above threshold; exit when imbalance reverses or after time limit."
    ),
    2: (
        "Enter after a brief spread/imbalance shock and capture the reversion to equilibrium. "
        "Requires conservative exits and a wide enough spread to cover round-trip costs."
    ),
    3: (
        "Detect one-sided aggressive trade flow that is weakening — "
        "a likely reversal when depth or micro_price fails to confirm further momentum."
    ),
    4: (
        "Align depth skew and trade flow direction as dual confirmation before entry. "
        "Avoid entries when price impact already implies poor execution quality."
    ),
}


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def decompose(research_goal: str) -> GoalDecomposition:
    """Map a free-form research goal string to a GoalDecomposition.

    Returns a GoalDecomposition with archetype=None if no keyword matches —
    in that case the LLM is free to choose any archetype.
    """
    norm = _normalise(research_goal)

    best_archetype: int | None = None
    best_score: int = 0

    for archetype, keywords in _ARCHETYPE_KEYWORDS:
        for kw in keywords:
            if kw in norm:
                # longer matches are more specific — prefer them
                score = len(kw)
                if score > best_score:
                    best_score = score
                    best_archetype = archetype

    if best_archetype is None:
        # No match — return an open decomposition
        return GoalDecomposition(
            archetype=None,
            archetype_name="(LLM choice — no specific archetype inferred from goal)",
            suggested_features=[],
            rationale=(
                "No archetype was inferred from the research goal. "
                "Choose the most economically plausible archetype from the system prompt."
            ),
        )

    return GoalDecomposition(
        archetype=best_archetype,
        archetype_name=_ARCHETYPE_NAMES[best_archetype],
        suggested_features=_ARCHETYPE_FEATURES[best_archetype],
        rationale=_ARCHETYPE_RATIONALE[best_archetype],
    )
