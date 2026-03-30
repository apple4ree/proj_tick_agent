from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from utils.config import build_backtest_constraint_summary, build_backtest_environment_context


def test_build_backtest_environment_context_has_canonical_fields():
    cfg = {
        "backtest": {
            "resample": "500ms",
            "market_data_delay_ms": 200.0,
            "decision_compute_ms": 50.0,
            "latency": {
                "order_submit_ms": 5.0,
                "order_ack_ms": 15.0,
                "cancel_ms": 3.0,
            },
            "exchange": {
                "queue_model": "risk_adverse",
                "queue_position_assumption": 0.5,
            },
        },
    }

    env = build_backtest_environment_context(cfg)

    assert env["resample"] == "500ms"
    assert env["canonical_tick_interval_ms"] == 500.0
    assert env["market_data_delay_ms"] == 200.0
    assert env["decision_compute_ms"] == 50.0
    assert env["effective_delay_ms"] == 250.0

    assert "latency" in env
    assert env["latency"]["order_submit_ms"] == 5.0
    assert env["latency"]["order_ack_ms"] == 15.0
    assert env["latency"]["cancel_ms"] == 3.0
    assert env["latency"]["order_ack_used_for_fill_gating"] is False

    assert "queue" in env
    assert env["queue"]["queue_model"] == "risk_adverse"
    assert env["queue"]["queue_position_assumption"] == 0.5

    assert "semantics" in env
    assert env["semantics"]["submit_latency_gating"] is True
    assert env["semantics"]["cancel_latency_gating"] is True
    assert env["semantics"]["replace_model"] == "minimal_immediate"


def test_build_backtest_constraint_summary_contains_canonical_contract_terms():
    summary = build_backtest_constraint_summary(
        {
            "resample": "500ms",
            "canonical_tick_interval_ms": 500.0,
            "market_data_delay_ms": 200.0,
            "decision_compute_ms": 50.0,
            "effective_delay_ms": 250.0,
            "latency": {
                "order_submit_ms": 5.0,
                "order_ack_ms": 15.0,
                "cancel_ms": 3.0,
                "order_ack_used_for_fill_gating": False,
            },
            "queue": {
                "queue_model": "risk_adverse",
                "queue_position_assumption": 0.5,
            },
            "semantics": {
                "replace_model": "minimal_immediate",
            },
        },
    )

    assert "Backtest constraint summary (canonical)" in summary
    assert "tick = resample step" in summary
    assert "passive fills require queue waiting" in summary
    assert "repricing resets queue position" in summary
    assert "submit/cancel latency compounds churn cost" in summary
    assert "replace is minimal immediate, not staged venue replace" in summary
    assert "low-churn execution is preferred under queue and latency friction" in summary
    assert "short-horizon strategies are more vulnerable to these frictions" in summary


def test_build_backtest_constraint_summary_fallback_without_context():
    summary = build_backtest_constraint_summary(None)
    assert "Backtest constraint summary: not provided" in summary
    assert "Constraint-aware generation/review is degraded" in summary
