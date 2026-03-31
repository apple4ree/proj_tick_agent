"""Per-window score projection helpers."""
from __future__ import annotations

from typing import Any

from .harness import WalkForwardRunResult


class WalkForwardScorer:
    """Convert a run result into report-friendly score payload."""

    def score_window(self, result: WalkForwardRunResult) -> dict[str, Any]:
        score = result.selection_score
        return {
            "window": {
                "train_start": result.window.train_start,
                "train_end": result.window.train_end,
                "select_start": result.window.select_start,
                "select_end": result.window.select_end,
                "holdout_start": result.window.holdout_start,
                "holdout_end": result.window.holdout_end,
                "forward_start": result.window.forward_start,
                "forward_end": result.window.forward_end,
            },
            "run_dir": result.run_dir,
            "trial_id": result.trial_id,
            "total_score": float(score.total_score),
            "components": dict(score.components),
            "penalties": dict(score.penalties),
            "metadata": dict(score.metadata),
        }
