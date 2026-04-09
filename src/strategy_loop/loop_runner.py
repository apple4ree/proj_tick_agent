"""
strategy_loop/loop_runner.py
------------------------------
코드 전략 생성 → Hard Gate → 백테스트 → 피드백 생성 → Memory 저장 → 재생성
반복 주기를 실행하는 code-only LoopRunner.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strategy_loop.code_strategy import CodeStrategy
from strategy_loop.costeer.knowledge import CodeKnowledge
from strategy_loop.costeer.rag_memory import RagMemoryV1
from strategy_loop.date_ranges import DateRanges
from strategy_loop.distribution_filter import (
    DistributionFilterError,
    MAX_ENTRY_FREQ,
    MIN_ENTRY_FREQ,
    SAMPLE_SIZE,
    check_code_entry_frequency,
)
from strategy_loop.feedback_generator import FeedbackGenerator
from strategy_loop.goal_decomposer import decompose as decompose_goal
from strategy_loop.hard_gate import HardGateResult, validate_code
from strategy_loop.implementer_prompt_builder import build_implementer_messages
from strategy_loop.memory_store import MemoryStore
from strategy_loop.openai_client import OpenAIClient
from strategy_loop.planner_prompt_builder import build_planner_messages, parse_planner_response
from strategy_loop.precode_eval import evaluate_spec
from strategy_loop.prompt_builder import build_code_generation_messages
from strategy_loop.spec_review import review_spec
from strategy_loop.spec_schema import StrategySpec
from strategy_loop.threshold_optimizer import optimize_code_thresholds

logger = logging.getLogger(__name__)


@dataclass
class IterationRecord:
    iteration: int
    run_id: str
    strategy_name: str
    gate_result: HardGateResult
    backtest_summary: dict[str, Any] | None = None
    feedback: dict[str, Any] | None = None
    skipped: bool = False
    skip_reason: str = ""
    code: str | None = None


@dataclass
class LoopResult:
    iterations: list[IterationRecord] = field(default_factory=list)
    best_run_id: str | None = None
    verdict: str = "no_pass"            # "pass" | "no_pass"
    oos_backtest_summary: dict[str, Any] | None = None
    oos_verdict: str = "no_oos"         # "pass_oos" | "fail_oos" | "no_oos"


class LoopRunner:
    def __init__(
        self,
        client: OpenAIClient,
        memory_dir: str | Path,
        output_dir: str | Path = "outputs/backtests",
        optimize_n_trials: int = 20,
    ) -> None:
        self._client = client
        self._memory = MemoryStore(memory_dir)
        self._feedback_gen = FeedbackGenerator(client)
        self._output_dir = str(output_dir)
        self._optimize_n_trials = optimize_n_trials

    @staticmethod
    def _distribution_filter_args(cfg: dict[str, Any] | None) -> dict[str, float | int]:
        dist_cfg = (cfg or {}).get("distribution_filter", {})
        sample_size = max(1, int(dist_cfg.get("sample_size", SAMPLE_SIZE)))
        min_freq = float(dist_cfg.get("min_entry_freq", MIN_ENTRY_FREQ))
        max_freq = float(dist_cfg.get("max_entry_freq", MAX_ENTRY_FREQ))
        return {
            "sample_size": sample_size,
            "min_freq": min_freq,
            "max_freq": max_freq,
        }

    def run(
        self,
        research_goal: str,
        n_iterations: int,
        data_dir: str | Path,
        symbols: list[str],
        date_ranges: DateRanges,
        cfg: dict[str, Any] | None = None,
    ) -> LoopResult:
        """Run the full iterative code-generation loop. Returns a LoopResult."""
        result = LoopResult()
        previous_feedback: dict[str, Any] | None = None

        # Session-level tracking: what has been tried in this run
        session_attempts: list[dict[str, Any]] = []
        best_code_so_far: str | None = None
        best_net_pnl: float = -float("inf")
        consecutive_non_pass: int = 0

        rag_cfg = (cfg or {}).get("rag_memory", {})
        max_failures = max(1, int(rag_cfg.get("max_failures", 5)))
        max_successes = max(1, int(rag_cfg.get("max_successes", 3)))
        rag_memory = RagMemoryV1(
            max_failures=max_failures,
            max_successes=max_successes,
        )

        # Decompose research goal once for the whole session
        goal_decomp = decompose_goal(research_goal)
        logger.info(
            "Goal decomposition: archetype=%s (%s), features=%s",
            goal_decomp.archetype,
            goal_decomp.archetype_name,
            goal_decomp.suggested_features,
        )

        for i in range(n_iterations):
            run_id = str(uuid.uuid4())[:8]
            strategy_name = f"code_strategy_v{i + 1}"
            logger.info("─── Iteration %d / %d  (run_id=%s) ───", i + 1, n_iterations, run_id)

            # 1) Generate code via LLM
            insights = self._memory.load_insights()
            failure_patterns = self._memory.load_failure_patterns()
            rag_ctx = rag_memory.format_for_prompt()
            messages = build_code_generation_messages(
                research_goal=research_goal,
                memory_insights=insights,
                failure_patterns=failure_patterns,
                previous_feedback=previous_feedback,
                session_attempts=session_attempts,
                best_code_so_far=best_code_so_far,
                stuck_count=consecutive_non_pass,
                goal_decomposition=goal_decomp,
                rag_context=rag_ctx,
            )

            try:
                code = self._client.chat_code(messages)
            except Exception as exc:
                logger.error("LLM code generation failed: %s", exc)
                rec = IterationRecord(
                    iteration=i + 1,
                    run_id=run_id,
                    strategy_name=strategy_name,
                    code=None,
                    gate_result=HardGateResult(passed=False, errors=[str(exc)]),
                    skipped=True,
                    skip_reason="llm_error",
                )
                result.iterations.append(rec)
                continue

            # 2) Hard Gate
            gate = validate_code(code)
            rec = IterationRecord(
                iteration=i + 1,
                run_id=run_id,
                strategy_name=strategy_name,
                code=code,
                gate_result=gate,
            )
            if not gate.passed:
                logger.warning("Code hard gate failed: %s", gate.errors)
                rec.skipped = True
                rec.skip_reason = "hard_gate_fail"
                result.iterations.append(rec)
                continue

            # 2.5) Optuna 상수 최적화
            if self._optimize_n_trials > 0:
                code = self._optimize_code(
                    code=code,
                    data_dir=data_dir,
                    symbol=symbols[0],
                    start_date=date_ranges.is_start,
                    end_date=date_ranges.is_end,
                    cfg=cfg,
                )
                rec.code = code

            # 3) Backtest IS
            try:
                bt_summary, _is_run_dirs = self._run_backtest_multi_code(
                    code=code,
                    strategy_name=strategy_name,
                    data_dir=data_dir,
                    symbols=symbols,
                    start_date=date_ranges.is_start,
                    end_date=date_ranges.is_end,
                    cfg=cfg,
                )
            except DistributionFilterError as exc:
                logger.warning("Distribution filter rejected code: %s", exc.reason)
                rec.skipped = True
                rec.skip_reason = f"dist_filter: {exc.reason}"
                session_attempts.append({
                    "iteration": i + 1,
                    "strategy_name": strategy_name,
                    "entry_frequency": exc.entry_frequency,
                    "net_pnl": 0.0,
                    "n_fills": 0.0,
                    "verdict": "dist_filter",
                    "primary_issue": exc.reason,
                })
                previous_feedback = {
                    "verdict": "retry",
                    "diagnosis_code": "distribution_filter",
                    "severity": "parametric",
                    "control_mode": "repair",
                    "primary_issue": exc.reason,
                    "structural_change_required": False,
                    "controller_reasons": [exc.reason],
                    "suggestions": [
                        "Adjust UPPER_CASE threshold constants so generate_signal returns 1 "
                        "between 0.1% and 50% of states.",
                        (
                            "entry_too_sparse → lower imbalance/threshold constants "
                            "or relax filter conditions."
                        ),
                        (
                            "entry_too_frequent → raise imbalance/threshold constants "
                            "or add more filters."
                        ),
                    ],
                    "issues": [],
                }
                consecutive_non_pass += 1
                result.iterations.append(rec)
                continue
            except Exception as exc:
                logger.error("Code backtest failed: %s", exc)
                rec.skipped = True
                rec.skip_reason = f"backtest_error: {exc}"
                result.iterations.append(rec)
                continue

            rec.backtest_summary = bt_summary

            # 4) LLM Feedback
            feedback = self._feedback_gen.generate(
                code=code,
                backtest_summary=bt_summary,
                memory_insights=insights,
            )
            rec.feedback = feedback
            logger.info("Feedback verdict: %s", feedback["verdict"])

            for _rd in _is_run_dirs:
                _write_strategy_info(_rd, code=code, feedback=feedback, strategy_text=None, iteration=i + 1)

            # 5) Save to memory
            self._memory.save_strategy(run_id, strategy_name, code, bt_summary, feedback)
            if feedback.get("suggestions"):
                self._memory.append_insights(feedback["suggestions"])
            if feedback.get("issues"):
                self._memory.append_failure_patterns(feedback["issues"])

            # code 모드: RAG 메모리 추가
            net_pnl_for_rag = bt_summary.get("net_pnl") or 0.0
            derived_metrics = feedback.get("derived_metrics") if isinstance(feedback.get("derived_metrics"), dict) else {}
            entry_frequency_for_rag = derived_metrics.get("entry_frequency")
            if not isinstance(entry_frequency_for_rag, (int, float)):
                sig_count = bt_summary.get("signal_count") or 0.0
                n_st = bt_summary.get("n_states") or 1.0
                entry_frequency_for_rag = sig_count / n_st
            rag_memory.add(CodeKnowledge(
                task_name=strategy_name,
                code=code,
                verdict=feedback["verdict"],
                diagnosis_code=str(feedback.get("diagnosis_code", "")),
                net_pnl=net_pnl_for_rag,
                entry_frequency=float(entry_frequency_for_rag),
                primary_issue=feedback.get("primary_issue", ""),
                suggestions=feedback.get("suggestions", []),
            ))

            # 6) Update session tracking
            net_pnl = bt_summary.get("net_pnl") or 0.0
            signal_count = bt_summary.get("signal_count") or 0.0
            n_states = bt_summary.get("n_states") or 1.0
            fallback_entry_frequency = signal_count / n_states
            entry_frequency = feedback.get("derived_metrics", {}).get("entry_frequency", fallback_entry_frequency)
            if not isinstance(entry_frequency, (int, float)):
                entry_frequency = fallback_entry_frequency
            current_issue = feedback.get("primary_issue", "")
            session_attempts.append({
                "iteration": i + 1,
                "strategy_name": strategy_name,
                "entry_frequency": float(entry_frequency),
                "net_pnl": net_pnl,
                "n_fills": bt_summary.get("n_fills") or 0.0,
                "verdict": feedback["verdict"],
                "primary_issue": current_issue,
            })
            if net_pnl > best_net_pnl:
                best_net_pnl = net_pnl
                best_code_so_far = code if net_pnl > 0 else None

            # Track consecutive non-passing iterations (stuck detection)
            if feedback["verdict"] != "pass":
                consecutive_non_pass += 1
            else:
                consecutive_non_pass = 0

            previous_feedback = feedback
            result.iterations.append(rec)

            # 7) Check stop condition
            if feedback["verdict"] == "pass":
                if date_ranges.has_oos:
                    logger.info("IS passed. Running OOS validation (%s ~ %s)...",
                                date_ranges.oos_start, date_ranges.oos_end)
                    try:
                        oos_summary, _ = self._run_backtest_multi_code(
                            code=code,
                            strategy_name=strategy_name,
                            data_dir=data_dir,
                            symbols=symbols,
                            start_date=date_ranges.oos_start,
                            end_date=date_ranges.oos_end,
                            cfg=cfg,
                        )
                        result.oos_backtest_summary = oos_summary
                        oos_net_pnl = oos_summary.get("net_pnl", 0.0) or 0.0
                        if oos_net_pnl > 0:
                            result.oos_verdict = "pass_oos"
                            result.best_run_id = run_id
                            result.verdict = "pass"
                            logger.info("OOS also passed (net_pnl=%.1f). Stopping loop.", oos_net_pnl)
                            break

                        result.oos_verdict = "fail_oos"
                        logger.warning("OOS failed (net_pnl=%.1f). Continuing loop.", oos_net_pnl)
                        previous_feedback = {
                            **feedback,
                            "primary_issue": (
                                f"IS passed but OOS failed (oos_net_pnl={oos_net_pnl:.0f}). "
                                "Strategy may be overfit to IS period. "
                                "Try a more robust entry condition."
                            ),
                            "diagnosis_code": "oos_fail",
                            "severity": "structural",
                            "control_mode": "explore",
                            "structural_change_required": True,
                            "verdict": "retry",
                            "controller_reasons": [
                                f"oos_net_pnl={oos_net_pnl:.4f} <= 0",
                            ],
                        }
                        consecutive_non_pass += 1
                        continue
                    except DistributionFilterError as exc:
                        logger.warning("OOS distribution filter rejected: %s", exc.reason)
                        result.oos_verdict = "fail_oos"
                        previous_feedback = {
                            **feedback,
                            "primary_issue": (
                                f"IS passed but OOS entry condition never fired ({exc.reason}). "
                                "Strategy is likely overfit to IS period. Use a more general entry condition."
                            ),
                            "diagnosis_code": "oos_distribution_filter",
                            "severity": "structural",
                            "control_mode": "explore",
                            "structural_change_required": True,
                            "verdict": "retry",
                            "controller_reasons": [exc.reason],
                        }
                        consecutive_non_pass += 1
                        continue
                    except Exception as exc:
                        logger.error("OOS backtest failed: %s", exc)
                        result.oos_verdict = "no_oos"
                        result.best_run_id = run_id
                        result.verdict = "pass"
                        break
                else:
                    result.best_run_id = run_id
                    result.verdict = "pass"
                    logger.info("Strategy passed (no OOS configured). Stopping loop.")
                    break

        return result

    def run_spec_centric(
        self,
        research_goal: str,
        max_plan_iterations: int,
        max_code_attempts: int,
        data_dir: str | Path,
        symbols: list[str],
        date_ranges: DateRanges,
        cfg: dict[str, Any] | None = None,
        precode_eval_threshold: float = 0.50,
    ) -> LoopResult:
        """Spec-centric pipeline: planner → spec_review → precode_eval → implementer.

        Outer loop iterates over plan proposals (up to max_plan_iterations);
        inner loop retries code generation for a fixed spec (up to max_code_attempts).

        Routing rules:
          - spec_review invalid               → skip plan (no code attempts)
          - precode_eval.overall < threshold  → skip plan (no code attempts)
          - structural feedback (structural_change_required=True,
            severity=="structural", or diagnosis_code in
            {oos_fail, oos_distribution_filter})
                                              → break inner loop immediately;
                                                plan_outcome="structural_fail"
          - parametric feedback (no_trades, repair)
                                              → stay in inner loop (retry code)
          - code_attempt >= max_code_attempts → exit inner loop, request new plan
          - IS pass + no OOS configured       → return (verdict="pass")
          - IS pass + OOS pass                → return (verdict="pass")
          - IS pass + OOS fail                → structural routing (see above)
        """
        result = LoopResult()
        overall_iteration = 0   # monotone counter across all plans/attempts

        rag_cfg = (cfg or {}).get("rag_memory", {})
        max_failures = max(1, int(rag_cfg.get("max_failures", 5)))
        max_successes = max(1, int(rag_cfg.get("max_successes", 3)))
        rag_memory = RagMemoryV1(max_failures=max_failures, max_successes=max_successes)

        goal_decomp = decompose_goal(research_goal)
        logger.info(
            "Goal decomposition: archetype=%s (%s), features=%s",
            goal_decomp.archetype,
            goal_decomp.archetype_name,
            goal_decomp.suggested_features,
        )

        previous_plan_feedback: str | None = None

        for plan_iter in range(max_plan_iterations):
            plan_id = str(uuid.uuid4())[:8]
            logger.info(
                "═══ Plan iteration %d / %d  (plan_id=%s) ═══",
                plan_iter + 1, max_plan_iterations, plan_id,
            )

            # ── Stage 1: Planner LLM ─────────────────────────────────
            planner_memory = self._memory.load_planner_memory()
            planner_msgs = build_planner_messages(
                research_goal=research_goal,
                goal_decomposition=goal_decomp,
                planner_memory=planner_memory,
                previous_plan_feedback=previous_plan_feedback,
            )
            try:
                planner_response = self._client.chat_json(
                    planner_msgs, context="planner"
                )
                strategy_text, spec_raw = parse_planner_response(planner_response)
                spec = StrategySpec.from_dict(spec_raw)
            except Exception as exc:
                logger.error("Planner LLM failed: %s", exc)
                previous_plan_feedback = f"Planner call failed: {exc}"
                continue

            logger.info(
                "  Planner: archetype=%s (%s), entry_conds=%d, derived=%d, params=%d",
                spec.archetype, spec.archetype_name,
                len(spec.entry_conditions), len(spec.derived_features),
                len(spec.tunable_params),
            )

            # ── Stage 2: Spec review ─────────────────────────────────
            review = review_spec(spec)
            if not review.valid:
                logger.warning(
                    "  Spec review failed: %s", review.errors
                )
                previous_plan_feedback = (
                    "Spec review failed — fix these errors before proceeding:\n"
                    + "\n".join(f"  - {e}" for e in review.errors)
                )
                self._memory.save_plan(
                    plan_id=plan_id,
                    strategy_text=strategy_text,
                    spec=spec.to_dict(),
                    spec_review=review.to_dict(),
                    precode_eval={"version": "1", "go": False, "overall": 0.0,
                                  "scores": {}, "notes": review.errors},
                    outcome="spec_invalid",
                    primary_issue="; ".join(review.errors),
                )
                continue

            # normalized_spec is the canonical implementer input from here on
            normalized_spec = review.normalized_spec

            # ── Stage 3: Pre-code evaluation ─────────────────────────
            pce = evaluate_spec(normalized_spec)
            logger.info(
                "  PrecodeEval: overall=%.2f, go=%s, notes=%s",
                pce.overall, pce.go, pce.notes,
            )

            # Save plan record (outcome will be updated after inner loop)
            self._memory.save_plan(
                plan_id=plan_id,
                strategy_text=strategy_text,
                spec=spec.to_dict(),
                spec_review=review.to_dict(),
                precode_eval=pce.to_dict(),
            )

            if not pce.go or pce.overall < precode_eval_threshold:
                previous_plan_feedback = (
                    f"Pre-code eval score {pce.overall:.2f} < threshold {precode_eval_threshold}. "
                    "Improve these dimensions:\n"
                    + "\n".join(f"  - {n}" for n in pce.notes)
                )
                self._memory.update_plan_outcome(
                    plan_id=plan_id,
                    outcome="precode_rejected",
                    primary_issue=previous_plan_feedback,
                )
                continue

            # ── Stage 4: Inner code loop ──────────────────────────────
            code_attempts: list[dict[str, Any]] = []
            best_code_for_plan: str | None = None
            best_net_pnl_for_plan: float = -float("inf")
            previous_code_feedback: dict[str, Any] | None = None
            consecutive_code_fails: int = 0
            plan_outcome = "no_code_pass"

            for code_attempt in range(max_code_attempts):
                overall_iteration += 1
                run_id = str(uuid.uuid4())[:8]
                strategy_name = f"spec_v{plan_iter + 1}_code_v{code_attempt + 1}"
                logger.info(
                    "  ─ Code attempt %d / %d  (run_id=%s)",
                    code_attempt + 1, max_code_attempts, run_id,
                )

                # 4a) Build implementer messages from normalized_spec
                insights = self._memory.load_insights()
                failure_patterns = self._memory.load_failure_patterns()
                rag_ctx = rag_memory.format_for_prompt()
                messages = build_implementer_messages(
                    spec=normalized_spec,
                    session_attempts=code_attempts,
                    previous_feedback=previous_code_feedback,
                    best_code_so_far=best_code_for_plan,
                    stuck_count=consecutive_code_fails,
                    rag_context=rag_ctx,
                )

                try:
                    code = self._client.chat_code(messages)
                except Exception as exc:
                    logger.error("Implementer LLM failed: %s", exc)
                    rec = IterationRecord(
                        iteration=overall_iteration,
                        run_id=run_id,
                        strategy_name=strategy_name,
                        code=None,
                        gate_result=HardGateResult(passed=False, errors=[str(exc)]),
                        skipped=True,
                        skip_reason="llm_error",
                    )
                    result.iterations.append(rec)
                    consecutive_code_fails += 1
                    continue

                # 4b) Hard gate
                gate = validate_code(code)
                rec = IterationRecord(
                    iteration=overall_iteration,
                    run_id=run_id,
                    strategy_name=strategy_name,
                    code=code,
                    gate_result=gate,
                )
                if not gate.passed:
                    logger.warning("  Code hard gate failed: %s", gate.errors)
                    rec.skipped = True
                    rec.skip_reason = "hard_gate_fail"
                    result.iterations.append(rec)
                    consecutive_code_fails += 1
                    continue

                # 4c) Optuna optimization
                if self._optimize_n_trials > 0:
                    code = self._optimize_code(
                        code=code,
                        data_dir=data_dir,
                        symbol=symbols[0],
                        start_date=date_ranges.is_start,
                        end_date=date_ranges.is_end,
                        cfg=cfg,
                    )
                    rec.code = code

                # 4d) Backtest IS
                try:
                    bt_summary, _is_run_dirs = self._run_backtest_multi_code(
                        code=code,
                        strategy_name=strategy_name,
                        data_dir=data_dir,
                        symbols=symbols,
                        start_date=date_ranges.is_start,
                        end_date=date_ranges.is_end,
                        cfg=cfg,
                    )
                except DistributionFilterError as exc:
                    logger.warning("  Distribution filter rejected code: %s", exc.reason)
                    rec.skipped = True
                    rec.skip_reason = f"dist_filter: {exc.reason}"
                    code_attempts.append({
                        "iteration": code_attempt + 1,
                        "strategy_name": strategy_name,
                        "entry_frequency": exc.entry_frequency,
                        "net_pnl": 0.0,
                        "n_fills": 0.0,
                        "verdict": "dist_filter",
                        "primary_issue": exc.reason,
                    })
                    previous_code_feedback = {
                        "verdict": "retry",
                        "diagnosis_code": "distribution_filter",
                        "severity": "parametric",
                        "control_mode": "repair",
                        "primary_issue": exc.reason,
                        "structural_change_required": False,
                        "controller_reasons": [exc.reason],
                        "suggestions": [
                            "Adjust UPPER_CASE threshold constants so generate_signal returns 1 "
                            "between 0.1% and 50% of states.",
                        ],
                        "issues": [],
                    }
                    consecutive_code_fails += 1
                    result.iterations.append(rec)
                    continue
                except Exception as exc:
                    logger.error("  Code backtest failed: %s", exc)
                    rec.skipped = True
                    rec.skip_reason = f"backtest_error: {exc}"
                    result.iterations.append(rec)
                    consecutive_code_fails += 1
                    continue

                rec.backtest_summary = bt_summary

                # 4e) LLM Feedback
                feedback = self._feedback_gen.generate(
                    code=code,
                    backtest_summary=bt_summary,
                    memory_insights=insights,
                )
                rec.feedback = feedback
                logger.info("  Feedback verdict: %s", feedback["verdict"])

                for _rd in _is_run_dirs:
                    _write_strategy_info(_rd, code=code, feedback=feedback, strategy_text=strategy_text, iteration=plan_iter + 1)

                # 4f) Save to memory
                self._memory.save_strategy(
                    run_id, strategy_name, code, bt_summary, feedback
                )
                if feedback.get("suggestions"):
                    self._memory.append_insights(feedback["suggestions"])
                if feedback.get("issues"):
                    self._memory.append_failure_patterns(feedback["issues"])

                net_pnl_for_rag = bt_summary.get("net_pnl") or 0.0
                derived = feedback.get("derived_metrics") or {}
                entry_freq_for_rag = derived.get("entry_frequency")
                if not isinstance(entry_freq_for_rag, (int, float)):
                    sig_count = bt_summary.get("signal_count") or 0.0
                    n_st = bt_summary.get("n_states") or 1.0
                    entry_freq_for_rag = sig_count / n_st
                rag_memory.add(CodeKnowledge(
                    task_name=strategy_name,
                    code=code,
                    verdict=feedback["verdict"],
                    diagnosis_code=str(feedback.get("diagnosis_code", "")),
                    net_pnl=net_pnl_for_rag,
                    entry_frequency=float(entry_freq_for_rag),
                    primary_issue=feedback.get("primary_issue", ""),
                    suggestions=feedback.get("suggestions", []),
                ))

                # 4g) Update code-attempt tracking
                net_pnl = bt_summary.get("net_pnl") or 0.0
                sig_count = bt_summary.get("signal_count") or 0.0
                n_states = bt_summary.get("n_states") or 1.0
                entry_freq = derived.get("entry_frequency", sig_count / n_states)
                if not isinstance(entry_freq, (int, float)):
                    entry_freq = sig_count / n_states

                code_attempts.append({
                    "iteration": code_attempt + 1,
                    "strategy_name": strategy_name,
                    "entry_frequency": float(entry_freq),
                    "net_pnl": net_pnl,
                    "n_fills": bt_summary.get("n_fills") or 0.0,
                    "verdict": feedback["verdict"],
                    "primary_issue": feedback.get("primary_issue", ""),
                })

                if net_pnl > best_net_pnl_for_plan:
                    best_net_pnl_for_plan = net_pnl
                    best_code_for_plan = code if net_pnl > 0 else None

                if feedback["verdict"] != "pass":
                    consecutive_code_fails += 1
                else:
                    consecutive_code_fails = 0

                previous_code_feedback = feedback
                result.iterations.append(rec)

                # 4h) Structural-failure check — exit inner loop immediately
                _STRUCTURAL_DIAG = frozenset({"oos_fail", "oos_distribution_filter"})
                _is_structural = (
                    feedback.get("structural_change_required") is True
                    or feedback.get("severity") == "structural"
                    or feedback.get("diagnosis_code") in _STRUCTURAL_DIAG
                )
                if _is_structural and feedback["verdict"] != "pass":
                    logger.warning(
                        "  Structural failure (%s) — abandoning this spec, requesting new plan.",
                        feedback.get("diagnosis_code", "?"),
                    )
                    plan_outcome = "structural_fail"
                    previous_plan_feedback = (
                        f"Structural failure: {feedback.get('primary_issue', feedback.get('diagnosis_code', ''))}"
                        " — design a different strategy (different archetype or features)."
                    )
                    self._memory.update_plan_outcome(
                        plan_id,
                        outcome="structural_fail",
                        primary_issue=previous_plan_feedback,
                        best_net_pnl=max(best_net_pnl_for_plan, 0.0),
                    )
                    break   # exit inner code loop; outer plan loop will continue

                # 4i) Check stop / OOS
                if feedback["verdict"] == "pass":
                    if date_ranges.has_oos:
                        logger.info(
                            "  IS passed. Running OOS (%s ~ %s)...",
                            date_ranges.oos_start, date_ranges.oos_end,
                        )
                        try:
                            oos_summary, _ = self._run_backtest_multi_code(
                                code=code,
                                strategy_name=strategy_name,
                                data_dir=data_dir,
                                symbols=symbols,
                                start_date=date_ranges.oos_start,
                                end_date=date_ranges.oos_end,
                                cfg=cfg,
                            )
                            result.oos_backtest_summary = oos_summary
                            oos_net_pnl = oos_summary.get("net_pnl", 0.0) or 0.0
                            if oos_net_pnl > 0:
                                result.oos_verdict = "pass_oos"
                                result.best_run_id = run_id
                                result.verdict = "pass"
                                plan_outcome = "pass"
                                logger.info(
                                    "  OOS also passed (net_pnl=%.1f). Stopping.", oos_net_pnl
                                )
                                self._memory.update_plan_outcome(
                                    plan_id, outcome="pass",
                                    best_net_pnl=oos_net_pnl,
                                )
                                break

                            # OOS fail → structural: exit inner loop, request new plan
                            result.oos_verdict = "fail_oos"
                            logger.warning(
                                "  OOS failed (net_pnl=%.1f). Requesting new plan.", oos_net_pnl
                            )
                            plan_outcome = "structural_fail"
                            previous_plan_feedback = (
                                f"IS passed but OOS failed (oos_net_pnl={oos_net_pnl:.0f}). "
                                "Strategy is overfit to IS period. "
                                "Design a more robust strategy."
                            )
                            self._memory.update_plan_outcome(
                                plan_id,
                                outcome="structural_fail",
                                primary_issue=previous_plan_feedback,
                                best_net_pnl=max(best_net_pnl_for_plan, 0.0),
                            )
                            break   # exit inner loop

                        except DistributionFilterError as exc:
                            # OOS dist filter → structural: exit inner loop
                            logger.warning(
                                "  OOS distribution filter rejected: %s", exc.reason
                            )
                            result.oos_verdict = "fail_oos"
                            plan_outcome = "structural_fail"
                            previous_plan_feedback = (
                                f"IS passed but OOS entry never fired ({exc.reason}). "
                                "Strategy is overfit. Design a more general strategy."
                            )
                            self._memory.update_plan_outcome(
                                plan_id,
                                outcome="structural_fail",
                                primary_issue=previous_plan_feedback,
                                best_net_pnl=max(best_net_pnl_for_plan, 0.0),
                            )
                            break   # exit inner loop

                        except Exception as exc:
                            logger.error("  OOS backtest failed: %s", exc)
                            result.oos_verdict = "no_oos"
                            result.best_run_id = run_id
                            result.verdict = "pass"
                            plan_outcome = "pass"
                            self._memory.update_plan_outcome(
                                plan_id, outcome="pass", best_net_pnl=best_net_pnl_for_plan
                            )
                            break
                    else:
                        result.best_run_id = run_id
                        result.verdict = "pass"
                        plan_outcome = "pass"
                        logger.info("  Strategy passed (no OOS). Stopping.")
                        self._memory.update_plan_outcome(
                            plan_id, outcome="pass", best_net_pnl=best_net_pnl_for_plan
                        )
                        break

                if result.verdict == "pass":
                    break   # propagate out of plan loop too

            # ── end of inner code loop ────────────────────────────────
            if result.verdict == "pass":
                break

            # Update plan outcome if still no pass
            if plan_outcome == "no_code_pass":
                last_issue = (
                    code_attempts[-1].get("primary_issue", "")
                    if code_attempts else "no code attempts succeeded"
                )
                self._memory.update_plan_outcome(
                    plan_id,
                    outcome=plan_outcome,
                    primary_issue=last_issue,
                    best_net_pnl=max(best_net_pnl_for_plan, 0.0),
                )
                previous_plan_feedback = (
                    f"All {max_code_attempts} code attempts failed for this spec. "
                    f"Last issue: {last_issue}. "
                    "Design a fundamentally different strategy spec."
                )

        return result

    # ── internal ──────────────────────────────────────────────────────

    # Fields that should be summed across symbols (absolute magnitudes).
    # Everything else that is numeric is averaged.
    # Excluded intentionally:
    #   var_95, expected_shortfall_95  — tail risk quantiles are NOT additive across symbols
    #   alpha_contribution             — always 0 due to implementation artifact; unreliable
    #   execution_contribution         — derived from broken alpha; unreliable
    #   cost_contribution              — derive from summed total_commission + total_slippage instead
    #   timing_contribution            — derived metric; not additively meaningful
    _SUM_FIELDS: frozenset[str] = frozenset({
        "n_fills", "n_states", "signal_count", "child_order_count", "parent_order_count",
        "total_realized_pnl", "total_unrealized_pnl", "net_pnl",
        "total_commission", "total_slippage", "total_impact",
    })

    @staticmethod
    def _aggregate_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge per-symbol backtest summaries into one aggregate summary."""
        if len(summaries) == 1:
            return summaries[0]

        sum_fields = LoopRunner._SUM_FIELDS
        agg: dict[str, Any] = {}
        numeric_counts: dict[str, int] = {}

        for s in summaries:
            for k, v in s.items():
                if not isinstance(v, (int, float)):
                    if k not in agg:
                        agg[k] = v
                    continue
                if k in sum_fields:
                    agg[k] = agg.get(k, 0.0) + float(v)
                else:
                    agg[k] = agg.get(k, 0.0) + float(v)
                    numeric_counts[k] = numeric_counts.get(k, 0) + 1

        for k in numeric_counts:
            if k not in sum_fields:
                agg[k] = agg[k] / numeric_counts[k]

        agg["n_symbols"] = len(summaries)
        return agg

    def _optimize_code(
        self,
        code: str,
        data_dir: str | Path,
        symbol: str,
        start_date: str,
        end_date: str | None,
        cfg: dict[str, Any] | None,
    ) -> str:
        """코드 전략의 UPPER_CASE 상수를 Optuna로 최적화한다."""
        from scripts.backtest import backtest_config_from_cfg, build_states_for_range

        cfg = cfg or {}
        bt_cfg = cfg.get("backtest", {})
        opt_cfg = cfg.get("optimization", {})

        resample = bt_cfg.get("resample", "1s")
        lookback = bt_cfg.get("trade_lookback", 100)

        lambda_mdd = float(opt_cfg.get("lambda_mdd", 1.0))
        raw_stage_prefixes = opt_cfg.get("stage_prefixes", [0.2, 0.5, 1.0])
        if isinstance(raw_stage_prefixes, (list, tuple)):
            stage_prefixes = [float(v) for v in raw_stage_prefixes]
        else:
            stage_prefixes = [0.2, 0.5, 1.0]
        enable_pruning = bool(opt_cfg.get("enable_pruning", True))

        try:
            states = build_states_for_range(
                data_dir=data_dir,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                resample_freq=resample,
                trade_lookback=lookback,
            )
        except Exception as exc:
            logger.warning("Could not load states for code optimization: %s", exc)
            return code

        try:
            bt_config = backtest_config_from_cfg(
                cfg,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            opt_result = optimize_code_thresholds(
                code=code,
                states=states,
                backtest_config=bt_config,
                data_dir=data_dir,
                n_trials=self._optimize_n_trials,
                lambda_mdd=lambda_mdd,
                stage_prefixes=stage_prefixes,
                enable_pruning=enable_pruning,
            )
        except Exception as exc:
            logger.warning("Code Optuna optimization failed: %s", exc)
            return code

        logger.info(
            "  Code Optuna: best_score=%.2f, best_net_return_bps=%.2f, best_max_drawdown=%.6f, "
            "entry_freq=%.4f (%d trials, %d pruned)",
            opt_result.best_score,
            opt_result.best_net_return_bps,
            opt_result.best_max_drawdown,
            opt_result.entry_frequency,
            opt_result.n_trials_run,
            opt_result.n_trials_pruned,
        )
        return opt_result.best_code

    def _run_backtest_multi_code(
        self,
        code: str,
        strategy_name: str,
        data_dir: str | Path,
        symbols: list[str],
        start_date: str,
        end_date: str | None,
        cfg: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[Path]]:
        """코드 전략을 모든 심볼에 대해 백테스트하고 집계 결과를 반환한다."""
        summaries = []
        run_dirs: list[Path] = []
        for sym in symbols:
            logger.info("  Backtesting code strategy, symbol=%s", sym)
            s, rd = self._run_backtest_code(
                code=code,
                strategy_name=strategy_name,
                data_dir=data_dir,
                symbol=sym,
                start_date=start_date,
                end_date=end_date,
                cfg=cfg,
            )
            summaries.append(s)
            run_dirs.append(rd)
        return self._aggregate_summaries(summaries), run_dirs

    def _run_backtest_code(
        self,
        code: str,
        strategy_name: str,
        data_dir: str | Path,
        symbol: str,
        start_date: str,
        end_date: str | None,
        cfg: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], Path]:
        """단일 심볼에 대해 코드 전략을 백테스트한다."""
        from evaluation_orchestration.layer7_validation import PipelineRunner
        from scripts.backtest import build_states_for_range, backtest_config_from_cfg

        cfg = cfg or {}
        bt_cfg = cfg.get("backtest", {})
        resample = bt_cfg.get("resample", "1s")
        lookback = bt_cfg.get("trade_lookback", 100)
        tick_size = float(bt_cfg.get("tick_size", 1.0))

        states = build_states_for_range(
            data_dir=data_dir,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            resample_freq=resample,
            trade_lookback=lookback,
        )

        filter_args = self._distribution_filter_args(cfg)
        filter_result = check_code_entry_frequency(
            code=code, states=states, tick_size=tick_size, **filter_args
        )
        if not filter_result.passed:
            raise DistributionFilterError(filter_result.reason, filter_result.entry_frequency)
        logger.info("  Code distribution filter passed: entry_freq=%.4f", filter_result.entry_frequency)

        config = backtest_config_from_cfg(
            cfg,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        strategy = CodeStrategy(code=code, name=strategy_name, tick_size=config.tick_size)

        runner = PipelineRunner(
            config=config,
            data_dir=str(data_dir),
            output_dir=self._output_dir,
            strategy=strategy,
        )
        bt_result = runner.run(states)
        run_dir = Path(self._output_dir) / bt_result.run_id
        return bt_result.summary(), run_dir


def _write_strategy_info(
    run_dir: Path,
    *,
    code: str,
    feedback: dict,
    strategy_text: str | None,
    iteration: int,
) -> None:
    """strategy_info.json을 run_dir에 기록한다. 실패해도 warning만."""
    import json as _json

    info = {
        "iteration": iteration,
        "strategy_text": strategy_text,
        "code": code,
        "feedback": feedback,
    }
    try:
        (run_dir / "strategy_info.json").write_text(
            _json.dumps(info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write strategy_info.json to %s: %s", run_dir, exc)
