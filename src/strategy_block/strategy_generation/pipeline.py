"""Multi-Agent orchestration pipeline for strategy generation.

Chains ResearcherAgent → FactorDesignerAgent → RiskDesignerAgent →
assembler → LLMReviewerAgent → static reviewer → StrategySpec.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategy_block.strategy_specs.schema import StrategySpec
from strategy_block.strategy_review.reviewer import StrategyReviewer

from .agent_schemas import IdeaBrief, ReviewDecision
from .agents import (
    FactorDesignerAgent,
    LLMReviewerAgent,
    ResearcherAgent,
    RiskDesignerAgent,
)
from .assembler import assemble_spec
from .openai_client import OpenAIStrategyGenClient, is_openai_available

logger = logging.getLogger(__name__)


class MultiAgentPipeline:
    """Orchestrates 4 agents to generate a StrategySpec.

    Parameters
    ----------
    mode : str
        "live", "replay", or "mock". Default "live".
    model : str
        OpenAI model name.
    temperature : float
        Sampling temperature.
    latency_ms : float
        Expected execution latency for risk calibration.
    max_review_iterations : int
        Max times to re-run factor/risk design after reviewer critique.
    replay_path : Path | str | None
        Path for replay log (load in replay mode, save in live mode).
    """

    def __init__(
        self,
        *,
        mode: str = "live",
        model: str = "gpt-4o",
        temperature: float = 0.2,
        latency_ms: float = 1.0,
        max_review_iterations: int = 1,
        replay_path: Path | str | None = None,
    ) -> None:
        self.mode = mode
        self.latency_ms = latency_ms
        self.max_review_iterations = max_review_iterations
        self._replay_path = Path(replay_path) if replay_path else None

        # Initialize client (None for mock mode, or if API key missing)
        if mode == "mock":
            self._client: OpenAIStrategyGenClient | None = None
        else:
            self._client = OpenAIStrategyGenClient(
                mode=mode,
                model=model,
                temperature=temperature,
                replay_path=replay_path,
            )

        # Initialize agents
        self._researcher = ResearcherAgent(self._client)
        self._factor_designer = FactorDesignerAgent(self._client)
        self._risk_designer = RiskDesignerAgent(self._client)
        self._llm_reviewer = LLMReviewerAgent(self._client)
        self._static_reviewer = StrategyReviewer()

    @property
    def is_llm_mode(self) -> bool:
        """Whether the pipeline is using real LLM calls."""
        return self._client is not None and self._client.is_available

    def generate(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
        idea_index: int = 0,
    ) -> tuple[StrategySpec, dict[str, Any]]:
        """Generate a single strategy spec via multi-agent pipeline.

        Returns (spec, trace) matching the StrategyGenerator interface.
        """
        trace: dict[str, Any] = {
            "pipeline": "multi_agent_openai_v1",
            "mode": self.mode,
            "llm_active": self.is_llm_mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": {
                "research_goal": research_goal,
                "n_ideas": n_ideas,
                "idea_index": idea_index,
                "latency_ms": self.latency_ms,
            },
        }

        # Step 1: Research — generate ideas
        logger.info("Step 1: Generating ideas for goal=%r", research_goal)
        idea_list = self._researcher.run(research_goal, n_ideas)
        trace["researcher"] = {
            "n_ideas": len(idea_list.ideas),
            "ideas": [i.model_dump() for i in idea_list.ideas],
        }

        # Step 2: Select idea
        if idea_index >= len(idea_list.ideas):
            idea_index = 0
        idea = idea_list.ideas[idea_index]
        trace["selected_idea"] = idea.model_dump()
        logger.info("Step 2: Selected idea '%s'", idea.name)

        # Step 3: Factor design
        logger.info("Step 3: Designing factors for '%s'", idea.name)
        signal_draft = self._factor_designer.run(idea)
        trace["factor_design"] = signal_draft.model_dump()

        # Step 4: Risk design
        logger.info("Step 4: Designing risk/exits for '%s'", idea.name)
        risk_draft = self._risk_designer.run(idea, signal_draft, self.latency_ms)
        trace["risk_design"] = risk_draft.model_dump()

        # Step 5: Assemble spec
        logger.info("Step 5: Assembling StrategySpec")
        spec = assemble_spec(
            idea=idea,
            signal_draft=signal_draft,
            risk_draft=risk_draft,
            research_goal=research_goal,
            latency_ms=self.latency_ms,
        )

        # Step 6: LLM review (soft critique)
        review_trace: list[dict[str, Any]] = []
        for iteration in range(self.max_review_iterations + 1):
            logger.info("Step 6: LLM review (iteration %d)", iteration + 1)
            review = self._llm_reviewer.run(spec.to_dict())
            review_trace.append({
                "iteration": iteration + 1,
                "approved": review.approved,
                "issues": [i.model_dump() for i in review.issues],
                "confidence": review.confidence,
            })

            if review.approved or iteration == self.max_review_iterations:
                break

            # Apply suggested changes by re-running factor/risk design
            logger.info("LLM reviewer suggested changes, re-running design")
            signal_draft = self._factor_designer.run(idea)
            risk_draft = self._risk_designer.run(idea, signal_draft, self.latency_ms)
            spec = assemble_spec(
                idea=idea,
                signal_draft=signal_draft,
                risk_draft=risk_draft,
                research_goal=research_goal,
                latency_ms=self.latency_ms,
            )

        trace["llm_review"] = review_trace

        # Step 7: Static reviewer (hard gate)
        logger.info("Step 7: Static review (hard validation)")
        static_result = self._static_reviewer.review(spec)
        trace["static_review"] = static_result.to_dict()

        # Build output section before potential raise
        trace["output"] = {
            "spec_name": spec.name,
            "spec_version": spec.version,
            "n_signal_rules": len(spec.signal_rules),
            "n_filters": len(spec.filters),
            "n_exit_rules": len(spec.exit_rules),
        }
        trace["fallback_used"] = False

        if not static_result.passed:
            trace["static_review_passed"] = False
            trace["generation_outcome"] = "failed"
            error_descriptions = "; ".join(
                i.description for i in static_result.issues
                if i.severity == "error"
            )
            logger.warning(
                "Static review FAILED (hard gate) — spec rejected: %s",
                error_descriptions,
            )
            # Save replay log before raising
            if self._client is not None and self._replay_path and self.mode == "live":
                self._client.save_replay_log(self._replay_path)
            raise ValueError(
                f"Multi-agent spec '{spec.name}' failed static review: "
                + error_descriptions
            )

        trace["static_review_passed"] = True
        trace["generation_outcome"] = "success"

        # Step 8: Validate spec schema
        validation_errors = spec.validate()
        trace["validation_errors"] = validation_errors
        if validation_errors:
            logger.warning("Spec validation errors: %s", validation_errors)

        # Save replay log
        if self._client is not None and self._replay_path and self.mode == "live":
            self._client.save_replay_log(self._replay_path)

        return spec, trace

    def generate_batch(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
    ) -> list[tuple[StrategySpec, dict[str, Any]]]:
        """Generate specs for all ideas."""
        results = []
        for i in range(n_ideas):
            try:
                results.append(self.generate(
                    research_goal=research_goal,
                    n_ideas=n_ideas,
                    idea_index=i,
                ))
            except Exception as e:
                logger.warning("Failed to generate idea %d: %s", i, e)
        return results
