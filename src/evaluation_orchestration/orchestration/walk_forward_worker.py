"""Worker for queued walk-forward evaluation jobs."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from evaluation_orchestration.layer6_evaluator.selection_metrics import SelectionMetrics
from evaluation_orchestration.layer7_validation.walk_forward import (
    WalkForwardHarness,
    WalkForwardReportBuilder,
    WalkForwardSelector,
)
from evaluation_orchestration.layer7_validation.walk_forward.selection_snapshot import (
    SelectionContextResolver,
)
from evaluation_orchestration.orchestration.file_queue import FileQueue
from evaluation_orchestration.orchestration.models import JobType

logger = logging.getLogger(__name__)


class WalkForwardWorker:
    """Minimal worker that consumes ``walk_forward_evaluation`` queue jobs."""

    def __init__(
        self,
        queue: FileQueue,
        output_dir: str | Path = "outputs/walk_forward",
    ) -> None:
        self.queue = queue
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_once(self) -> bool:
        job = self.queue.dequeue(job_type=JobType.WALK_FORWARD_EVALUATION)
        if job is None:
            return False

        try:
            payload = dict(job.payload or {})
            result = self._run_job(payload)
            out_path = self.output_dir / f"{job.job_id}_walk_forward_result.json"
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            self.queue.mark_succeeded(job.job_id, result_path=str(out_path))
            logger.info("Walk-forward job %s succeeded", job.job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Walk-forward job %s failed: %s", job.job_id, exc)
            self.queue.mark_failed(job.job_id, error_message=str(exc))

        return True

    def _run_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec_path = str(payload.get("spec_path") or "")
        if not spec_path:
            raise ValueError("payload missing spec_path")

        trial_id = payload.get("trial_id")
        universe = bool(payload.get("universe", False))
        symbol = payload.get("symbol")
        if not universe and not symbol:
            raise ValueError("payload must include symbol when universe=False")

        wf_cfg = {
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "profile": payload.get("profile"),
            "config_path": payload.get("config_path"),
            "data_dir": payload.get("data_dir"),
            "output_root": str(payload.get("output_dir") or self.output_dir),
            "selection": payload.get("selection") or {},
        }

        family_context = SelectionContextResolver().build_family_context(
            spec_path=spec_path,
            trial_id=str(trial_id) if trial_id is not None else None,
            profile=payload.get("profile"),
            config_path=payload.get("config_path"),
        )

        harness = WalkForwardHarness(selection_metrics=SelectionMetrics(wf_cfg.get("selection")))
        results = harness.run_spec(
            spec_path=spec_path,
            symbol=str(symbol) if symbol is not None else None,
            universe=universe,
            cfg=wf_cfg,
            trial_id=str(trial_id) if trial_id is not None else None,
            selection_context=family_context,
        )

        selector = WalkForwardSelector()
        decision = selector.select(
            results,
            cfg=wf_cfg,
            family_context=family_context,
        )

        report_builder = WalkForwardReportBuilder()
        report = report_builder.build(
            decision,
            results,
            family_context=family_context,
        )
        report["spec_path"] = spec_path
        report["trial_id"] = trial_id
        report["execution_mode"] = "universe" if universe else "single"
        report["symbol"] = str(symbol) if symbol is not None else None

        selection_snapshot = report_builder.build_selection_snapshot(
            report,
            family_context=family_context,
        )

        report_out_dir = Path(str(wf_cfg["output_root"])) / Path(spec_path).stem
        saved_paths = report_builder.save(
            str(report_out_dir),
            report,
            selection_cfg=wf_cfg.get("selection"),
            selection_snapshot=selection_snapshot,
        )

        return {
            "passed": bool(decision.passed),
            "aggregate_score": float(decision.aggregate_score),
            "reasons": list(decision.reasons),
            "report_path": saved_paths["report_path"],
            "selection_snapshot_path": saved_paths.get("selection_snapshot_path"),
            "n_windows": len(results),
        }
