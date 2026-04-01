"""
strategy_loop/memory_store.py
-------------------------------
Two-level JSON memory:

  1. Per-strategy record  → memory_dir/strategies/{run_id}.json
     { "spec": {...}, "backtest_summary": {...}, "feedback": {...} }

  2. Global insights file → memory_dir/global_memory.json
     { "insights": ["insight1", "insight2", ...] }

전략 루프가 각 iteration 후 결과를 저장하고,
다음 iteration 에서 global insights 를 읽어 LLM 프롬프트에 삽입한다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GLOBAL_FILE = "global_memory.json"
_STRATEGIES_DIR = "strategies"


class MemoryStore:
    def __init__(self, memory_dir: str | Path) -> None:
        self._root = Path(memory_dir)
        self._strat_dir = self._root / _STRATEGIES_DIR
        self._global_path = self._root / _GLOBAL_FILE
        self._strat_dir.mkdir(parents=True, exist_ok=True)

    # ── per-strategy ──────────────────────────────────────────────────

    def save_strategy(
        self,
        run_id: str,
        spec: dict[str, Any],
        backtest_summary: dict[str, Any],
        feedback: dict[str, Any],
    ) -> Path:
        """Save one strategy run record. Returns path to the saved file."""
        record = {
            "run_id": run_id,
            "spec": spec,
            "backtest_summary": backtest_summary,
            "feedback": feedback,
        }
        path = self._strat_dir / f"{run_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
        logger.debug("MemoryStore: saved strategy record → %s", path)
        return path

    def load_strategy(self, run_id: str) -> dict[str, Any]:
        path = self._strat_dir / f"{run_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # ── global insights ───────────────────────────────────────────────

    def load_insights(self) -> list[str]:
        """Return the current list of cross-strategy insights."""
        if not self._global_path.exists():
            return []
        data = json.loads(self._global_path.read_text(encoding="utf-8"))
        return data.get("insights", [])

    def append_insights(self, new_insights: list[str]) -> None:
        """Append new insights to global memory (deduped)."""
        existing = self.load_insights()
        combined = existing + [s for s in new_insights if s not in existing]
        self._global_path.write_text(
            json.dumps({"insights": combined}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("MemoryStore: global insights updated (%d total)", len(combined))
