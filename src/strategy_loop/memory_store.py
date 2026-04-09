"""
strategy_loop/memory_store.py
-------------------------------
Three-level JSON memory:

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

  3. Plan-level record    → memory_dir/plans/{plan_id}.json  (spec-centric pipeline)
     {
       "plan_id": "...",
       "archetype": 1,
       "archetype_name": "...",
       "strategy_text": "...",
       "spec": {...},
       "spec_review": {...},
       "precode_eval": {...},
       "outcome": "pass" | "fail" | "no_code_pass",
       "primary_issue": "...",
       "best_net_pnl": 0.0
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
_PLANS_DIR = "plans"


class MemoryStore:
    def __init__(self, memory_dir: str | Path) -> None:
        self._root = Path(memory_dir)
        self._strat_dir = self._root / _STRATEGIES_DIR
        self._plans_dir = self._root / _PLANS_DIR
        self._global_path = self._root / _GLOBAL_FILE
        self._strat_dir.mkdir(parents=True, exist_ok=True)
        self._plans_dir.mkdir(parents=True, exist_ok=True)

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

    # ── plan-level (spec-centric pipeline) ───────────────────────────

    def save_plan(
        self,
        plan_id: str,
        strategy_text: str,
        spec: dict[str, Any],
        spec_review: dict[str, Any],
        precode_eval: dict[str, Any],
        outcome: str = "no_code_pass",
        primary_issue: str = "",
        best_net_pnl: float = 0.0,
    ) -> Path:
        """Save one plan-level record. Returns the saved path."""
        record: dict[str, Any] = {
            "plan_id": plan_id,
            "archetype": spec.get("archetype"),
            "archetype_name": spec.get("archetype_name", ""),
            "strategy_text": strategy_text,
            "spec": spec,
            "spec_review": spec_review,
            "precode_eval": precode_eval,
            "outcome": outcome,
            "primary_issue": primary_issue,
            "best_net_pnl": best_net_pnl,
        }
        path = self._plans_dir / f"{plan_id}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=float),
            encoding="utf-8",
        )
        logger.debug("MemoryStore: saved plan record → %s", path)
        return path

    def update_plan_outcome(
        self,
        plan_id: str,
        outcome: str,
        primary_issue: str = "",
        best_net_pnl: float = 0.0,
    ) -> None:
        """Update outcome fields on an existing plan record."""
        path = self._plans_dir / f"{plan_id}.json"
        if not path.exists():
            logger.warning("MemoryStore: plan %s not found for update", plan_id)
            return
        record = json.loads(path.read_text(encoding="utf-8"))
        record["outcome"] = outcome
        if primary_issue:
            record["primary_issue"] = primary_issue
        record["best_net_pnl"] = best_net_pnl
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=float),
            encoding="utf-8",
        )

    def load_planner_memory(self, max_plans: int = 5) -> list[dict[str, Any]]:
        """Return the most recent plan records for planner context injection.

        Each entry contains: plan_id, archetype_name, outcome, primary_issue.
        """
        paths = sorted(
            self._plans_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
        )[-max_plans:]
        records: list[dict[str, Any]] = []
        for p in paths:
            try:
                full = json.loads(p.read_text(encoding="utf-8"))
                records.append({
                    "plan_id": full.get("plan_id", p.stem),
                    "archetype_name": full.get("archetype_name", ""),
                    "outcome": full.get("outcome", ""),
                    "primary_issue": full.get("primary_issue", ""),
                })
            except Exception as exc:
                logger.warning("MemoryStore: could not load plan %s: %s", p, exc)
        return records

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
