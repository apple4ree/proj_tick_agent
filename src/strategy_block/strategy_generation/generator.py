"""StrategySpec v2 generator (canonical, v2-only).

Supports two generation backends:
- template: deterministic template selection + lowering (default)
- openai:   OpenAI structured plan generation + lowering

TODO (observation lag): When market_data_delay_ms is added to the backtest
config, consider passing it to the planner prompt context alongside
latency_ms. This would let the planner generate more conservative execution
assumptions when observation lag is high (e.g., wider stop-loss, longer
time exits). Not required for the initial observation-lag implementation.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2

from .openai_client import OpenAIStrategyGenClient
from .v2.lowering import lower_to_spec_v2
from .v2.templates_v2 import V2_TEMPLATES, get_v2_template

logger = logging.getLogger(__name__)


class StaticReviewError(ValueError):
    """Raised when a generated spec fails the static review hard gate."""

    def __init__(self, message: str, *, trace: dict | None = None) -> None:
        super().__init__(message)
        self.trace: dict = trace or {}


class StrategyGenerator:
    """StrategySpec v2 generator with template and OpenAI backends."""

    def __init__(
        self,
        latency_ms: float = 1.0,
        backtest_environment: dict[str, Any] | None = None,
        *,
        backend: str = "template",
        mode: str = "live",
        model: str = "gpt-4o",
        replay_path=None,
        spec_format: str = "v2",
        allow_template_fallback: bool = True,
        allow_heuristic_fallback: bool = True,
        fail_on_fallback: bool = False,
    ) -> None:
        if spec_format != "v2":
            raise ValueError("Only spec_format='v2' is supported")

        self.latency_ms = latency_ms
        self.backtest_environment = copy.deepcopy(backtest_environment) if backtest_environment else None
        self.backend = backend
        self.mode = mode
        self.model = model
        self.replay_path = replay_path
        self.spec_format = "v2"
        self.allow_template_fallback = bool(allow_template_fallback)
        self.fail_on_fallback = bool(fail_on_fallback)
        self._reviewer_v2 = StrategyReviewerV2()

        # Lazily initialized for openai backend
        self._openai_client: OpenAIStrategyGenClient | None = None

    def _get_openai_client(self) -> OpenAIStrategyGenClient:
        """Get or create the OpenAI client."""
        if self._openai_client is None:
            from pathlib import Path
            rp = Path(self.replay_path) if self.replay_path else None
            self._openai_client = OpenAIStrategyGenClient(
                mode=self.mode,
                model=self.model,
                replay_path=rp,
            )
        return self._openai_client

    _V2_GOAL_KEYWORDS: dict[str, list[str]] = {
        "imbalance": ["imbalance_persist_momentum", "adaptive_execution_imbalance"],
        "momentum": ["imbalance_persist_momentum", "position_aware_time_exit_momentum"],
        "spread": ["spread_absorption_reversal", "latency_adaptive_passive_entry"],
        "reversion": ["rolling_mean_reversion", "loss_streak_degraded_reversion"],
        "mean reversion": ["rolling_mean_reversion", "pnl_stop_degraded_scalper"],
        "latency": ["latency_adaptive_passive_entry", "regime_adaptive_passive_reentry_block"],
        "state": ["stateful_cooldown_momentum", "loss_streak_degraded_reversion"],
        "cooldown": ["stateful_cooldown_momentum", "regime_adaptive_passive_reentry_block"],
        "regime": ["regime_filtered_persist_momentum", "regime_adaptive_passive_reentry_block"],
        "scalp": ["pnl_stop_degraded_scalper", "adaptive_execution_imbalance"],
        "execution": ["adaptive_execution_imbalance", "latency_adaptive_passive_entry"],
        "position": ["position_aware_time_exit_momentum", "pnl_stop_degraded_scalper"],
    }

    def _finalize_trace(
        self,
        *,
        trace: dict[str, Any],
        requested_backend: str,
        effective_backend: str,
        requested_mode: str,
        effective_mode: str,
        generation_class: str = "template_v2",
    ) -> dict[str, Any]:
        provenance = dict(trace.get("provenance") or {})
        provenance.setdefault("requested_backend", requested_backend)
        provenance.setdefault("effective_backend", effective_backend)
        provenance.setdefault("requested_mode", requested_mode)
        provenance.setdefault("effective_mode", effective_mode)
        provenance.setdefault("spec_format", "v2")
        provenance.setdefault("generation_class", generation_class)
        trace["provenance"] = provenance

        fallback_obj = dict(trace.get("fallback") or {})
        events = list(fallback_obj.get("events") or [])
        used = bool(trace.get("fallback_used")) or bool(events)
        fallback_obj["used"] = used
        fallback_obj["events"] = events
        fallback_obj["count"] = len(events)
        trace["fallback"] = fallback_obj
        trace["fallback_used"] = used

        if used and trace.get("generation_outcome") == "success":
            trace["generation_outcome"] = "fallback_success"

        return trace

    def _enforce_fail_on_fallback(self, trace: dict[str, Any], *, context: str) -> None:
        if self.fail_on_fallback and trace.get("fallback_used"):
            trace["generation_outcome"] = "failed_fallback_policy"
            raise StaticReviewError(
                f"Fallback occurred in {context} while fail_on_fallback=true",
                trace=trace,
            )

    def _select_v2_templates_for_goal(self, goal: str, n_ideas: int) -> list[str]:
        goal_lower = goal.lower()
        scored: dict[str, int] = {}

        for keyword, names in self._V2_GOAL_KEYWORDS.items():
            if keyword in goal_lower:
                for name in names:
                    scored[name] = scored.get(name, 0) + 1

        if scored:
            ranked = sorted(scored, key=lambda n: scored[n], reverse=True)
            for name in V2_TEMPLATES.keys():
                if name not in ranked:
                    ranked.append(name)
        else:
            ranked = list(V2_TEMPLATES.keys())

        return ranked[:n_ideas]

    def generate(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
        idea_index: int = 0,
    ) -> tuple[StrategySpecV2, dict[str, Any]]:
        if self.backend == "openai":
            return self._generate_openai_v2(research_goal=research_goal)

        spec, trace = self._generate_template_v2(
            research_goal=research_goal,
            n_ideas=n_ideas,
            idea_index=idea_index,
        )
        trace = self._finalize_trace(
            trace=trace,
            requested_backend=self.backend,
            effective_backend="template_v2",
            requested_mode=self.mode,
            effective_mode=self.mode,
        )
        self._enforce_fail_on_fallback(trace, context="v2_template")
        return spec, trace

    def _generate_openai_v2(
        self,
        *,
        research_goal: str,
    ) -> tuple[StrategySpecV2, dict[str, Any]]:
        """Generate via OpenAI structured plan + lowering."""
        from .v2.generation_rescue import GenerationRescue
        from .v2.openai_generation import generate_spec_v2_with_openai
        from .v2.utils.response_parser import PlanParseError

        try:
            client = self._get_openai_client()
            spec, trace = generate_spec_v2_with_openai(
                client=client,
                research_goal=research_goal,
                latency_ms=self.latency_ms,
                backtest_environment=self.backtest_environment,
                reviewer=self._reviewer_v2,
            )

            # Check review result (with exactly-one deterministic rescue attempt on eligible failures)
            if trace.get("static_review_passed") is False:
                pre_review = dict(trace.get("static_review") or {})
                rescue_result = GenerationRescue().maybe_rescue(
                    spec=spec,
                    review_result=pre_review,
                    backtest_environment=self.backtest_environment,
                )
                rescue_attempted = bool(rescue_result.metadata.get("eligible"))
                trace["generation_rescue_attempted"] = rescue_attempted
                trace["generation_rescue_applied"] = bool(rescue_result.applied)
                trace["generation_rescue_operations"] = list(rescue_result.operations)
                trace["rescue"] = {
                    "attempted": rescue_attempted,
                    "applied": bool(rescue_result.applied),
                    "operations": list(rescue_result.operations),
                    "reasons": list(rescue_result.reasons),
                    "metadata": dict(rescue_result.metadata),
                    "pre_review": pre_review,
                }

                if rescue_result.applied and isinstance(rescue_result.rescued_spec, StrategySpecV2):
                    rescued_spec = rescue_result.rescued_spec
                    post_rescue_review = self._reviewer_v2.review(
                        rescued_spec,
                        backtest_environment=self.backtest_environment,
                    )
                    trace["post_rescue_review"] = post_rescue_review.to_dict()
                    trace["static_review"] = post_rescue_review.to_dict()
                    trace["static_review_passed"] = post_rescue_review.passed
                    if post_rescue_review.passed:
                        spec = rescued_spec

                if trace.get("static_review_passed") is False:
                    error_issues = trace.get("static_review", {}).get("issues", [])
                    error_descriptions = "; ".join(
                        i.get("description", "") for i in error_issues if i.get("severity") == "error"
                    )
                    if not error_descriptions:
                        error_descriptions = "unknown static review error"
                    trace["generation_outcome"] = "failed"
                    trace = self._finalize_trace(
                        trace=trace,
                        requested_backend="openai",
                        effective_backend="openai_v2",
                        requested_mode=self.mode,
                        effective_mode=self.mode,
                        generation_class="openai_v2_plan",
                    )
                    raise StaticReviewError(
                        f"OpenAI v2 spec '{spec.name}' failed static review: {error_descriptions}",
                        trace=trace,
                    )

            trace["generation_outcome"] = "success"
            trace = self._finalize_trace(
                trace=trace,
                requested_backend="openai",
                effective_backend="openai_v2",
                requested_mode=self.mode,
                effective_mode=self.mode,
                generation_class="openai_v2_plan",
            )
            return spec, trace
        except (PlanParseError, StaticReviewError) as e:
            # If it's already a StaticReviewError with trace, re-raise as-is
            if isinstance(e, StaticReviewError):
                raise

            # OpenAI failed to produce a parseable plan — try template fallback
            logger.warning("OpenAI v2 generation failed: %s", e)

            if not self.allow_template_fallback:
                trace = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "generation_outcome": "failed_openai_no_fallback",
                    "static_review_passed": False,
                    "fallback_used": False,
                    "openai_error": str(e),
                }
                trace = self._finalize_trace(
                    trace=trace,
                    requested_backend="openai",
                    effective_backend="openai_v2",
                    requested_mode=self.mode,
                    effective_mode=self.mode,
                    generation_class="openai_v2_plan",
                )
                raise StaticReviewError(
                    f"OpenAI v2 generation failed and template fallback is disabled: {e}",
                    trace=trace,
                ) from e

            # Fallback to template
            logger.warning("Falling back to template_v2 generation")
            spec, trace = self._generate_template_v2(
                research_goal=research_goal,
                n_ideas=3,
                idea_index=0,
            )
            trace["fallback_used"] = True
            trace.setdefault("fallback", {})["events"] = trace.get("fallback", {}).get("events", []) + [{
                "stage": "generator",
                "type": "openai_to_template_fallback",
                "reason": str(e),
                "severity": "medium",
            }]
            trace["generation_outcome"] = "fallback_success"
            trace = self._finalize_trace(
                trace=trace,
                requested_backend="openai",
                effective_backend="template_v2",
                requested_mode=self.mode,
                effective_mode=self.mode,
                generation_class="template_v2",
            )
            self._enforce_fail_on_fallback(trace, context="openai_v2_fallback")
            return spec, trace

    def generate_batch(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
    ) -> list[tuple[StrategySpecV2, dict[str, Any]]]:
        selected = self._select_v2_templates_for_goal(research_goal, n_ideas)
        results: list[tuple[StrategySpecV2, dict[str, Any]]] = []
        for i in range(len(selected)):
            try:
                results.append(
                    self.generate(
                        research_goal=research_goal,
                        n_ideas=n_ideas,
                        idea_index=i,
                    )
                )
            except StaticReviewError as e:
                logger.warning("V2 template idea %d failed review: %s", i, e)
        return results

    def _generate_template_v2(
        self,
        *,
        research_goal: str,
        n_ideas: int = 3,
        idea_index: int = 0,
    ) -> tuple[StrategySpecV2, dict[str, Any]]:
        selected = self._select_v2_templates_for_goal(research_goal, n_ideas)
        if idea_index >= len(selected):
            raise IndexError(
                f"idea_index={idea_index} but only {len(selected)} ideas selected for goal={research_goal!r}"
            )

        template_name = selected[idea_index]
        template = get_v2_template(template_name)
        spec = lower_to_spec_v2(template)

        spec.metadata = dict(spec.metadata or {})
        spec.metadata.update({
            "research_goal": research_goal,
            "idea_name": template_name,
            "latency_ms": self.latency_ms,
            "spec_canonical": "v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })

        trace = self._build_trace_v2(
            research_goal=research_goal,
            n_ideas=n_ideas,
            idea_index=idea_index,
            template_name=template_name,
            selected_names=selected,
            spec=spec,
        )

        review_result = self._reviewer_v2.review(
            spec,
            backtest_environment=self.backtest_environment,
        )
        trace["static_review"] = review_result.to_dict()
        trace["static_review_passed"] = review_result.passed

        if not review_result.passed:
            trace["generation_outcome"] = "failed"
            error_descriptions = "; ".join(
                i.description for i in review_result.issues if i.severity == "error"
            )
            raise StaticReviewError(
                f"Template v2 spec '{spec.name}' failed static review: " + error_descriptions,
                trace=trace,
            )

        trace["generation_outcome"] = "success"
        return spec, trace

    def _build_trace_v2(
        self,
        *,
        research_goal: str,
        n_ideas: int,
        idea_index: int,
        template_name: str,
        selected_names: list[str],
        spec: StrategySpecV2,
    ) -> dict[str, Any]:
        input_ctx: dict[str, Any] = {
            "research_goal": research_goal,
            "n_ideas": n_ideas,
            "idea_index": idea_index,
            "latency_ms": self.latency_ms,
            "spec_format": "v2",
        }
        if self.backtest_environment is not None:
            input_ctx["backtest_environment"] = copy.deepcopy(self.backtest_environment)

        return {
            "pipeline": "template_generator_v2",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": input_ctx,
            "selection": {
                "goal_matched_templates": selected_names,
                "chosen_template_name": template_name,
            },
            "output": {
                "spec_name": spec.name,
                "spec_version": spec.version,
                "spec_format": "v2",
                "n_entry_policies": len(spec.entry_policies),
                "n_exit_policies": len(spec.exit_policies),
                "n_preconditions": len(spec.preconditions),
                "n_regimes": len(spec.regimes),
            },
            "fallback_used": False,
            "fallback": {"used": False, "count": 0, "events": []},
        }
