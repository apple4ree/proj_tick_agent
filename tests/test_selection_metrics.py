from __future__ import annotations

from evaluation_orchestration.layer6_evaluator.selection_metrics import SelectionMetrics


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
                "timeout": 0.4,
            },
        },
    }


def test_selection_metrics_churn_penalty_reduces_score() -> None:
    metric = SelectionMetrics()
    base_summary = _summary()
    base_diag = _diagnostics()

    low_churn = metric.score_run(base_summary, base_diag)

    high_churn_diag = _diagnostics()
    high_churn_diag["lifecycle"]["children_per_parent"] = 18.0
    high_churn_diag["lifecycle"]["cancel_rate"] = 0.92
    high_churn_diag["lifecycle"]["child_order_count"] = 360.0
    high_churn = metric.score_run(base_summary, high_churn_diag)

    assert high_churn.penalties["churn"] > low_churn.penalties["churn"]
    assert high_churn.total_score < low_churn.total_score


def test_selection_metrics_queue_fragility_penalty_reduces_score() -> None:
    metric = SelectionMetrics()
    base_summary = _summary()
    base_diag = _diagnostics()

    good_queue = metric.score_run(base_summary, base_diag)

    bad_queue_diag = _diagnostics()
    bad_queue_diag["queue"]["maker_fill_ratio"] = 0.01
    bad_queue_diag["queue"]["queue_blocked_count"] = 80.0
    bad_queue_diag["queue"]["blocked_miss_count"] = 50.0
    bad_queue_diag["queue"]["queue_ready_count"] = 0.0
    bad_queue = metric.score_run(base_summary, bad_queue_diag)

    assert bad_queue.penalties["queue_fragility"] > good_queue.penalties["queue_fragility"]
    assert bad_queue.total_score < good_queue.total_score
    assert bad_queue.metadata["flags"]["queue_ineffective"] is True


def test_selection_metrics_adverse_selection_dominance_penalty() -> None:
    metric = SelectionMetrics()
    base_summary = _summary()

    low_adv_diag = _diagnostics()
    low_adv_diag["cancel_reasons"]["shares"]["adverse_selection"] = 0.1
    low_adv = metric.score_run(base_summary, low_adv_diag)

    high_adv_diag = _diagnostics()
    high_adv_diag["cancel_reasons"]["shares"]["adverse_selection"] = 0.85
    high_adv = metric.score_run(base_summary, high_adv_diag)

    assert high_adv.penalties["adverse_selection"] > low_adv.penalties["adverse_selection"]
    assert high_adv.total_score < low_adv.total_score
    assert high_adv.metadata["flags"]["adverse_selection_dominated"] is True


def test_selection_metrics_same_summary_different_diagnostics_changes_score() -> None:
    metric = SelectionMetrics()
    summary = _summary()

    diag_a = _diagnostics()
    diag_b = _diagnostics()
    diag_b["queue"]["blocked_miss_count"] = 100.0

    score_a = metric.score_run(summary, diag_a)
    score_b = metric.score_run(summary, diag_b)

    assert score_a.total_score != score_b.total_score
