from __future__ import annotations

import json

from strategy_block.strategy_review.v2.backtest_feedback import (
    build_backtest_feedback_summary,
    load_backtest_feedback,
)


def _write_feedback_artifacts(run_dir):
    summary = {
        "signal_count": 100.0,
        "parent_order_count": 20.0,
        "child_order_count": 500.0,
        "cancel_rate": 0.95,
        "avg_child_lifetime_seconds": 2.0,
        "maker_fill_ratio": 0.0,
        "net_pnl": -100.0,
        "total_commission": 80.0,
        "total_slippage": 70.0,
        "total_impact": 10.0,
        "resample_interval": "500ms",
        "canonical_tick_interval_ms": 500.0,
        "configured_order_submit_ms": 30.0,
        "configured_cancel_ms": 20.0,
        "queue_model": "prob_queue",
    }
    diagnostics = {
        "lifecycle": {
            "signal_count": 100.0,
            "parent_order_count": 20.0,
            "child_order_count": 500.0,
            "children_per_parent": 25.0,
            "cancel_rate": 0.95,
            "avg_child_lifetime_seconds": 2.0,
            "max_children_per_parent": 200.0,
        },
        "queue": {
            "queue_model": "prob_queue",
            "queue_blocked_count": 100.0,
            "blocked_miss_count": 90.0,
            "queue_ready_count": 5.0,
            "maker_fill_ratio": 0.0,
        },
        "cancel_reasons": {
            "shares": {
                "adverse_selection": 0.8,
                "timeout": 0.1,
                "stale_price": 0.05,
                "max_reprices_reached": 0.05,
                "micro_event_block": 0.0,
            },
        },
        "tick_time": {
            "resample_interval": "500ms",
            "canonical_tick_interval_ms": 500.0,
        },
        "latency": {
            "configured_order_submit_ms": 30.0,
            "configured_cancel_ms": 20.0,
        },
    }

    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "realism_diagnostics.json").write_text(json.dumps(diagnostics), encoding="utf-8")


def test_load_backtest_feedback_extracts_compact_aggregate(tmp_path):
    _write_feedback_artifacts(tmp_path)

    feedback = load_backtest_feedback(tmp_path)

    assert feedback.feedback_available is True

    assert feedback.lifecycle.signal_count == 100.0
    assert feedback.lifecycle.parent_order_count == 20.0
    assert feedback.lifecycle.child_order_count == 500.0
    assert feedback.lifecycle.children_per_parent == 25.0
    assert feedback.lifecycle.cancel_rate == 0.95
    assert feedback.lifecycle.avg_child_lifetime_seconds == 2.0
    assert feedback.lifecycle.max_children_per_parent == 200.0

    assert feedback.queue.queue_model == "prob_queue"
    assert feedback.queue.queue_blocked_count == 100.0
    assert feedback.queue.blocked_miss_count == 90.0
    assert feedback.queue.queue_ready_count == 5.0
    assert feedback.queue.maker_fill_ratio == 0.0

    assert feedback.cancel_reasons.adverse_selection_share == 0.8
    assert feedback.cancel_reasons.timeout_share == 0.1
    assert feedback.cancel_reasons.stale_price_share == 0.05
    assert feedback.cancel_reasons.max_reprices_reached_share == 0.05
    assert feedback.cancel_reasons.micro_event_block_share == 0.0

    assert feedback.cost.net_pnl == -100.0
    assert feedback.cost.total_commission == 80.0
    assert feedback.cost.total_slippage == 70.0
    assert feedback.cost.total_impact == 10.0

    assert feedback.context.resample == "500ms"
    assert feedback.context.canonical_tick_interval_ms == 500.0
    assert feedback.context.configured_order_submit_ms == 30.0
    assert feedback.context.configured_cancel_ms == 20.0

    assert feedback.flags.churn_heavy is True
    assert feedback.flags.queue_ineffective is True
    assert feedback.flags.cost_dominated is True
    assert feedback.flags.adverse_selection_dominated is True


def test_build_backtest_feedback_summary_returns_compact_human_readable_text(tmp_path):
    _write_feedback_artifacts(tmp_path)
    feedback = load_backtest_feedback(tmp_path)

    rendered = build_backtest_feedback_summary(feedback)
    assert "Recent backtest feedback (aggregate-only):" in rendered
    assert "children_per_parent" in rendered
    assert "queue_model=prob_queue" in rendered
    assert "adverse_selection=" in rendered
    assert "derived_flags:" in rendered


def test_build_backtest_feedback_summary_fallback_when_missing_feedback(tmp_path):
    feedback = load_backtest_feedback(tmp_path)
    assert feedback.feedback_available is False

    rendered = build_backtest_feedback_summary(feedback)
    assert (
        "No recent backtest feedback provided; critique spec using static review + "
        "environment context only."
    ) == rendered
