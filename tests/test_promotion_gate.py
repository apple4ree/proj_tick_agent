from __future__ import annotations

from strategy_block.strategy_promotion.promotion_gate import PromotionGate
from strategy_block.strategy_registry.trial_registry import TrialRecord


def _trial(*, family_id: str | None = "fam-1", stage: str = "WF_PASSED", status: str = "ACTIVE") -> TrialRecord:
    return TrialRecord(
        trial_id="trial-01",
        strategy_name="demo",
        strategy_version="2.0",
        source_spec_path="strategies/demo_v2.0.json",
        parent_trial_id=None,
        family_id=family_id,
        stage=stage,
        status=status,
        reject_reason=None,
        metadata={},
    )


def _report(*, passed: bool = True, aggregate_score: float = 0.2, queue_ineffective_flags: tuple[bool, ...] = (False, False)) -> dict:
    return {
        "decision": {
            "passed": passed,
            "aggregate_score": aggregate_score,
            "reasons": [] if passed else ["walk_forward_not_passed"],
            "metadata": {
                "n_valid_windows": 2,
                "n_pass_windows": 2 if passed else 0,
                "churn_heavy_share": 0.2,
                "cost_dominated_share": 0.2,
                "adverse_selection_dominated_share": 0.1,
            },
        },
        "window_results": [
            {"metadata": {"flags": {"queue_ineffective": queue_ineffective_flags[0]}}},
            {"metadata": {"flags": {"queue_ineffective": queue_ineffective_flags[1]}}},
        ],
    }


def _cfg() -> dict:
    return {
        "promotion": {
            "gate": {
                "require_walk_forward_passed": True,
                "min_aggregate_score": 0.0,
                "min_valid_windows": 2,
                "min_forward_survival_ratio": 0.5,
                "max_churn_heavy_share": 0.7,
                "max_cost_dominated_share": 0.8,
                "max_adverse_selection_dominated_share": 0.8,
                "max_queue_ineffective_share": 0.5,
                "require_family_id": True,
                "require_trial_active": True,
            }
        }
    }


def test_promotion_gate_passes_with_valid_inputs() -> None:
    decision = PromotionGate().evaluate(
        trial_record=_trial(),
        walk_forward_report=_report(passed=True, aggregate_score=0.2),
        cfg=_cfg(),
    )
    assert decision.passed is True
    assert decision.reasons == []


def test_promotion_gate_fails_on_walk_forward_and_score() -> None:
    decision = PromotionGate().evaluate(
        trial_record=_trial(),
        walk_forward_report=_report(passed=False, aggregate_score=-0.3),
        cfg=_cfg(),
    )
    assert decision.passed is False
    assert any("walk_forward_not_passed" in reason for reason in decision.reasons)
    assert any("aggregate_score_below_threshold" in reason for reason in decision.reasons)


def test_promotion_gate_fails_on_queue_prevalence_threshold() -> None:
    decision = PromotionGate().evaluate(
        trial_record=_trial(),
        walk_forward_report=_report(passed=True, aggregate_score=0.2, queue_ineffective_flags=(True, True)),
        cfg=_cfg(),
    )
    assert decision.passed is False
    assert "queue_ineffective_share_too_high" in decision.reasons


def test_promotion_gate_requires_family_id_by_default() -> None:
    decision = PromotionGate().evaluate(
        trial_record=_trial(family_id=None),
        walk_forward_report=_report(),
        cfg=_cfg(),
    )
    assert decision.passed is False
    assert "missing_family_id" in decision.reasons


def test_promotion_gate_can_disable_family_requirement_by_config_override() -> None:
    cfg = _cfg()
    cfg["promotion"]["gate"]["require_family_id"] = False
    decision = PromotionGate().evaluate(
        trial_record=_trial(family_id=None),
        walk_forward_report=_report(),
        cfg=cfg,
    )
    assert decision.passed is True
