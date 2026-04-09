from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from strategy_loop.date_ranges import DateRanges
from strategy_loop.feedback_controller import (
    compute_controller_decision,
    compute_derived_metrics,
)
from strategy_loop.feedback_generator import FeedbackGenerator
from strategy_loop.loop_runner import LoopRunner
from strategy_loop.prompt_builder import (
    build_code_feedback_messages,
    build_code_generation_messages,
)


def _summary(**overrides: Any) -> dict[str, Any]:
    base = {
        "signal_count": 10,
        "n_states": 1000,
        "n_fills": 10,
        "avg_holding_period": 20,
        "net_pnl": 10.0,
        "total_realized_pnl": 60.0,
        "total_unrealized_pnl": 40.0,
        "total_commission": 10.0,
        "total_slippage": 5.0,
        "total_impact": 2.0,
    }
    base.update(overrides)
    return base


def test_derived_metrics_gross_positive_but_net_negative() -> None:
    summary = _summary(
        total_realized_pnl=100.0,
        total_unrealized_pnl=20.0,
        total_commission=80.0,
        total_slippage=50.0,
        total_impact=20.0,
        net_pnl=-30.0,
        signal_count=40,
        n_states=1000,
    )

    metrics = compute_derived_metrics(summary)

    assert metrics["gross_pnl_before_explicit_fees"] == pytest.approx(120.0)
    assert metrics["estimated_total_cost"] == pytest.approx(150.0)
    assert metrics["fee_drain_ratio"] == pytest.approx(1.25)
    assert metrics["entry_frequency"] == pytest.approx(0.04)
    assert metrics["net_pnl"] == pytest.approx(-30.0)


def test_diagnosis_order_exit_too_short_before_signal_negative_before_cost() -> None:
    summary = _summary(
        avg_holding_period=7.0,
        total_realized_pnl=-8.0,
        total_unrealized_pnl=-2.0,
        net_pnl=-12.0,
    )

    decision = compute_controller_decision(compute_derived_metrics(summary))

    assert decision["diagnosis_code"] == "exit_too_short"
    assert decision["severity"] == "parametric"


def test_no_trades_vs_no_fills_after_signal() -> None:
    no_trades = compute_controller_decision(
        compute_derived_metrics(_summary(signal_count=0, n_fills=0))
    )
    no_fills = compute_controller_decision(
        compute_derived_metrics(_summary(signal_count=5, n_fills=0))
    )

    assert no_trades["diagnosis_code"] == "no_trades"
    assert no_fills["diagnosis_code"] == "no_fills_after_signal"


def test_fee_dominated_structural_vs_parametric() -> None:
    structural = compute_controller_decision(
        compute_derived_metrics(
            _summary(
                total_realized_pnl=70.0,
                total_unrealized_pnl=30.0,
                total_commission=80.0,
                total_slippage=35.0,
                total_impact=15.0,
                avg_holding_period=25,
                signal_count=10,
                n_states=1000,
            )
        )
    )
    parametric = compute_controller_decision(
        compute_derived_metrics(
            _summary(
                total_realized_pnl=70.0,
                total_unrealized_pnl=30.0,
                total_commission=80.0,
                total_slippage=35.0,
                total_impact=15.0,
                avg_holding_period=12,
                signal_count=10,
                n_states=1000,
            )
        )
    )

    assert structural["diagnosis_code"] == "fee_dominated"
    assert structural["severity"] == "structural"
    assert parametric["diagnosis_code"] == "fee_dominated"
    assert parametric["severity"] == "parametric"


def test_control_mode_mapping() -> None:
    structural = compute_controller_decision(
        compute_derived_metrics(
            _summary(total_realized_pnl=-10.0, total_unrealized_pnl=-1.0, net_pnl=-20.0)
        )
    )
    parametric = compute_controller_decision(
        compute_derived_metrics(_summary(signal_count=0, n_fills=0))
    )
    inconclusive = compute_controller_decision(
        compute_derived_metrics(
            _summary(
                total_realized_pnl=40.0,
                total_unrealized_pnl=20.0,
                total_commission=10.0,
                total_slippage=5.0,
                total_impact=2.0,
                avg_holding_period=18,
                signal_count=10,
                n_states=1000,
                net_pnl=5.0,
            )
        )
    )

    assert structural["control_mode"] == "explore"
    assert parametric["control_mode"] == "repair"
    assert inconclusive["control_mode"] == "neutral"


def test_feedback_prompt_contract_includes_authoritative_context() -> None:
    summary = _summary()
    derived_metrics = compute_derived_metrics(summary)
    controller_decision = compute_controller_decision(derived_metrics)

    messages = build_code_feedback_messages(
        code="def generate_signal(features, position):\n    return None",
        backtest_summary=summary,
        derived_metrics=derived_metrics,
        controller_decision=controller_decision,
    )

    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    assert "diagnosis explanation only, not control-flow selection" in system_prompt
    assert "never include: verdict" in system_prompt
    assert "Derived metrics (authoritative)" in user_prompt
    assert "Controller decision (authoritative)" in user_prompt
    assert "precomputed and authoritative. Do not recompute" in user_prompt


def test_generation_previous_feedback_uses_control_mode_not_next_archetype() -> None:
    messages = build_code_generation_messages(
        research_goal="test goal",
        previous_feedback={
            "primary_issue": "Need broader logic change",
            "diagnosis_code": "signal_negative_before_cost",
            "verdict": "fail",
            "control_mode": "explore",
            "next_archetype": "Archetype 2: old-style instruction that should be ignored",
            "issues": ["gross pnl is negative"],
            "suggestions": ["change feature family"],
        },
    )

    user_prompt = messages[1]["content"]
    assert "control_mode=explore" in user_prompt
    assert "Do NOT just tune constants. Change the primary logic family and feature combination." in user_prompt
    assert "Archetype 2: old-style instruction that should be ignored" not in user_prompt


class _NarrativeOnlyClient:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def chat_json(self, messages: list[dict[str, Any]]) -> Any:
        return self._payload


def test_feedback_generator_merges_controller_and_narrative_safely() -> None:
    summary = _summary(signal_count=0, n_fills=0, net_pnl=0.0)
    client = _NarrativeOnlyClient(
        {
            "primary_issue": "No entries were emitted.",
            "issues": ["entry condition too strict"],
            # evidence, suggestions intentionally omitted
        }
    )
    generator = FeedbackGenerator(client=client)  # type: ignore[arg-type]

    feedback = generator.generate(code="def generate_signal(features, position):\n    return None", backtest_summary=summary)

    assert feedback["diagnosis_code"] == "no_trades"
    assert feedback["control_mode"] == "repair"
    assert feedback["verdict"] == "retry"
    assert isinstance(feedback["controller_reasons"], list)
    assert "derived_metrics" in feedback
    assert feedback["primary_issue"] == "No entries were emitted."
    assert feedback["issues"] == ["entry condition too strict"]
    assert feedback["suggestions"] == []
    assert feedback["evidence"] == []


def test_feedback_generator_ignores_llm_control_fields() -> None:
    summary = _summary(signal_count=0, n_fills=0, net_pnl=0.0)
    client = _NarrativeOnlyClient(
        {
            "evidence": ["LLM claims this should pass"],
            "primary_issue": "LLM narrative",
            "issues": [],
            "suggestions": [],
            "verdict": "pass",
            "control_mode": "neutral",
            "diagnosis_code": "inconclusive",
        }
    )
    generator = FeedbackGenerator(client=client)  # type: ignore[arg-type]

    feedback = generator.generate(code="def generate_signal(features, position):\n    return None", backtest_summary=summary)

    assert feedback["verdict"] == "retry"
    assert feedback["control_mode"] == "repair"
    assert feedback["diagnosis_code"] == "no_trades"


class _ConflictingLoopClient:
    def chat_code(self, messages: list[dict[str, Any]]) -> str:
        return (
            "HOLDING_TICKS_EXIT = 5\n"
            "def generate_signal(features, position):\n"
            "    if position.get('in_position', False):\n"
            "        if position.get('holding_ticks', 0) >= HOLDING_TICKS_EXIT:\n"
            "            return -1\n"
            "        return None\n"
            "    return 1\n"
        )

    def chat_json(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "evidence": ["Narrative says this should pass."],
            "primary_issue": "Narrative is optimistic",
            "issues": ["none"],
            "suggestions": ["ship it"],
            "verdict": "pass",
            "control_mode": "neutral",
        }


def test_loop_runner_uses_controller_verdict_and_control_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_backtest(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], list]:
        return _summary(
            signal_count=0,
            n_fills=0,
            avg_holding_period=0,
            net_pnl=0.0,
            total_realized_pnl=0.0,
            total_unrealized_pnl=0.0,
            total_commission=0.0,
            total_slippage=0.0,
            total_impact=0.0,
        ), []

    monkeypatch.setattr(LoopRunner, "_run_backtest_multi_code", _fake_backtest)

    runner = LoopRunner(
        client=_ConflictingLoopClient(),  # type: ignore[arg-type]
        memory_dir=tmp_path / "memory",
        output_dir=tmp_path / "outputs",
        optimize_n_trials=0,
    )
    result = runner.run(
        research_goal="imbalance momentum",
        n_iterations=1,
        data_dir=tmp_path,
        symbols=["005930"],
        date_ranges=DateRanges.from_single_day("20260102"),
        cfg={},
    )

    assert result.verdict == "no_pass"
    assert len(result.iterations) == 1
    feedback = result.iterations[0].feedback
    assert feedback is not None
    assert feedback["verdict"] == "retry"
    assert feedback["control_mode"] == "repair"
    assert feedback["diagnosis_code"] == "no_trades"

