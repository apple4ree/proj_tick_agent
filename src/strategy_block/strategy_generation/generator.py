"""Strategy spec generator with template and OpenAI multi-agent backends.

Supports two backends:
- "template" (default): deterministic template-based generation
- "openai":  Multi-Agent pipeline via OpenAI structured outputs,
             with automatic fallback to template on failure

Static review hard gate
~~~~~~~~~~~~~~~~~~~~~~~
Both backends run ``StrategyReviewer.review()`` as a hard gate before
returning a spec.  If review fails:

- OpenAI backend → falls back to template.
- Template backend → raises ``StaticReviewError``.
- If both fail → ``StaticReviewError`` propagates to the caller.

A returned ``(spec, trace)`` is therefore **guaranteed** to have passed
static review.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategy_block.strategy_specs.schema import StrategySpec
from strategy_block.strategy_review.reviewer import StrategyReviewer

from .templates import IDEA_TEMPLATES, select_ideas_for_goal

logger = logging.getLogger(__name__)


class StaticReviewError(ValueError):
    """Raised when a generated spec fails the static review hard gate.

    Attributes
    ----------
    trace : dict
        The generation trace at the point of failure (for auditing).
    """

    def __init__(self, message: str, *, trace: dict | None = None) -> None:
        super().__init__(message)
        self.trace: dict = trace or {}


class StrategyGenerator:
    """Strategy spec generator with pluggable backend.

    Parameters
    ----------
    latency_ms : float
        Expected execution latency — used to calibrate holding periods
        and timeout exits. Higher latency → longer holding periods.
    backend : str
        "template" or "openai". Default "template".
    mode : str
        OpenAI client mode: "live", "mock", "replay". Only used when backend="openai".
    model : str
        OpenAI model name. Only used when backend="openai".
    replay_path : Path | str | None
        Replay log path. Only used when backend="openai".
    """

    def __init__(
        self,
        latency_ms: float = 1.0,
        *,
        backend: str = "template",
        mode: str = "live",
        model: str = "gpt-4o",
        replay_path: Path | str | None = None,
    ) -> None:
        self.latency_ms = latency_ms
        self.backend = backend
        self._multi_agent = None
        self._reviewer = StrategyReviewer()

        if backend == "openai":
            try:
                from .pipeline import MultiAgentPipeline
                self._multi_agent = MultiAgentPipeline(
                    mode=mode,
                    model=model,
                    latency_ms=latency_ms,
                    replay_path=replay_path,
                )
                logger.info("OpenAI multi-agent pipeline initialized (mode=%s)", mode)
            except Exception as e:
                logger.warning("Failed to init OpenAI pipeline: %s — will use template fallback", e)

    def generate(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
        idea_index: int = 0,
    ) -> tuple[StrategySpec, dict[str, Any]]:
        """Generate a single strategy spec.

        Returns
        -------
        (spec, trace) — spec is guaranteed to have passed static review.

        Raises
        ------
        StaticReviewError
            If all backends (including fallback) fail static review.
        """
        # Try OpenAI pipeline first if configured
        if self.backend == "openai" and self._multi_agent is not None:
            try:
                spec, trace = self._multi_agent.generate(
                    research_goal=research_goal,
                    n_ideas=n_ideas,
                    idea_index=idea_index,
                )
                # Pipeline enforces static review; if we reach here it passed.
                return spec, trace
            except Exception as e:
                logger.warning("OpenAI pipeline failed: %s — falling back to template", e)
                try:
                    fallback_spec, fallback_trace = self._generate_template(
                        research_goal=research_goal,
                        n_ideas=n_ideas,
                        idea_index=idea_index,
                    )
                except StaticReviewError as review_err:
                    # Both backends failed review — annotate and re-raise.
                    if review_err.trace:
                        review_err.trace["fallback_used"] = True
                        review_err.trace["fallback_reason"] = str(e)
                        review_err.trace["original_backend"] = "openai"
                        review_err.trace["generation_outcome"] = "failed"
                    raise

                fallback_trace["fallback_used"] = True
                fallback_trace["fallback_reason"] = str(e)
                fallback_trace["original_backend"] = "openai"
                fallback_trace["generation_outcome"] = "fallback_success"
                return fallback_spec, fallback_trace

        return self._generate_template(
            research_goal=research_goal,
            n_ideas=n_ideas,
            idea_index=idea_index,
        )

    def generate_batch(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
    ) -> list[tuple[StrategySpec, dict[str, Any]]]:
        """Generate specs for all selected ideas."""
        if self.backend == "openai" and self._multi_agent is not None:
            try:
                return self._multi_agent.generate_batch(
                    research_goal=research_goal,
                    n_ideas=n_ideas,
                )
            except Exception as e:
                logger.warning("OpenAI batch failed: %s — falling back to template", e)

        # Template fallback
        selected = select_ideas_for_goal(research_goal, n_ideas)
        results = []
        for i in range(len(selected)):
            try:
                results.append(
                    self._generate_template(
                        research_goal=research_goal,
                        n_ideas=n_ideas,
                        idea_index=i,
                    )
                )
            except StaticReviewError as e:
                logger.warning("Template idea %d failed review: %s", i, e)
        return results

    # ── Template backend ─────────────────────────────────────────────

    def _generate_template(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
        idea_index: int = 0,
    ) -> tuple[StrategySpec, dict[str, Any]]:
        """Template-based generation with static review hard gate."""
        selected = select_ideas_for_goal(research_goal, n_ideas)
        if idea_index >= len(selected):
            raise IndexError(
                f"idea_index={idea_index} but only {len(selected)} ideas "
                f"selected for goal={research_goal!r}"
            )
        template_idx = selected[idea_index]
        template = IDEA_TEMPLATES[template_idx]

        spec = self._build_spec(template, research_goal)
        trace = self._build_trace(
            research_goal=research_goal,
            n_ideas=n_ideas,
            idea_index=idea_index,
            template_idx=template_idx,
            selected_indices=selected,
            spec=spec,
        )

        # Static review hard gate
        review_result = self._reviewer.review(spec)
        trace["static_review"] = review_result.to_dict()
        trace["static_review_passed"] = review_result.passed

        if not review_result.passed:
            trace["generation_outcome"] = "failed"
            error_descriptions = "; ".join(
                i.description for i in review_result.issues
                if i.severity == "error"
            )
            raise StaticReviewError(
                f"Template spec '{spec.name}' failed static review: "
                + error_descriptions,
                trace=trace,
            )

        trace["generation_outcome"] = "success"
        return spec, trace

    def _build_spec(self, template: dict[str, Any], research_goal: str) -> StrategySpec:
        """Create a StrategySpec from a template, applying latency calibration."""
        data = copy.deepcopy(template)

        # Latency calibration: scale holding periods and timeouts
        latency_factor = max(1.0, self.latency_ms / 10.0)

        if "position_rule" in data:
            pr = data["position_rule"]
            base_hold = pr.get("holding_period_ticks", 10)
            pr["holding_period_ticks"] = max(1, int(base_hold * latency_factor))

        for exit_rule in data.get("exit_rules", []):
            if exit_rule.get("exit_type") == "time_exit":
                base_timeout = exit_rule.get("timeout_ticks", 300)
                exit_rule["timeout_ticks"] = max(10, int(base_timeout * latency_factor))

        # Metadata
        data["metadata"] = {
            "research_goal": research_goal,
            "idea_name": data["name"],
            "latency_ms": self.latency_ms,
            "pipeline": "template_generator_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return StrategySpec.from_dict(data)

    def _build_trace(
        self,
        *,
        research_goal: str,
        n_ideas: int,
        idea_index: int,
        template_idx: int,
        selected_indices: list[int],
        spec: StrategySpec,
    ) -> dict[str, Any]:
        """Build a generation trace for auditability."""
        return {
            "pipeline": "template_generator_v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": {
                "research_goal": research_goal,
                "n_ideas": n_ideas,
                "idea_index": idea_index,
                "latency_ms": self.latency_ms,
            },
            "selection": {
                "goal_matched_indices": selected_indices,
                "chosen_template_index": template_idx,
                "template_name": IDEA_TEMPLATES[template_idx]["name"],
            },
            "output": {
                "spec_name": spec.name,
                "spec_version": spec.version,
                "n_signal_rules": len(spec.signal_rules),
                "n_filters": len(spec.filters),
                "n_exit_rules": len(spec.exit_rules),
            },
            "latency_calibration": {
                "latency_ms": self.latency_ms,
                "factor": max(1.0, self.latency_ms / 10.0),
            },
            "fallback_used": False,
        }
