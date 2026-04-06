"""
strategy_loop/memory_store.py
-------------------------------
Two-level JSON memory:

  1. Per-strategy record  → memory_dir/strategies/{run_id}.json
     {
       "strategy_name": "...",
       "code": "...",
       "backtest_summary": {...},
       "feedback": {...}
     }

  2. Global insights file → memory_dir/global_memory.json
     {
       "insights": ["..."],
       "failure_patterns": ["..."]
     }
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
        strategy_name: str,
        code: str,
        backtest_summary: dict[str, Any],
        feedback: dict[str, Any],
    ) -> Path:
        """Save one code-strategy run record. Returns the saved path."""
        record = {
            "run_id": run_id,
            "strategy_name": strategy_name,
            "code": code,
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
        return self._load_global().get("insights", [])

    def append_insights(self, new_insights: list[str], max_count: int = 10) -> None:
        """Append new insights to global memory (deduped, capped at max_count)."""
        data = self._load_global()
        existing = data.get("insights", [])
        combined = existing + [s for s in new_insights if s not in existing]
        data["insights"] = combined[-max_count:]
        self._save_global(data)
        logger.debug("MemoryStore: global insights updated (%d total)", len(data["insights"]))

    # ── failure patterns ──────────────────────────────────────────────

    def load_failure_patterns(self) -> list[str]:
        """Return the accumulated list of failure patterns (from feedback issues)."""
        return self._load_global().get("failure_patterns", [])

    def append_failure_patterns(self, new_patterns: list[str], max_count: int = 10) -> None:
        """Append failure patterns to global memory (deduped, capped at max_count)."""
        data = self._load_global()
        existing = data.get("failure_patterns", [])
        combined = existing + [s for s in new_patterns if s not in existing]
        data["failure_patterns"] = combined[-max_count:]
        self._save_global(data)
        logger.debug("MemoryStore: failure patterns updated (%d total)", len(data["failure_patterns"]))

    # ── internal ──────────────────────────────────────────────────────

    def _load_global(self) -> dict[str, Any]:
        if not self._global_path.exists():
            return {}
        return json.loads(self._global_path.read_text(encoding="utf-8"))

    def _save_global(self, data: dict[str, Any]) -> None:
        self._global_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
