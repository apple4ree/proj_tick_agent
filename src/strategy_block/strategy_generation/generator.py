"""StrategySpec v2 generator (canonical, v2-only)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from strategy_block.strategy_review.v2.reviewer_v2 import StrategyReviewerV2

from .v2.lowering import lower_to_spec_v2
from .v2.templates_v2 import V2_TEMPLATES, get_v2_template

logger = logging.getLogger(__name__)


class StaticReviewError(ValueError):
    """Raised when a generated spec fails the static review hard gate."""

    def __init__(self, message: str, *, trace: dict | None = None) -> None:
        super().__init__(message)
        self.trace: dict = trace or {}


class StrategyGenerator:
    """StrategySpec v2 generator with template-default behavior."""

    def __init__(
        self,
        latency_ms: float = 1.0,
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
        del model, replay_path, allow_heuristic_fallback
        if spec_format != "v2":
            raise ValueError("Only spec_format='v2' is supported")

        self.latency_ms = latency_ms
        self.backend = backend
        self.mode = mode
        self.spec_format = "v2"
        self.allow_template_fallback = bool(allow_template_fallback)
        self.fail_on_fallback = bool(fail_on_fallback)
        self._reviewer_v2 = StrategyReviewerV2()

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
    ) -> dict[str, Any]:
        provenance = dict(trace.get("provenance") or {})
        provenance.setdefault("requested_backend", requested_backend)
        provenance.setdefault("effective_backend", effective_backend)
        provenance.setdefault("requested_mode", requested_mode)
        provenance.setdefault("effective_mode", effective_mode)
        provenance.setdefault("spec_format", "v2")
        provenance.setdefault("generation_class", "template_v2")
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
            if not self.allow_template_fallback:
                trace = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "generation_outcome": "failed_fallback_disabled",
                    "static_review_passed": False,
                    "fallback_used": True,
                    "fallback": {
                        "used": True,
                        "count": 1,
                        "events": [{
                            "stage": "generator",
                            "type": "fallback_blocked",
                            "reason": "openai backend is not available for v2 generation",
                            "severity": "high",
                        }],
                    },
                }
                trace = self._finalize_trace(
                    trace=trace,
                    requested_backend="openai",
                    effective_backend="template_v2",
                    requested_mode=self.mode,
                    effective_mode=self.mode,
                )
                raise StaticReviewError(
                    "openai backend is not available for v2 generation and fallback is disabled",
                    trace=trace,
                )

            logger.warning("openai backend requested; using canonical v2 template generation")
            spec, trace = self._generate_template_v2(
                research_goal=research_goal,
                n_ideas=n_ideas,
                idea_index=idea_index,
            )
            trace["fallback_used"] = True
            trace["fallback_reason"] = "openai backend redirected to v2 template"
            trace["generation_outcome"] = "fallback_success"
            trace = self._finalize_trace(
                trace=trace,
                requested_backend="openai",
                effective_backend="template_v2",
                requested_mode=self.mode,
                effective_mode=self.mode,
            )
            self._enforce_fail_on_fallback(trace, context="v2_openai_redirect")
            return spec, trace

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

        review_result = self._reviewer_v2.review(spec)
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
        return {
            "pipeline": "template_generator_v2",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": {
                "research_goal": research_goal,
                "n_ideas": n_ideas,
                "idea_index": idea_index,
                "latency_ms": self.latency_ms,
                "spec_format": "v2",
            },
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
