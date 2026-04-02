"""
strategy_loop/loop_runner.py
------------------------------
전략 생성 → Hard Gate → 백테스트 → 피드백 생성 → Memory 저장 → 재생성
이 반복 주기를 실행하는 LoopRunner.

사용 예시:
    runner = LoopRunner(
        client=OpenAIClient(mode="mock"),
        memory_dir="outputs/memory",
        output_dir="outputs/backtests",
    )
    result = runner.run(
        research_goal="order imbalance momentum",
        n_iterations=5,
        data_dir="/data/krx",
        symbol="005930",
        start_date="20260313",
        end_date="20260313",
        cfg=load_config(),
    )
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strategy_loop.feedback_generator import FeedbackGenerator
from strategy_loop.hard_gate import HardGateResult, validate as hard_gate_validate
from strategy_loop.memory_store import MemoryStore
from strategy_loop.openai_client import OpenAIClient
from strategy_loop.prompt_builder import build_generation_messages
from strategy_loop.simple_spec_strategy import SimpleSpecStrategy

logger = logging.getLogger(__name__)


@dataclass
class IterationRecord:
    iteration: int
    run_id: str
    spec: dict[str, Any]
    gate_result: HardGateResult
    backtest_summary: dict[str, Any] | None = None
    feedback: dict[str, Any] | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class LoopResult:
    iterations: list[IterationRecord] = field(default_factory=list)
    best_run_id: str | None = None
    verdict: str = "no_pass"   # "pass" | "no_pass"


class LoopRunner:
    def __init__(
        self,
        client: OpenAIClient,
        memory_dir: str | Path,
        output_dir: str | Path = "outputs/backtests",
    ) -> None:
        self._client = client
        self._memory = MemoryStore(memory_dir)
        self._feedback_gen = FeedbackGenerator(client)
        self._output_dir = str(output_dir)

    def run(
        self,
        research_goal: str,
        n_iterations: int,
        data_dir: str | Path,
        symbol: str,
        start_date: str,
        end_date: str | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> LoopResult:
        """Run the full iterative loop. Returns a LoopResult."""
        result = LoopResult()
        previous_feedback: dict[str, Any] | None = None

        # Session-level tracking: what has been tried in this run
        session_attempts: list[dict[str, Any]] = []
        best_so_far: dict[str, Any] | None = None      # spec with highest fill_rate
        best_fill_rate: float = -1.0

        for i in range(n_iterations):
            run_id = str(uuid.uuid4())[:8]
            logger.info("─── Iteration %d / %d  (run_id=%s) ───", i + 1, n_iterations, run_id)

            # 1. Generate spec via LLM
            insights = self._memory.load_insights()
            failure_patterns = self._memory.load_failure_patterns()
            messages = build_generation_messages(
                research_goal=research_goal,
                memory_insights=insights,
                failure_patterns=failure_patterns,
                previous_feedback=previous_feedback,
                session_attempts=session_attempts,
                best_so_far=best_so_far,
            )
            try:
                from strategy_loop.spec_schema import StrategySpec
                spec_obj = self._client.chat_parsed(messages, StrategySpec)
                spec = spec_obj.model_dump(mode="json", exclude_none=True)
            except Exception as exc:
                logger.error("LLM generation failed: %s", exc)
                rec = IterationRecord(
                    iteration=i + 1, run_id=run_id, spec={},
                    gate_result=HardGateResult(passed=False, errors=[str(exc)]),
                    skipped=True, skip_reason="llm_error",
                )
                result.iterations.append(rec)
                continue

            # 2. Hard Gate
            gate = hard_gate_validate(spec)
            rec = IterationRecord(iteration=i + 1, run_id=run_id, spec=spec, gate_result=gate)
            if not gate.passed:
                logger.warning("Hard gate failed: %s", gate.errors)
                rec.skipped = True
                rec.skip_reason = "hard_gate_fail"
                result.iterations.append(rec)
                continue

            # 3. Backtest
            try:
                bt_summary = self._run_backtest(
                    spec=spec,
                    data_dir=data_dir,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    cfg=cfg,
                )
            except Exception as exc:
                logger.error("Backtest failed: %s", exc)
                rec.skipped = True
                rec.skip_reason = f"backtest_error: {exc}"
                result.iterations.append(rec)
                continue

            rec.backtest_summary = bt_summary

            # 4. LLM Feedback
            feedback = self._feedback_gen.generate(spec, bt_summary, insights)
            rec.feedback = feedback
            logger.info("Feedback verdict: %s", feedback["verdict"])

            # 5. Save to memory
            self._memory.save_strategy(run_id, spec, bt_summary, feedback)
            if feedback.get("suggestions"):
                self._memory.append_insights(feedback["suggestions"])
            if feedback.get("issues"):
                self._memory.append_failure_patterns(feedback["issues"])

            # 6. Update session tracking
            fill_rate = bt_summary.get("fill_rate") or 0.0
            session_attempts.append({
                "iteration": i + 1,
                "spec_name": spec.get("name", ""),
                "fill_rate": fill_rate,
                "net_pnl": bt_summary.get("net_pnl") or 0.0,
                "n_fills": bt_summary.get("n_fills") or 0.0,
                "verdict": feedback["verdict"],
            })
            if fill_rate > best_fill_rate:
                best_fill_rate = fill_rate
                best_so_far = spec

            previous_feedback = feedback
            result.iterations.append(rec)

            # 7. Check stop condition
            if feedback["verdict"] == "pass":
                result.best_run_id = run_id
                result.verdict = "pass"
                logger.info("Strategy passed! Stopping loop.")
                break

        return result

    # ── internal ──────────────────────────────────────────────────────

    def _run_backtest(
        self,
        spec: dict[str, Any],
        data_dir: str | Path,
        symbol: str,
        start_date: str,
        end_date: str | None,
        cfg: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build states, run pipeline, return summary dict."""
        from data.layer0_data import MarketStateBuilder, validate_resample_freq
        from evaluation_orchestration.layer7_validation import BacktestConfig, PipelineRunner

        cfg = cfg or {}
        bt_cfg = cfg.get("backtest", {})
        resample = bt_cfg.get("resample", "1s")
        validate_resample_freq(resample)
        lookback = bt_cfg.get("trade_lookback", 100)

        # Build states
        from scripts.backtest import build_states_for_range
        states = build_states_for_range(
            data_dir=data_dir,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        # Build config
        from scripts.backtest import backtest_config_from_cfg
        config = backtest_config_from_cfg(
            cfg,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        strategy = SimpleSpecStrategy(spec)

        runner = PipelineRunner(
            config=config,
            data_dir=str(data_dir),
            output_dir=self._output_dir,
            strategy=strategy,
        )
        bt_result = runner.run(states)
        return bt_result.summary()
