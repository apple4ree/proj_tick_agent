"""Tests for OpenAI v2 strategy generation pipeline.

Covers:
- Mock mode: plan build → lower → review → spec
- Replay mode: recorded responses → plan → spec
- Live mode boundary: client unavailable → fallback or error
- Fallback policies: allow_template_fallback, fail_on_fallback
- Trace / provenance fields
- Response parser edge cases
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_generation.generator import (
    StaticReviewError,
    StrategyGenerator,
)
from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient
from strategy_block.strategy_generation.v2.lowering import lower_plan_to_spec_v2
from strategy_block.strategy_generation.v2.openai_generation import (
    _build_mock_plan,
    generate_plan_with_openai,
    generate_spec_v2_with_openai,
)
from strategy_block.strategy_generation.v2.schemas.plan_schema import (
    ConditionPlan,
    EntryPlan,
    ExecutionPlan,
    ExitPolicyPlan,
    ExitRulePlan,
    PreconditionPlan,
    RiskPlan,
    StateEventPlan,
    StateGuardPlan,
    StatePlan,
    StateUpdatePlan,
    StateVarPlan,
    StrategyPlan,
)
from strategy_block.strategy_generation.v2.utils.response_parser import (
    PlanParseError,
    parse_plan_response,
    validate_plan,
)
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2
from strategy_block.strategy_generation.v2.utils.prompt_builder import build_user_prompt
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def mock_client() -> OpenAIStrategyGenClient:
    return OpenAIStrategyGenClient(mode="mock")


@pytest.fixture()
def reviewer() -> StrategyReviewerV2:
    return StrategyReviewerV2()


def _make_minimal_plan(
    *,
    name: str = "test_plan",
    goal: str = "test imbalance momentum",
    style: str = "momentum",
) -> StrategyPlan:
    """Build a minimal valid plan for testing."""
    return StrategyPlan(
        name=name,
        description="Minimal test plan",
        research_goal=goal,
        strategy_style=style,
        preconditions=[
            PreconditionPlan(
                name="spread_ok",
                condition=ConditionPlan(feature="spread_bps", op="<", threshold=30.0),
            ),
        ],
        entry_policies=[
            EntryPlan(
                name="long_entry",
                side="long",
                trigger=ConditionPlan(feature="order_imbalance", op=">", threshold=0.3),
                strength=0.6,
                cooldown_ticks=50,
            ),
            EntryPlan(
                name="short_entry",
                side="short",
                trigger=ConditionPlan(feature="order_imbalance", op="<", threshold=-0.3),
                strength=0.6,
                cooldown_ticks=50,
            ),
        ],
        exit_policies=[
            ExitPolicyPlan(
                name="risk_exits",
                rules=[
                    ExitRulePlan(
                        name="stop_loss",
                        priority=1,
                        condition=ConditionPlan(
                            position_attr="unrealized_pnl_bps", op="<=", threshold=-25.0,
                        ),
                        action="close_all",
                    ),
                    ExitRulePlan(
                        name="time_exit",
                        priority=2,
                        condition=ConditionPlan(
                            position_attr="holding_ticks", op=">=", threshold=30.0,
                        ),
                        action="close_all",
                    ),
                ],
            ),
        ],
        risk_policy=RiskPlan(
            max_position=400,
            inventory_cap=800,
            sizing_mode="fixed",
            base_size=100,
            max_size=400,
        ),
        execution_policy=ExecutionPlan(
            placement_mode="passive_join",
            cancel_after_ticks=15,
            max_reprices=2,
        ),
    )


# ── Mock plan builder ────────────────────────────────────────────────


class TestBuildMockPlan:

    @pytest.mark.parametrize("goal,expected_style", [
        ("exploit imbalance momentum", "momentum"),
        ("mean reversion on order flow", "mean_reversion"),
        ("spread absorption fade", "mean_reversion"),
    ])
    def test_mock_plan_style_selection(self, goal: str, expected_style: str):
        plan = _build_mock_plan(goal)
        assert plan.strategy_style == expected_style

    def test_mock_plan_has_required_sections(self):
        plan = _build_mock_plan("test momentum strategy")
        assert plan.name
        assert plan.description
        assert plan.research_goal == "test momentum strategy"
        assert len(plan.entry_policies) >= 2
        assert len(plan.exit_policies) >= 1
        assert plan.risk_policy is not None
        assert len(plan.preconditions) >= 1

    def test_mock_plan_roundtrip_serialization(self):
        plan = _build_mock_plan("roundtrip test")
        data = plan.model_dump()
        restored = StrategyPlan.model_validate(data)
        assert restored.name == plan.name
        assert restored.strategy_style == plan.strategy_style


# ── Plan-based lowering ──────────────────────────────────────────────


class TestPlanLowering:

    @pytest.mark.parametrize("goal", [
        "exploit imbalance momentum",
        "mean reversion on order flow",
        "spread absorption strategy",
    ])
    def test_lower_mock_plan_produces_valid_spec(self, goal: str):
        plan = _build_mock_plan(goal)
        spec = lower_plan_to_spec_v2(plan)
        assert isinstance(spec, StrategySpecV2)
        assert spec.spec_format == "v2"
        errors = spec.validate()
        assert errors == [], f"Validation errors: {errors}"

    def test_lower_preserves_structure(self):
        plan = _build_mock_plan("test structure")
        spec = lower_plan_to_spec_v2(plan)
        assert spec.name == plan.name
        assert len(spec.entry_policies) == len(plan.entry_policies)
        assert len(spec.exit_policies) == len(plan.exit_policies)
        assert len(spec.preconditions) == len(plan.preconditions)

    def test_lower_minimal_plan(self):
        plan = _make_minimal_plan()
        spec = lower_plan_to_spec_v2(plan)
        assert spec.name == "test_plan"
        assert len(spec.entry_policies) == 2
        assert spec.entry_policies[0].side == "long"
        assert spec.entry_policies[1].side == "short"

    @pytest.mark.parametrize("goal", [
        "exploit imbalance momentum",
        "mean reversion on order flow",
        "spread absorption strategy",
    ])
    def test_lowered_plan_passes_review(self, goal: str, reviewer: StrategyReviewerV2):
        plan = _build_mock_plan(goal)
        spec = lower_plan_to_spec_v2(plan)
        result = reviewer.review(spec)
        assert result.passed, (
            f"Review failed for goal={goal!r}: "
            f"{[i.description for i in result.issues if i.severity == 'error']}"
        )

    def test_lowered_plan_serialization_roundtrip(self, tmp_path: Path):
        plan = _build_mock_plan("roundtrip test")
        spec = lower_plan_to_spec_v2(plan)
        path = tmp_path / "spec.json"
        spec.save(path)
        loaded = StrategySpecV2.load(path)
        assert loaded.name == spec.name
        assert len(loaded.entry_policies) == len(spec.entry_policies)


# ── Response parser ──────────────────────────────────────────────────


class TestResponseParser:

    def test_parse_plan_instance_passthrough(self):
        plan = _make_minimal_plan()
        result = parse_plan_response(plan)
        assert result is plan

    def test_parse_dict_response(self):
        plan = _make_minimal_plan()
        data = plan.model_dump()
        result = parse_plan_response(data)
        assert isinstance(result, StrategyPlan)
        assert result.name == plan.name

    def test_parse_json_string(self):
        plan = _make_minimal_plan()
        json_str = plan.model_dump_json()
        result = parse_plan_response(json_str)
        assert isinstance(result, StrategyPlan)
        assert result.name == plan.name

    def test_parse_invalid_json_raises(self):
        with pytest.raises(PlanParseError, match="not valid JSON"):
            parse_plan_response("{broken json")

    def test_parse_invalid_schema_raises(self):
        with pytest.raises(PlanParseError, match="does not match"):
            parse_plan_response({"not_a_valid_field": 123})

    def test_parse_unexpected_type_raises(self):
        with pytest.raises(PlanParseError, match="Unexpected response type"):
            parse_plan_response(42)

    def test_validate_plan_no_entries_warns(self):
        plan = StrategyPlan(
            name="empty",
            description="no entries",
            research_goal="test",
            strategy_style="momentum",
            entry_policies=[],
            exit_policies=[],
        )
        warnings = validate_plan(plan)
        assert any("no entry" in w.lower() for w in warnings)

    def test_validate_plan_no_close_all_warns(self):
        plan = _make_minimal_plan()
        # Replace exit rule action with reduce_position
        plan.exit_policies[0].rules = [
            ExitRulePlan(
                name="partial",
                condition=ConditionPlan(feature="spread_bps", op=">", threshold=50.0),
                action="reduce_position",
                reduce_fraction=0.5,
            ),
        ]
        warnings = validate_plan(plan)
        assert any("close_all" in w for w in warnings)

    def test_validate_plan_short_horizon_missing_execution_policy_warns(self):
        plan = _make_minimal_plan()
        plan.execution_policy = None
        # tighten time exit to short horizon
        plan.exit_policies[0].rules[1].condition.threshold = 10.0
        warnings = validate_plan(plan)
        assert any("Short-horizon plan has no execution_policy" in w for w in warnings)


# ── OpenAI generation module (mock mode) ─────────────────────────────


class TestGeneratePlanWithOpenAI:

    def test_mock_mode_returns_plan(self, mock_client: OpenAIStrategyGenClient):
        plan, trace = generate_plan_with_openai(
            client=mock_client,
            research_goal="test momentum",
        )
        assert isinstance(plan, StrategyPlan)
        assert trace["source"] == "mock"
        assert trace["parse_success"] is True

    def test_mock_mode_trace_includes_execution_policy_flags(self, mock_client: OpenAIStrategyGenClient):
        plan, trace = generate_plan_with_openai(
            client=mock_client,
            research_goal="test momentum",
        )
        assert plan.execution_policy is not None
        assert trace["execution_policy_explicit"] is True
        assert trace["execution_policy_missing_short_horizon"] is False
        assert "inferred_holding_horizon_ticks" in trace

    def test_mock_mode_different_goals(self, mock_client: OpenAIStrategyGenClient):
        plan1, _ = generate_plan_with_openai(
            client=mock_client, research_goal="imbalance momentum"
        )
        plan2, _ = generate_plan_with_openai(
            client=mock_client, research_goal="mean reversion"
        )
        assert plan1.strategy_style == "momentum"
        assert plan2.strategy_style == "mean_reversion"


class TestGenerateSpecV2WithOpenAI:

    def test_mock_mode_produces_spec(
        self,
        mock_client: OpenAIStrategyGenClient,
        reviewer: StrategyReviewerV2,
    ):
        spec, trace = generate_spec_v2_with_openai(
            client=mock_client,
            research_goal="imbalance momentum",
            reviewer=reviewer,
        )
        assert isinstance(spec, StrategySpecV2)
        assert spec.spec_format == "v2"
        assert trace["pipeline"] == "openai_v2_plan_generation"
        assert trace["static_review_passed"] is True

    def test_mock_mode_no_reviewer(self, mock_client: OpenAIStrategyGenClient):
        spec, trace = generate_spec_v2_with_openai(
            client=mock_client,
            research_goal="test without review",
            reviewer=None,
        )
        assert isinstance(spec, StrategySpecV2)
        assert trace["static_review_passed"] is None

    def test_trace_has_plan_info(
        self,
        mock_client: OpenAIStrategyGenClient,
        reviewer: StrategyReviewerV2,
    ):
        _, trace = generate_spec_v2_with_openai(
            client=mock_client,
            research_goal="momentum test",
            reviewer=reviewer,
        )
        assert "plan" in trace
        assert trace["plan"]["strategy_style"] == "momentum"
        assert trace["plan"]["n_entries"] >= 2
        assert "plan_trace" in trace
        assert trace["output"]["spec_format"] == "v2"

        assert "execution_policy_explicit" in trace["plan"]
        assert "inferred_short_horizon" in trace["plan"]
        assert "execution_policy_explicit" in trace["output"]

    def test_metadata_has_generation_source(
        self,
        mock_client: OpenAIStrategyGenClient,
    ):
        spec, _ = generate_spec_v2_with_openai(
            client=mock_client,
            research_goal="metadata test",
        )
        assert spec.metadata["generation_source"] == "openai_v2_plan"
        assert spec.metadata["spec_canonical"] == "v2"
        assert spec.metadata["plan_schema_version"] == "plan_v1"


# ── StrategyGenerator integration (mock mode) ───────────────────────


class TestGeneratorOpenAIBackend:

    def test_mock_mode_end_to_end(self):
        gen = StrategyGenerator(
            backend="openai",
            mode="mock",
            spec_format="v2",
        )
        spec, trace = gen.generate(research_goal="imbalance momentum")
        assert isinstance(spec, StrategySpecV2)
        assert trace["generation_outcome"] == "success"
        assert trace["provenance"]["requested_backend"] == "openai"
        assert trace["provenance"]["effective_backend"] == "openai_v2"
        assert trace["provenance"]["generation_class"] == "openai_v2_plan"

    def test_mock_mode_reversion_goal(self):
        gen = StrategyGenerator(
            backend="openai",
            mode="mock",
            spec_format="v2",
        )
        spec, trace = gen.generate(research_goal="mean reversion on imbalance")
        assert isinstance(spec, StrategySpecV2)
        assert trace["generation_outcome"] == "success"

    def test_mock_mode_spread_goal(self):
        gen = StrategyGenerator(
            backend="openai",
            mode="mock",
            spec_format="v2",
        )
        spec, trace = gen.generate(research_goal="spread fade strategy")
        assert isinstance(spec, StrategySpecV2)
        assert trace["generation_outcome"] == "success"

    def test_mock_mode_trace_includes_backtest_environment(self):
        gen = StrategyGenerator(
            backend="openai",
            mode="mock",
            spec_format="v2",
            backtest_environment={
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
                    "submit_latency_gating": True,
                    "cancel_latency_gating": True,
                    "replace_model": "minimal_immediate",
                },
            },
        )
        _, trace = gen.generate(research_goal="imbalance momentum")
        env = trace["input"]["backtest_environment"]
        assert env["resample"] == "500ms"
        assert env["latency"]["order_submit_ms"] == 5.0
        assert env["queue"]["queue_model"] == "risk_adverse"


# ── Fallback policies ────────────────────────────────────────────────


class TestFallbackPolicies:

    def test_template_fallback_on_openai_failure(self):
        """When openai fails (live mode, no API key), falls back to template."""
        gen = StrategyGenerator(
            backend="openai",
            mode="live",  # no OPENAI_API_KEY → client unavailable → PlanParseError
            spec_format="v2",
            allow_template_fallback=True,
            fail_on_fallback=False,
        )
        spec, trace = gen.generate(research_goal="imbalance momentum")
        assert isinstance(spec, StrategySpecV2)
        assert trace["fallback_used"] is True
        assert trace["generation_outcome"] == "fallback_success"
        assert trace["provenance"]["requested_backend"] == "openai"
        assert trace["provenance"]["effective_backend"] == "template_v2"

    def test_no_fallback_raises(self):
        """When fallback is disabled, openai failure raises StaticReviewError."""
        gen = StrategyGenerator(
            backend="openai",
            mode="live",
            spec_format="v2",
            allow_template_fallback=False,
        )
        with pytest.raises(StaticReviewError, match="fallback is disabled"):
            gen.generate(research_goal="imbalance momentum")

    def test_fail_on_fallback_raises(self):
        """When fail_on_fallback=True, even successful fallback raises."""
        gen = StrategyGenerator(
            backend="openai",
            mode="live",
            spec_format="v2",
            allow_template_fallback=True,
            fail_on_fallback=True,
        )
        with pytest.raises(StaticReviewError, match="fail_on_fallback"):
            gen.generate(research_goal="imbalance momentum")

    def test_template_backend_ignores_fallback_flags(self):
        """Template backend works regardless of fallback flags."""
        gen = StrategyGenerator(
            backend="template",
            mode="live",
            spec_format="v2",
            allow_template_fallback=False,
            fail_on_fallback=True,
        )
        spec, trace = gen.generate(research_goal="imbalance momentum")
        assert isinstance(spec, StrategySpecV2)
        assert trace["fallback_used"] is False
        assert trace["generation_outcome"] == "success"


# ── Trace / provenance verification ─────────────────────────────────


class TestTraceProvenance:

    def test_template_trace_provenance(self):
        gen = StrategyGenerator(backend="template", spec_format="v2")
        _, trace = gen.generate(research_goal="imbalance momentum")
        prov = trace["provenance"]
        assert prov["requested_backend"] == "template"
        assert prov["effective_backend"] == "template_v2"
        assert prov["spec_format"] == "v2"
        assert prov["generation_class"] == "template_v2"

    def test_openai_mock_trace_provenance(self):
        gen = StrategyGenerator(backend="openai", mode="mock", spec_format="v2")
        _, trace = gen.generate(research_goal="imbalance momentum")
        prov = trace["provenance"]
        assert prov["requested_backend"] == "openai"
        assert prov["effective_backend"] == "openai_v2"
        assert prov["generation_class"] == "openai_v2_plan"

    def test_fallback_trace_has_events(self):
        gen = StrategyGenerator(
            backend="openai",
            mode="live",
            spec_format="v2",
            allow_template_fallback=True,
            fail_on_fallback=False,
        )
        _, trace = gen.generate(research_goal="imbalance momentum")
        fb = trace["fallback"]
        assert fb["used"] is True
        assert fb["count"] >= 1
        assert len(fb["events"]) >= 1
        assert fb["events"][0]["type"] == "openai_to_template_fallback"

    def test_trace_has_static_review(self):
        gen = StrategyGenerator(backend="openai", mode="mock", spec_format="v2")
        _, trace = gen.generate(research_goal="imbalance momentum")
        assert "static_review" in trace
        assert trace["static_review_passed"] is True


# ── Replay mode ──────────────────────────────────────────────────────


class TestReplayMode:

    def test_replay_from_file(self, tmp_path: Path):
        """Record a mock plan, save as replay, then replay it."""
        # Build a plan and save as replay log
        plan = _build_mock_plan("replay test momentum")
        replay_log = [{
            "schema": "StrategyPlan",
            "system_prompt": "test",
            "user_prompt": "test",
            "response": plan.model_dump(),
        }]
        replay_path = tmp_path / "replay.json"
        replay_path.write_text(json.dumps(replay_log), encoding="utf-8")

        # Use replay mode
        client = OpenAIStrategyGenClient(mode="replay", replay_path=replay_path)
        assert client.mode == "replay"

        plan_result, trace = generate_plan_with_openai(
            client=client,
            research_goal="replay test momentum",
        )
        assert isinstance(plan_result, StrategyPlan)
        assert trace["source"] == "replay"
        assert trace["parse_success"] is True

    def test_replay_exhausted_falls_back(self, tmp_path: Path):
        """When replay log is exhausted, client returns None → PlanParseError."""
        replay_path = tmp_path / "empty_replay.json"
        replay_path.write_text("[]", encoding="utf-8")

        client = OpenAIStrategyGenClient(mode="replay", replay_path=replay_path)
        with pytest.raises(PlanParseError, match="no response"):
            generate_plan_with_openai(
                client=client,
                research_goal="test",
            )

    def test_generator_replay_with_fallback(self, tmp_path: Path):
        """Generator in replay mode with exhausted log falls back to template."""
        replay_path = tmp_path / "empty.json"
        replay_path.write_text("[]", encoding="utf-8")

        gen = StrategyGenerator(
            backend="openai",
            mode="replay",
            replay_path=str(replay_path),
            spec_format="v2",
            allow_template_fallback=True,
            fail_on_fallback=False,
        )
        spec, trace = gen.generate(research_goal="imbalance momentum")
        assert isinstance(spec, StrategySpecV2)
        assert trace["fallback_used"] is True


# ── Condition plan lowering edge cases ───────────────────────────────


class TestConditionPlanLowering:

    def test_composite_all_condition(self):
        plan = _make_minimal_plan()
        # Replace entry trigger with composite all
        plan.entry_policies[0].trigger = ConditionPlan(
            combine="all",
            children=[
                ConditionPlan(feature="order_imbalance", op=">", threshold=0.3),
                ConditionPlan(feature="depth_imbalance", op=">", threshold=0.1),
            ],
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.entry_policies[0].trigger.__class__.__name__ == "AllExpr"

    def test_composite_any_condition(self):
        plan = _make_minimal_plan()
        plan.entry_policies[0].trigger = ConditionPlan(
            combine="any",
            children=[
                ConditionPlan(feature="order_imbalance", op=">", threshold=0.3),
                ConditionPlan(feature="trade_flow_imbalance", op=">", threshold=0.2),
            ],
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.entry_policies[0].trigger.__class__.__name__ == "AnyExpr"

    def test_cross_condition(self):
        plan = _make_minimal_plan()
        plan.entry_policies[0].trigger = ConditionPlan(
            cross_feature="order_imbalance",
            cross_threshold=0.3,
            cross_direction="above",
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.entry_policies[0].trigger.__class__.__name__ == "CrossExpr"

    def test_persist_condition(self):
        plan = _make_minimal_plan()
        plan.entry_policies[0].trigger = ConditionPlan(
            persist_condition=ConditionPlan(
                feature="order_imbalance", op=">", threshold=0.3,
            ),
            persist_window=10,
            persist_min_true=7,
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.entry_policies[0].trigger.__class__.__name__ == "PersistExpr"

    def test_rolling_condition(self):
        plan = _make_minimal_plan()
        plan.entry_policies[0].trigger = ConditionPlan(
            rolling_feature="order_imbalance",
            rolling_method="mean",
            rolling_window=10,
            op=">",
            threshold=0.2,
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.entry_policies[0].trigger.__class__.__name__ == "ComparisonExpr"

    def test_state_var_condition(self):
        plan = _make_minimal_plan()
        plan.exit_policies[0].rules[0].condition = ConditionPlan(
            state_var="loss_streak", op=">=", threshold=3.0,
        )
        spec = lower_plan_to_spec_v2(plan)
        rule_cond = spec.exit_policies[0].rules[0].condition
        assert rule_cond.__class__.__name__ == "ComparisonExpr"

    def test_position_attr_condition(self):
        plan = _make_minimal_plan()
        # Already uses position_attr in exit rules — verify it works
        spec = lower_plan_to_spec_v2(plan)
        rule_cond = spec.exit_policies[0].rules[0].condition
        assert rule_cond.__class__.__name__ == "ComparisonExpr"


# ── OpenAI client unit tests ────────────────────────────────────────


class TestOpenAIClient:

    def test_mock_mode_returns_none_without_factory(self):
        client = OpenAIStrategyGenClient(mode="mock")
        result = client.query_structured(
            system_prompt="test",
            user_prompt="test",
            schema=StrategyPlan,
        )
        assert result is None
        assert client.last_query_meta["status"] == "mock_no_call"

    def test_mock_mode_uses_factory(self):
        client = OpenAIStrategyGenClient(mode="mock")
        plan = _make_minimal_plan()
        result = client.query_structured(
            system_prompt="test",
            user_prompt="test",
            schema=StrategyPlan,
            mock_factory=lambda: plan,
        )
        assert result is plan
        assert client.last_query_meta["status"] == "mock_factory"

    def test_live_mode_without_key_unavailable(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client = OpenAIStrategyGenClient(mode="live")
        assert not client.is_available
        result = client.query_structured(
            system_prompt="test",
            user_prompt="test",
            schema=StrategyPlan,
        )
        assert result is None
        assert client.last_query_meta["status"] == "live_unavailable"

    def test_reset_clears_state(self):
        client = OpenAIStrategyGenClient(mode="mock")
        client._replay_log.append({"test": True})
        client._replay_cursor = 5
        client.reset()
        assert client._replay_log == []
        assert client._replay_cursor == 0
        assert client.last_query_meta["status"] == "reset"

    def test_save_replay_log(self, tmp_path: Path):
        client = OpenAIStrategyGenClient(mode="mock")
        client._replay_log = [{"schema": "test", "response": {}}]
        path = tmp_path / "saved_replay.json"
        client.save_replay_log(path)
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert len(loaded) == 1


# ── Batch generation ────────────────────────────────────────────────


class TestBatchGeneration:

    def test_generate_batch_template(self):
        gen = StrategyGenerator(backend="template", spec_format="v2")
        results = gen.generate_batch(research_goal="imbalance momentum", n_ideas=3)
        assert len(results) >= 1
        for spec, trace in results:
            assert isinstance(spec, StrategySpecV2)
            assert trace["generation_outcome"] == "success"


# ── OpenAI structured output schema compatibility ────────────────────


class TestSchemaCompatibility:
    """Verify StrategyPlan JSON schema is compatible with OpenAI strict mode.

    OpenAI structured outputs require:
    - No ``additionalProperties`` other than ``false``
    - No ``minimum``/``maximum``/``minItems`` etc.
    - All ``properties`` can be made ``required`` (SDK does this)
    - Recursive ``$ref`` is fine
    """

    def _get_schema(self) -> dict:
        return StrategyPlan.model_json_schema()

    def _find_additional_properties(self, obj: Any, path: str = "") -> list[str]:
        """Find any additionalProperties that is not False."""
        issues: list[str] = []
        if isinstance(obj, dict):
            if "additionalProperties" in obj and obj["additionalProperties"] is not False:
                issues.append(f"{path}: additionalProperties={obj['additionalProperties']}")
            for k, v in obj.items():
                issues.extend(self._find_additional_properties(v, f"{path}.{k}"))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                issues.extend(self._find_additional_properties(v, f"{path}[{i}]"))
        return issues

    def _find_unsupported_keywords(self, obj: Any, path: str = "") -> list[str]:
        """Find JSON schema keywords not supported by OpenAI strict mode."""
        unsupported = {
            "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
            "minItems", "maxItems", "minLength", "maxLength",
            "pattern", "format", "minProperties", "maxProperties",
        }
        issues: list[str] = []
        if isinstance(obj, dict):
            found = unsupported & set(obj.keys())
            if found:
                issues.append(f"{path}: unsupported keywords {found}")
            for k, v in obj.items():
                issues.extend(self._find_unsupported_keywords(v, f"{path}.{k}"))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                issues.extend(self._find_unsupported_keywords(v, f"{path}[{i}]"))
        return issues

    def test_no_additional_properties(self):
        """Schema must not use additionalProperties (dynamic maps)."""
        schema = self._get_schema()
        issues = self._find_additional_properties(schema)
        assert issues == [], f"Found additionalProperties violations:\n" + "\n".join(issues)

    def test_no_unsupported_keywords(self):
        """Schema must not use minimum/maximum etc."""
        schema = self._get_schema()
        issues = self._find_unsupported_keywords(schema)
        assert issues == [], f"Found unsupported keywords:\n" + "\n".join(issues)

    def test_vars_is_array_not_object(self):
        """StatePlan.vars must be array of StateVarPlan, not dict."""
        schema = self._get_schema()
        state_plan = schema["$defs"]["StatePlan"]
        vars_schema = state_plan["properties"]["vars"]
        assert vars_schema["type"] == "array", (
            f"StatePlan.vars should be array, got: {vars_schema}"
        )
        assert "$ref" in vars_schema["items"], (
            f"StatePlan.vars items should reference StateVarPlan"
        )

    def test_state_var_plan_has_name_and_initial_value(self):
        """StateVarPlan must have name (required) and initial_value fields."""
        schema = self._get_schema()
        svp = schema["$defs"]["StateVarPlan"]
        assert "name" in svp["properties"]
        assert "initial_value" in svp["properties"]
        assert "name" in svp.get("required", [])

    def test_entry_plan_strength_no_constraints(self):
        """EntryPlan.strength must not have minimum/maximum."""
        schema = self._get_schema()
        entry = schema["$defs"]["EntryPlan"]
        strength = entry["properties"]["strength"]
        assert "minimum" not in strength, f"strength has minimum: {strength}"
        assert "maximum" not in strength, f"strength has maximum: {strength}"

    def test_all_defs_are_object_type(self):
        """Every $def must be type=object (no dynamic maps)."""
        schema = self._get_schema()
        for name, defn in schema.get("$defs", {}).items():
            assert defn.get("type") == "object", (
                f"$defs.{name} has type={defn.get('type')}, expected 'object'"
            )

    def test_schema_roundtrip_json(self):
        """Schema can be serialized and deserialized as JSON."""
        schema = self._get_schema()
        json_str = json.dumps(schema)
        restored = json.loads(json_str)
        assert restored["title"] == "StrategyPlan"

    def test_recursive_condition_plan_uses_ref(self):
        """ConditionPlan children/persist use $ref (supported by OpenAI)."""
        schema = self._get_schema()
        cond = schema["$defs"]["ConditionPlan"]
        children_schema = cond["properties"]["children"]
        # children is anyOf: [array of $ref, null]
        array_variant = [v for v in children_schema["anyOf"] if v.get("type") == "array"]
        assert len(array_variant) == 1
        assert "$ref" in array_variant[0]["items"]


# ── State policy lowering with new schema ────────────────────────────


class TestStatePolicyLowering:
    """Test that list[StateVarPlan] correctly lowers to dict[str, float]."""

    def test_state_vars_list_to_dict(self):
        plan = _make_minimal_plan()
        plan.state_policy = StatePlan(
            vars=[
                StateVarPlan(name="loss_streak", initial_value=0.0),
                StateVarPlan(name="cooldown_counter", initial_value=5.0),
            ],
            guards=[
                StateGuardPlan(
                    name="cooldown_active",
                    condition=ConditionPlan(state_var="cooldown_counter", op=">", threshold=0.0),
                    effect="block_entry",
                ),
            ],
            events=[
                StateEventPlan(
                    name="on_loss",
                    on="on_exit_loss",
                    updates=[
                        StateUpdatePlan(var="loss_streak", op="increment", value=1.0),
                    ],
                ),
            ],
        )
        spec = lower_plan_to_spec_v2(plan)
        assert spec.state_policy is not None
        assert spec.state_policy.vars == {"loss_streak": 0.0, "cooldown_counter": 5.0}
        assert len(spec.state_policy.guards) == 1
        assert len(spec.state_policy.events) == 1

    def test_state_vars_empty_list(self):
        plan = _make_minimal_plan()
        plan.state_policy = StatePlan(vars=[], guards=[], events=[])
        spec = lower_plan_to_spec_v2(plan)
        assert spec.state_policy is not None
        assert spec.state_policy.vars == {}

    def test_plan_with_state_policy_passes_review(self, reviewer: StrategyReviewerV2):
        plan = _make_minimal_plan()
        plan.state_policy = StatePlan(
            vars=[StateVarPlan(name="counter", initial_value=0.0)],
            guards=[],
            events=[],
        )
        spec = lower_plan_to_spec_v2(plan)
        result = reviewer.review(spec)
        assert result.passed, (
            f"Review failed: {[i.description for i in result.issues if i.severity == 'error']}"
        )

    def test_validate_plan_state_var_refs(self):
        """validate_plan checks state_var references against defined vars."""
        plan = _make_minimal_plan()
        plan.state_policy = StatePlan(
            vars=[StateVarPlan(name="counter", initial_value=0.0)],
            guards=[
                StateGuardPlan(
                    name="bad_ref",
                    condition=ConditionPlan(state_var="undefined_var", op=">", threshold=0.0),
                ),
            ],
            events=[],
        )
        warnings = validate_plan(plan)
        assert any("undefined_var" in w for w in warnings)

    def test_state_policy_serialization_roundtrip(self):
        """Plan with state_policy survives model_dump/model_validate."""
        plan = _make_minimal_plan()
        plan.state_policy = StatePlan(
            vars=[
                StateVarPlan(name="x", initial_value=1.0),
                StateVarPlan(name="y", initial_value=2.0),
            ],
            guards=[],
            events=[],
        )
        data = plan.model_dump()
        restored = StrategyPlan.model_validate(data)
        assert len(restored.state_policy.vars) == 2
        assert restored.state_policy.vars[0].name == "x"
        assert restored.state_policy.vars[0].initial_value == 1.0
        assert restored.state_policy.vars[1].name == "y"

    def test_replay_with_state_policy(self, tmp_path: Path):
        """Replay mode with state_policy in plan data."""
        plan = _make_minimal_plan()
        plan.state_policy = StatePlan(
            vars=[StateVarPlan(name="streak", initial_value=0.0)],
            guards=[],
            events=[],
        )
        replay_log = [{
            "schema": "StrategyPlan",
            "system_prompt": "test",
            "user_prompt": "test",
            "response": plan.model_dump(),
        }]
        replay_path = tmp_path / "replay_state.json"
        replay_path.write_text(json.dumps(replay_log), encoding="utf-8")

        client = OpenAIStrategyGenClient(mode="replay", replay_path=replay_path)
        plan_result, trace = generate_plan_with_openai(
            client=client, research_goal="test with state",
        )
        assert plan_result.state_policy is not None
        assert len(plan_result.state_policy.vars) == 1
        assert plan_result.state_policy.vars[0].name == "streak"


def test_build_user_prompt_includes_canonical_backtest_constraint_summary():
    prompt = build_user_prompt(
        research_goal="test momentum",
        strategy_style="momentum",
        latency_ms=1.0,
        backtest_environment={
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
                "submit_latency_gating": True,
                "cancel_latency_gating": True,
                "replace_model": "minimal_immediate",
            },
        },
    )

    assert "Backtest constraint summary (canonical)" in prompt
    assert "tick = resample step" in prompt
    assert "queue_model=risk_adverse" in prompt
    assert "replace is minimal immediate, not staged venue replace" in prompt
