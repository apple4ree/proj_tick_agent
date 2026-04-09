"""
strategy_loop/precode_eval.py
-------------------------------
Pre-code evaluation: scores a StrategySpec before committing to code generation.

Five dimensions (each 0.0–1.0):
  1. feature_validity      — all referenced features exist in BUILTIN_FEATURES
  2. economic_plausibility — has cost-filter feature AND archetype is specified
  3. exit_completeness     — has time-floor exit (>=5 ticks) AND signal-reversal exit
  4. param_optunability    — tunable_params all have UPPER_CASE names and valid ranges
  5. archetype_alignment   — at least one canonical archetype feature is in use

overall = mean of five scores
go      = overall >= GO_THRESHOLD  (default 0.50)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
from strategy_loop.spec_schema import StrategySpec

_GO_THRESHOLD: float = 0.50

# Features that indicate economic awareness (cost awareness)
_COST_FEATURES: frozenset[str] = frozenset({
    "spread_bps", "spread_bps_ema",
    "price_impact_buy_bps", "price_impact_sell_bps",
})

# Canonical features per archetype (mirrors goal_decomposer._ARCHETYPE_FEATURES)
_ARCHETYPE_FEATURES: dict[int, frozenset[str]] = {
    1: frozenset({
        "order_imbalance", "order_imbalance_ema",
        "trade_flow_imbalance", "order_imbalance_delta",
        "spread_bps",
    }),
    2: frozenset({
        "spread_bps", "spread_bps_ema",
        "order_imbalance_delta", "depth_imbalance_ema",
        "price_impact_buy_bps",
    }),
    3: frozenset({
        "trade_flow_imbalance", "trade_flow_imbalance_ema",
        "order_imbalance_delta", "depth_imbalance",
        "volume_surprise",
    }),
    4: frozenset({
        "depth_imbalance", "depth_imbalance_ema",
        "trade_flow_imbalance", "trade_flow_imbalance_ema",
        "price_impact_buy_bps",
    }),
}


@dataclass
class PrecodeEval:
    scores: dict[str, float] = field(default_factory=dict)
    overall: float = 0.0
    go: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "1",
            "scores": dict(self.scores),
            "overall": self.overall,
            "go": self.go,
            "notes": list(self.notes),
        }


def evaluate_spec(spec: StrategySpec) -> PrecodeEval:
    """Score a StrategySpec across five dimensions and return a PrecodeEval."""
    notes: list[str] = []
    scores: dict[str, float] = {}

    eff_features = spec.effective_condition_features()

    # ── 1. feature_validity ───────────────────────────────────────────
    unknown = [f for f in eff_features if f not in BUILTIN_FEATURES]
    if not eff_features:
        scores["feature_validity"] = 0.0
        notes.append("No features referenced")
    elif unknown:
        scores["feature_validity"] = max(0.0, 1.0 - len(unknown) / len(eff_features))
        notes.append(f"Unknown features (not in BUILTIN_FEATURES): {unknown}")
    else:
        scores["feature_validity"] = 1.0

    # ── 2. economic_plausibility ──────────────────────────────────────
    has_cost = bool(eff_features & _COST_FEATURES)
    has_archetype = spec.archetype is not None
    ep = (0.5 if has_archetype else 0.0) + (0.5 if has_cost else 0.0)
    scores["economic_plausibility"] = ep
    if not has_cost:
        notes.append(
            "No cost-filter feature in spec "
            "(add spread_bps or price_impact_buy_bps to entry conditions)"
        )
    if not has_archetype:
        notes.append("archetype is None — LLM will freely choose strategy type")

    # ── 3. exit_completeness ──────────────────────────────────────────
    has_time = spec.exit_time_ticks >= 5
    has_signal = len(spec.exit_signal_conditions) > 0
    ec = (0.5 if has_time else 0.0) + (0.5 if has_signal else 0.0)
    scores["exit_completeness"] = ec
    if not has_time:
        notes.append(
            f"exit_time_ticks={spec.exit_time_ticks} < 5 — "
            "too short to recover round-trip costs"
        )
    if not has_signal:
        notes.append("No signal-reversal exit condition defined")

    # ── 4. param_optunability ─────────────────────────────────────────
    if not spec.tunable_params:
        # Half credit — missing params means no Optuna optimization
        scores["param_optunability"] = 0.5
        notes.append("No tunable_params — code will have no Optuna-optimizable constants")
    else:
        bad = [
            p for p in spec.tunable_params
            if not p.name.isupper() or p.range[0] >= p.range[1]
        ]
        scores["param_optunability"] = max(0.0, 1.0 - len(bad) / len(spec.tunable_params))
        if bad:
            notes.append(
                f"Params with bad names/ranges (Optuna will mishandle): "
                f"{[p.name for p in bad]}"
            )

    # ── 5. archetype_alignment ────────────────────────────────────────
    if spec.archetype is None:
        # Neutral — no alignment check possible
        scores["archetype_alignment"] = 0.5
    else:
        canonical = _ARCHETYPE_FEATURES.get(spec.archetype, frozenset())
        if not canonical:
            scores["archetype_alignment"] = 0.5
        else:
            overlap = eff_features & canonical
            scores["archetype_alignment"] = len(overlap) / len(canonical)
            if not overlap:
                notes.append(
                    f"No canonical archetype-{spec.archetype} features used "
                    f"(expected ≥1 of: {sorted(canonical)})"
                )

    overall = sum(scores.values()) / len(scores) if scores else 0.0
    go = overall >= _GO_THRESHOLD

    return PrecodeEval(scores=scores, overall=round(overall, 4), go=go, notes=notes)
