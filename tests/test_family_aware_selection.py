from __future__ import annotations

import json
from pathlib import Path

from evaluation_orchestration.layer6_evaluator.selection_metrics import SelectionMetrics, SelectionScore
from evaluation_orchestration.layer7_validation.walk_forward.harness import WalkForwardRunResult
from evaluation_orchestration.layer7_validation.walk_forward.report import WalkForwardReportBuilder
from evaluation_orchestration.layer7_validation.walk_forward.selector import WalkForwardSelector
from evaluation_orchestration.layer7_validation.walk_forward.window_plan import WalkForwardWindow


def _summary() -> dict:
    return {
        "net_pnl": 12000.0,
        "total_commission": 300.0,
        "total_slippage": 150.0,
        "total_impact": 80.0,
        "parent_order_count": 20.0,
        "child_order_count": 40.0,
        "cancel_rate": 0.4,
        "maker_fill_ratio": 0.35,
        "n_fills": 30.0,
    }


def _diagnostics() -> dict:
    return {
        "lifecycle": {
            "parent_order_count": 20.0,
            "child_order_count": 40.0,
            "children_per_parent": 2.0,
            "cancel_rate": 0.4,
            "n_fills": 30.0,
        },
        "queue": {
            "maker_fill_ratio": 0.35,
            "queue_blocked_count": 5.0,
            "blocked_miss_count": 2.0,
            "queue_ready_count": 12.0,
        },
        "cancel_reasons": {
            "shares": {
                "adverse_selection": 0.2,
            },
        },
    }


def _window(idx: int) -> WalkForwardWindow:
    day = f"202603{10 + idx:02d}"
    return WalkForwardWindow(
        train_start=day,
        train_end=day,
        select_start=day,
        select_end=day,
        holdout_start=day,
        holdout_end=day,
        forward_start=day,
        forward_end=day,
    )


def _result(idx: int, score: float, *, flags: dict | None = None, pre_context: float | None = None) -> WalkForwardRunResult:
    pre_context_total = score if pre_context is None else pre_context
    metadata = {
        "valid": True,
        "flags": dict(flags or {}),
        "window_index": idx,
        "pre_context_total_score": pre_context_total,
        "context_penalty_total": pre_context_total - score,
    }
    return WalkForwardRunResult(
        trial_id="trial-pr5",
        window=_window(idx),
        run_dir=f"/tmp/run-{idx}",
        summary={},
        diagnostics={},
        selection_score=SelectionScore(
            total_score=score,
            components={"edge_net_pnl": score},
            penalties={},
            metadata=metadata,
        ),
    )


def _family_context(
    *,
    family_trials: int = 2,
    active_trials: int = 1,
    same_family_pass: int = 0,
    family_pass_rate: float | None = 0.5,
    duplicate_match_type: str = "none",
    duplicate_score: float = 0.0,
) -> dict:
    duplicate_lookup = {}
    if duplicate_match_type != "none":
        duplicate_lookup = {
            "match_type": duplicate_match_type,
            "family_id": "fam-neighbor",
            "similarity": duplicate_score,
            "member_trial_ids": ["trial-old-1", "trial-old-2"],
            "member_count": 2,
            "representative_trial_id": "trial-old-1",
            "tags": [duplicate_match_type],
        }
    return {
        "family_id": "fam-pr5",
        "trial_count_for_family": family_trials,
        "active_trial_count_for_family": active_trials,
        "global_trial_count": 12,
        "same_family_pass_candidate_count": same_family_pass,
        "family_pass_rate": family_pass_rate,
        "duplicate_match_type": duplicate_match_type,
        "duplicate_neighbor_score": duplicate_score,
        "duplicate_neighbor_lookup": duplicate_lookup,
        "family_summary": {
            "family_id": "fam-pr5",
            "family_trial_count": family_trials,
            "family_active_count": active_trials,
            "family_reject_count": max(0, family_trials - active_trials),
            "family_pass_candidate_count": same_family_pass,
            "family_pass_rate": family_pass_rate,
        },
        "trial_accounting_snapshot": {
            "total_trials": 12,
            "active_trials": 8,
            "rejected_trials": 4,
            "family_trial_counts": {"fam-pr5": family_trials},
            "family_active_counts": {"fam-pr5": active_trials},
            "stage_counts": {"WF_PASSED": same_family_pass},
            "reject_reason_counts": {"REJECTED_WALK_FORWARD": 1},
        },
        "context_errors": [],
    }


def _selector_cfg() -> dict:
    return {
        "selection": {
            "family_penalty": {
                "enabled": True,
                "weight": 0.25,
                "soft_family_trial_count": 2,
                "hard_family_trial_count": 4,
            },
            "duplicate_penalty": {
                "enabled": True,
                "weight": 0.8,
                "hard_fail_similarity": 0.95,
            },
            "neighbor_penalty": {
                "enabled": True,
                "weight": 0.2,
            },
            "selector": {
                "min_windows": 2,
                "min_pass_windows": 1,
                "min_window_score": -1.0,
                "min_average_score": 0.0,
                "max_churn_heavy_share": 1.0,
                "max_cost_dominated_share": 1.0,
                "max_adverse_selection_share": 1.0,
                "max_score_std": 10.0,
                "max_same_family_promoted_candidates": 1,
            },
        }
    }


def test_selection_metrics_family_trial_count_low_has_no_penalty() -> None:
    metrics = SelectionMetrics(_selector_cfg())
    base_score = metrics.score_run(_summary(), _diagnostics())
    low_context_score = metrics.score_run(
        _summary(),
        _diagnostics(),
        context=_family_context(family_trials=2, active_trials=1),
    )

    assert low_context_score.total_score == base_score.total_score
    assert low_context_score.penalties["family_crowding"] == 0.0
    assert low_context_score.penalties["excessive_search"] == 0.0
    assert low_context_score.penalties["duplicate_proximity"] == 0.0


def test_selector_high_family_trial_count_reduces_aggregate_score_and_records_audit_reasons() -> None:
    selector = WalkForwardSelector()
    results = [_result(0, 0.4), _result(1, 0.4), _result(2, 0.4)]
    cfg = _selector_cfg()

    baseline = selector.select(results, cfg)
    decision = selector.select(
        results,
        cfg,
        family_context=_family_context(
            family_trials=8,
            active_trials=5,
            same_family_pass=3,
        ),
    )

    assert baseline.passed is True
    assert decision.passed is False
    assert decision.aggregate_score < baseline.aggregate_score
    assert "family_trial_count_too_high" in decision.reasons
    assert "same_family_candidate_pressure" in decision.reasons
    assert "family_trial_count_too_high" in decision.metadata["applied_penalty_reasons"]
    assert "same_family_candidate_pressure" in decision.metadata["applied_penalty_reasons"]


def test_selector_duplicate_hit_can_fail_and_neighbor_hit_is_milder() -> None:
    selector = WalkForwardSelector()
    cfg = _selector_cfg()
    results = [_result(0, 0.6), _result(1, 0.5)]

    baseline = selector.select(results, cfg)
    duplicate = selector.select(
        results,
        cfg,
        family_context=_family_context(
            duplicate_match_type="duplicate",
            duplicate_score=0.99,
        ),
    )
    neighbor = selector.select(
        results,
        cfg,
        family_context=_family_context(
            duplicate_match_type="neighbor",
            duplicate_score=0.8,
        ),
    )

    assert baseline.passed is True
    assert duplicate.passed is False
    assert any("duplicate_candidate_penalty_applied" in reason for reason in duplicate.reasons)
    assert neighbor.passed is True
    assert neighbor.aggregate_score < baseline.aggregate_score
    assert neighbor.aggregate_score > duplicate.aggregate_score


def test_selector_no_family_context_path_preserves_behavior() -> None:
    selector = WalkForwardSelector()
    cfg = _selector_cfg()
    results = [_result(0, 0.2), _result(1, 0.1), _result(2, 0.3)]

    baseline = selector.select(results, cfg)
    empty_context = selector.select(results, cfg, family_context={})

    assert baseline.passed == empty_context.passed
    assert baseline.aggregate_score == empty_context.aggregate_score
    assert baseline.reasons == empty_context.reasons


def test_report_builder_saves_selection_snapshot_artifact(tmp_path: Path) -> None:
    selector = WalkForwardSelector()
    report_builder = WalkForwardReportBuilder()
    cfg = _selector_cfg()
    results = [_result(0, 0.4), _result(1, 0.3)]
    family_context = _family_context(
        family_trials=7,
        active_trials=4,
        same_family_pass=2,
        duplicate_match_type="neighbor",
        duplicate_score=0.82,
    )

    decision = selector.select(results, cfg, family_context=family_context)
    report = report_builder.build(decision, results, family_context=family_context)
    report["trial_id"] = "trial-pr5"
    report["spec_path"] = str(tmp_path / "demo_strategy.json")

    snapshot = report_builder.build_selection_snapshot(report, family_context=family_context)
    saved = report_builder.save(
        str(tmp_path / "walk_forward"),
        report,
        selection_cfg={
            "selection": {
                "selection_snapshot": {
                    "output_root": str(tmp_path / "selection_snapshots"),
                }
            }
        },
        selection_snapshot=snapshot,
    )

    snapshot_path = Path(saved["selection_snapshot_path"])
    assert snapshot_path.exists()

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["trial_accounting_snapshot"]["total_trials"] == 12
    assert payload["duplicate_neighbor_lookup"]["match_type"] == "neighbor"
    assert payload["aggregate_score_summary"]["after_family_penalty"] == decision.aggregate_score
    assert payload["final_selection"]["reasons"] == decision.reasons
