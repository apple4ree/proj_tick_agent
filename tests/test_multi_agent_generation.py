"""Tests for Multi-Agent strategy generation pipeline.

All tests work without an OpenAI API key — LLM calls use mock/fallback.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from strategy_block.strategy_generation.agent_schemas import (
    ExitRuleDraft,
    FilterRuleDraft,
    IdeaBrief,
    IdeaBriefList,
    KNOWN_FEATURES_LIST,
    KNOWN_FEATURES_SET,
    PositionRuleDraft,
    ReviewDecision,
    ReviewIssueDraft,
    RiskDraft,
    SignalDraft,
    SignalRuleDraft,
    VALID_EXIT_TYPES,
    VALID_OPERATORS,
    VALID_SIZING_MODES,
)
from strategy_block.strategy_generation.prompt_loader import load_agent_prompt
from strategy_block.strategy_generation.assembler import assemble_spec
from strategy_block.strategy_generation.agents import (
    FactorDesignerAgent,
    LLMReviewerAgent,
    ResearcherAgent,
    RiskDesignerAgent,
)
from strategy_block.strategy_generation.generator import StrategyGenerator, StaticReviewError
from strategy_block.strategy_generation.openai_client import OpenAIStrategyGenClient
from strategy_block.strategy_generation.pipeline import MultiAgentPipeline
from strategy_block.strategy_specs.schema import StrategySpec
from strategy_block.strategy_review.reviewer import StrategyReviewer


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def sample_idea() -> IdeaBrief:
    return IdeaBrief(
        name="test_strategy",
        thesis="Order imbalance predicts short-term direction",
        core_features=["order_imbalance", "depth_imbalance"],
        style="momentum",
        rationale="Test rationale",
    )


@pytest.fixture
def sample_signal_draft() -> SignalDraft:
    return SignalDraft(
        signal_rules=[
            SignalRuleDraft(feature="order_imbalance", operator=">", threshold=0.3,
                           score_contribution=0.5, description="Buy signal"),
            SignalRuleDraft(feature="order_imbalance", operator="<", threshold=-0.3,
                           score_contribution=-0.5, description="Sell signal"),
        ],
        filters=[
            FilterRuleDraft(feature="spread_bps", operator=">", threshold=30.0,
                           action="block", description="Wide spread filter"),
        ],
        rationale="Test design",
    )


@pytest.fixture
def sample_risk_draft() -> RiskDraft:
    return RiskDraft(
        position_rule=PositionRuleDraft(
            max_position=500, sizing_mode="signal_proportional",
            fixed_size=100, holding_period_ticks=10, inventory_cap=1000,
        ),
        exit_rules=[
            ExitRuleDraft(exit_type="stop_loss", threshold_bps=15.0),
            ExitRuleDraft(exit_type="take_profit", threshold_bps=25.0),
            ExitRuleDraft(exit_type="time_exit", timeout_ticks=300),
        ],
        latency_notes="1ms latency",
    )


# ── 0. Prompt Loader Tests ───────────────────────────────────────────

_TEST_CONTEXT: dict[str, str] = {
    "FEATURES_BLOCK": (
        "ALLOWED FEATURES (use ONLY these):\n"
        + "\n".join(f"  - {f}" for f in KNOWN_FEATURES_LIST)
    ),
    "OPERATORS_BLOCK": f"ALLOWED OPERATORS: {', '.join(VALID_OPERATORS)}",
    "SIZING_BLOCK": f"ALLOWED SIZING MODES: {', '.join(VALID_SIZING_MODES)}",
    "EXIT_TYPES_BLOCK": f"ALLOWED EXIT TYPES: {', '.join(VALID_EXIT_TYPES)}",
}


class TestPromptLoader:

    @pytest.mark.parametrize("name", ["researcher", "factor_designer", "risk_designer", "reviewer"])
    def test_prompt_file_loads(self, name: str):
        """Each agent prompt .md file loads without error."""
        prompt = load_agent_prompt(name, _TEST_CONTEXT)
        assert isinstance(prompt, str)
        assert len(prompt) > 50  # non-trivial content

    @pytest.mark.parametrize("name", ["researcher", "factor_designer", "risk_designer", "reviewer"])
    def test_placeholders_fully_substituted(self, name: str):
        """No raw {PLACEHOLDER} tokens remain after substitution."""
        prompt = load_agent_prompt(name, _TEST_CONTEXT)
        for key in _TEST_CONTEXT:
            assert f"{{{key}}}" not in prompt, f"Unsubstituted placeholder {{{key}}} in {name}.md"

    def test_researcher_contains_features(self):
        prompt = load_agent_prompt("researcher", _TEST_CONTEXT)
        assert "ALLOWED FEATURES" in prompt
        assert "order_imbalance" in prompt

    def test_factor_designer_contains_operators(self):
        prompt = load_agent_prompt("factor_designer", _TEST_CONTEXT)
        assert "ALLOWED OPERATORS" in prompt
        assert ">" in prompt

    def test_risk_designer_contains_sizing_and_exits(self):
        prompt = load_agent_prompt("risk_designer", _TEST_CONTEXT)
        assert "ALLOWED SIZING MODES" in prompt
        assert "ALLOWED EXIT TYPES" in prompt
        assert "stop_loss" in prompt

    def test_missing_prompt_raises(self):
        with pytest.raises(FileNotFoundError):
            load_agent_prompt("nonexistent_agent", _TEST_CONTEXT)


# ── 1. Schema Parse Tests ────────────────────────────────────────────

class TestAgentSchemas:

    def test_signal_rule_valid_operator(self):
        rule = SignalRuleDraft(
            feature="order_imbalance", operator=">",
            threshold=0.3, score_contribution=0.5,
        )
        assert rule.operator == ">"

    def test_signal_rule_invalid_operator_rejected(self):
        with pytest.raises(Exception):
            SignalRuleDraft(
                feature="order_imbalance", operator="INVALID",
                threshold=0.3, score_contribution=0.5,
            )

    def test_signal_rule_unknown_feature_rejected(self):
        with pytest.raises(Exception):
            SignalRuleDraft(
                feature="nonexistent_feature", operator=">",
                threshold=0.3, score_contribution=0.5,
            )

    def test_idea_brief_filters_unknown_features(self):
        idea = IdeaBrief(
            name="test", thesis="test",
            core_features=["order_imbalance", "FAKE_FEATURE"],
            style="momentum",
        )
        assert "order_imbalance" in idea.core_features
        assert "FAKE_FEATURE" not in idea.core_features

    def test_position_rule_valid_sizing_modes(self):
        for mode in ["fixed", "signal_proportional", "kelly"]:
            rule = PositionRuleDraft(sizing_mode=mode)
            assert rule.sizing_mode == mode

    def test_exit_rule_valid_types(self):
        for exit_type in ["stop_loss", "take_profit", "trailing_stop", "time_exit", "signal_reversal"]:
            rule = ExitRuleDraft(exit_type=exit_type)
            assert rule.exit_type == exit_type

    def test_review_decision_schema(self):
        decision = ReviewDecision(
            approved=True,
            issues=[ReviewIssueDraft(category="test", description="test issue")],
            confidence=0.9,
        )
        assert decision.approved
        assert len(decision.issues) == 1

    def test_idea_brief_list_requires_ideas(self):
        with pytest.raises(Exception):
            IdeaBriefList(ideas=[])


# ── 2. Assembler Tests ───────────────────────────────────────────────

class TestAssembler:

    def test_assemble_spec_basic(self, sample_idea, sample_signal_draft, sample_risk_draft):
        spec = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test goal",
        )
        assert isinstance(spec, StrategySpec)
        assert spec.name == "test_strategy"
        assert len(spec.signal_rules) == 2
        assert len(spec.filters) == 1
        assert len(spec.exit_rules) == 3

    def test_assemble_spec_passes_validation(self, sample_idea, sample_signal_draft, sample_risk_draft):
        spec = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test",
        )
        errors = spec.validate()
        assert errors == [], f"Validation errors: {errors}"

    def test_latency_calibration_scales_holding_period(self, sample_idea, sample_signal_draft, sample_risk_draft):
        spec_1ms = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test",
            latency_ms=1.0,
        )
        spec_100ms = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test",
            latency_ms=100.0,
        )
        assert spec_100ms.position_rule.holding_period_ticks > spec_1ms.position_rule.holding_period_ticks

    def test_latency_calibration_scales_time_exit(self, sample_idea, sample_signal_draft, sample_risk_draft):
        spec_1ms = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test",
            latency_ms=1.0,
        )
        spec_100ms = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test",
            latency_ms=100.0,
        )
        time_exits_1 = [r for r in spec_1ms.exit_rules if r.exit_type == "time_exit"]
        time_exits_100 = [r for r in spec_100ms.exit_rules if r.exit_type == "time_exit"]
        assert time_exits_100[0].timeout_ticks > time_exits_1[0].timeout_ticks

    def test_auto_adds_stop_loss_if_missing(self, sample_idea, sample_signal_draft):
        risk = RiskDraft(
            position_rule=PositionRuleDraft(),
            exit_rules=[ExitRuleDraft(exit_type="take_profit", threshold_bps=20.0)],
        )
        spec = assemble_spec(
            idea=sample_idea, signal_draft=sample_signal_draft,
            risk_draft=risk, research_goal="test",
        )
        exit_types = {r.exit_type for r in spec.exit_rules}
        assert "stop_loss" in exit_types
        assert "time_exit" in exit_types

    def test_metadata_records_provenance(self, sample_idea, sample_signal_draft, sample_risk_draft):
        spec = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test goal",
            latency_ms=50.0,
        )
        assert spec.metadata["pipeline"] == "multi_agent_openai_v1"
        assert spec.metadata["latency_ms"] == 50.0
        assert spec.metadata["research_goal"] == "test goal"
        assert "generated_at" in spec.metadata


# ── 3. Agent Fallback Tests ──────────────────────────────────────────

class TestAgentsFallback:

    def test_researcher_fallback_produces_ideas(self):
        agent = ResearcherAgent(client=None)
        result = agent.run("Order imbalance alpha")
        assert isinstance(result, IdeaBriefList)
        assert len(result.ideas) >= 1
        for idea in result.ideas:
            assert idea.name
            assert idea.thesis

    def test_factor_designer_fallback_produces_rules(self, sample_idea):
        agent = FactorDesignerAgent(client=None)
        result = agent.run(sample_idea)
        assert isinstance(result, SignalDraft)
        assert len(result.signal_rules) >= 1
        for rule in result.signal_rules:
            assert rule.feature in KNOWN_FEATURES_SET

    def test_risk_designer_fallback_produces_risk(self, sample_idea, sample_signal_draft):
        agent = RiskDesignerAgent(client=None)
        result = agent.run(sample_idea, sample_signal_draft, 1.0)
        assert isinstance(result, RiskDraft)
        assert result.position_rule.max_position > 0
        assert len(result.exit_rules) >= 1

    def test_llm_reviewer_fallback_uses_static(self, sample_idea, sample_signal_draft, sample_risk_draft):
        spec = assemble_spec(
            idea=sample_idea,
            signal_draft=sample_signal_draft,
            risk_draft=sample_risk_draft,
            research_goal="test",
        )
        agent = LLMReviewerAgent(client=None)
        result = agent.run(spec.to_dict())
        assert isinstance(result, ReviewDecision)
        assert isinstance(result.approved, bool)


# ── 4. Pipeline Mock/Fallback Tests ─────────────────────────────────

class TestPipelineMock:

    def test_pipeline_mock_mode_generates_spec(self):
        pipeline = MultiAgentPipeline(mode="mock", latency_ms=1.0)
        spec, trace = pipeline.generate(research_goal="Order imbalance alpha")
        assert isinstance(spec, StrategySpec)
        assert spec.name
        assert len(spec.signal_rules) >= 1
        assert trace["pipeline"] == "multi_agent_openai_v1"
        assert trace["mode"] == "mock"

    def test_pipeline_mock_mode_batch(self):
        pipeline = MultiAgentPipeline(mode="mock", latency_ms=1.0)
        results = pipeline.generate_batch(research_goal="Test", n_ideas=2)
        assert len(results) >= 1
        for spec, trace in results:
            assert isinstance(spec, StrategySpec)
            errors = spec.validate()
            assert errors == [], f"Validation errors: {errors}"

    def test_pipeline_trace_has_required_keys(self):
        pipeline = MultiAgentPipeline(mode="mock")
        _, trace = pipeline.generate(research_goal="Test")
        required_keys = ["pipeline", "mode", "timestamp", "input", "researcher",
                         "selected_idea", "factor_design", "risk_design",
                         "llm_review", "static_review", "output"]
        for key in required_keys:
            assert key in trace, f"Missing trace key: {key}"

    def test_pipeline_spec_passes_static_review(self):
        pipeline = MultiAgentPipeline(mode="mock")
        spec, _ = pipeline.generate(research_goal="Test")
        reviewer = StrategyReviewer()
        result = reviewer.review(spec)
        assert result.passed, f"Static review failed: {[i.description for i in result.issues if i.severity == 'error']}"


# ── 5. Generator Backend Tests ───────────────────────────────────────

class TestGeneratorBackend:

    def test_template_mode_unchanged(self):
        gen = StrategyGenerator(latency_ms=1.0, backend="template")
        spec, trace = gen.generate(research_goal="Order imbalance")
        assert isinstance(spec, StrategySpec)
        assert trace["pipeline"] == "template_generator_v1"
        assert trace.get("fallback_used", False) is False

    def test_openai_mode_fallback_no_key(self):
        """Without OPENAI_API_KEY, openai backend should use agent fallbacks (mock-like)."""
        gen = StrategyGenerator(latency_ms=1.0, backend="openai", mode="mock")
        spec, trace = gen.generate(research_goal="Order imbalance")
        assert isinstance(spec, StrategySpec)
        assert spec.name
        assert len(spec.signal_rules) >= 1

    def test_generate_batch_template(self):
        gen = StrategyGenerator(latency_ms=1.0, backend="template")
        results = gen.generate_batch(research_goal="tick alpha")
        assert len(results) >= 1
        for spec, trace in results:
            assert isinstance(spec, StrategySpec)


# ── 6. Static Reviewer Hard Gate Tests ───────────────────────────────

class TestStaticReviewerGate:

    def test_static_reviewer_runs_on_pipeline_output(self):
        pipeline = MultiAgentPipeline(mode="mock")
        spec, trace = pipeline.generate(research_goal="Test")
        assert "static_review" in trace
        assert "passed" in trace["static_review"]

    def test_pipeline_raises_on_static_review_failure(self):
        """Pipeline must raise when static review fails (hard gate)."""
        pipeline = MultiAgentPipeline(mode="mock")

        # Force static review to fail
        fake_result = MagicMock()
        fake_result.passed = False
        fake_result.issues = [MagicMock(severity="error", description="forced fail")]
        fake_result.to_dict.return_value = {"passed": False, "issues": []}

        with patch.object(pipeline._static_reviewer, "review", return_value=fake_result):
            with pytest.raises(ValueError, match="failed static review"):
                pipeline.generate(research_goal="Test")

    def test_generator_template_raises_static_review_error(self):
        """Template backend must raise StaticReviewError when review fails."""
        gen = StrategyGenerator(latency_ms=1.0, backend="template")

        fake_result = MagicMock()
        fake_result.passed = False
        fake_result.issues = [MagicMock(severity="error", description="forced fail")]
        fake_result.to_dict.return_value = {"passed": False, "issues": []}

        with patch.object(gen._reviewer, "review", return_value=fake_result):
            with pytest.raises(StaticReviewError, match="failed static review"):
                gen.generate(research_goal="Test")

    def test_generator_openai_fallback_to_template_on_pipeline_fail(self):
        """When openai pipeline raises, generator falls back to template."""
        gen = StrategyGenerator(latency_ms=1.0, backend="openai", mode="mock")

        # Force the pipeline to raise, template should succeed
        if gen._multi_agent is not None:
            with patch.object(
                gen._multi_agent, "generate",
                side_effect=ValueError("pipeline failed"),
            ):
                spec, trace = gen.generate(research_goal="Order imbalance")
                assert isinstance(spec, StrategySpec)
                assert trace["fallback_used"] is True
                assert trace["generation_outcome"] == "fallback_success"
        else:
            # No pipeline, template is used directly
            spec, trace = gen.generate(research_goal="Order imbalance")
            assert isinstance(spec, StrategySpec)

    def test_generator_both_backends_fail_raises(self):
        """When both openai and template fail review, StaticReviewError propagates."""
        gen = StrategyGenerator(latency_ms=1.0, backend="openai", mode="mock")

        fake_result = MagicMock()
        fake_result.passed = False
        fake_result.issues = [MagicMock(severity="error", description="forced fail")]
        fake_result.to_dict.return_value = {"passed": False, "issues": []}

        if gen._multi_agent is not None:
            with patch.object(
                gen._multi_agent, "generate",
                side_effect=ValueError("pipeline failed"),
            ):
                with patch.object(gen._reviewer, "review", return_value=fake_result):
                    with pytest.raises(StaticReviewError):
                        gen.generate(research_goal="Test")
        else:
            # No pipeline; template-only path
            with patch.object(gen._reviewer, "review", return_value=fake_result):
                with pytest.raises(StaticReviewError):
                    gen.generate(research_goal="Test")

    def test_static_review_error_has_trace(self):
        """StaticReviewError raised from template should carry a trace dict."""
        gen = StrategyGenerator(latency_ms=1.0, backend="template")

        fake_result = MagicMock()
        fake_result.passed = False
        fake_result.issues = [MagicMock(severity="error", description="forced")]
        fake_result.to_dict.return_value = {"passed": False, "issues": []}

        with patch.object(gen._reviewer, "review", return_value=fake_result):
            with pytest.raises(StaticReviewError) as exc_info:
                gen.generate(research_goal="Test")

        assert exc_info.value.trace is not None
        assert exc_info.value.trace["generation_outcome"] == "failed"
        assert exc_info.value.trace["static_review_passed"] is False

    def test_generation_outcome_field_on_success(self):
        """Successful generation trace must have generation_outcome='success'."""
        gen = StrategyGenerator(latency_ms=1.0, backend="template")
        spec, trace = gen.generate(research_goal="Order imbalance")
        assert trace["generation_outcome"] == "success"
        assert trace["static_review_passed"] is True


# ── 7. Compiler Smoke Test ───────────────────────────────────────────

class TestCompilerSmoke:

    def test_mock_generated_spec_compiles(self):
        pipeline = MultiAgentPipeline(mode="mock")
        spec, _ = pipeline.generate(research_goal="Order imbalance")
        from strategy_block.strategy_compiler.compiler import StrategyCompiler
        strategy = StrategyCompiler.compile(spec)
        assert strategy is not None

    def test_template_generated_spec_compiles(self):
        gen = StrategyGenerator(latency_ms=1.0, backend="template")
        spec, _ = gen.generate(research_goal="Order imbalance")
        from strategy_block.strategy_compiler.compiler import StrategyCompiler
        strategy = StrategyCompiler.compile(spec)
        assert strategy is not None


# ── 8. OpenAI Client Tests ───────────────────────────────────────────

class TestOpenAIClient:

    def test_mock_mode_returns_none(self):
        client = OpenAIStrategyGenClient(mode="mock")
        result = client.query_structured(
            system_prompt="test", user_prompt="test", schema=IdeaBriefList,
        )
        assert result is None

    def test_replay_mode_empty_log(self):
        client = OpenAIStrategyGenClient(mode="replay")
        result = client.query_structured(
            system_prompt="test", user_prompt="test", schema=IdeaBriefList,
        )
        assert result is None

    def test_replay_mode_with_data(self, tmp_path):
        # Write a replay log
        log = [{
            "schema": "IdeaBriefList",
            "response": {
                "ideas": [{
                    "name": "replay_test",
                    "thesis": "test thesis",
                    "core_features": ["order_imbalance"],
                    "style": "momentum",
                }]
            }
        }]
        replay_path = tmp_path / "replay.json"
        replay_path.write_text(json.dumps(log))

        client = OpenAIStrategyGenClient(mode="replay", replay_path=replay_path)
        result = client.query_structured(
            system_prompt="test", user_prompt="test", schema=IdeaBriefList,
        )
        assert result is not None
        assert result.ideas[0].name == "replay_test"

    def test_save_replay_log(self, tmp_path):
        client = OpenAIStrategyGenClient(mode="mock")
        client._replay_log = [{"test": "entry"}]
        out_path = tmp_path / "saved_replay.json"
        client.save_replay_log(out_path)
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert len(data) == 1
