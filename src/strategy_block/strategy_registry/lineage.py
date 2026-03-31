"""Simple parent-child lineage tracker for trial records."""
from __future__ import annotations

import json
from pathlib import Path


class LineageTracker:
    def __init__(self, storage_path: str | Path = "strategies/trials/lineage_edges.json") -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text("[]", encoding="utf-8")

    def _load_edges(self) -> list[dict[str, str]]:
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Invalid lineage storage format: {self.storage_path}")
        return [dict(item) for item in payload]

    def _save_edges(self, edges: list[dict[str, str]]) -> None:
        self.storage_path.write_text(
            json.dumps(edges, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def link_parent_child(self, parent_trial_id: str, child_trial_id: str, relation: str) -> None:
        if not parent_trial_id or not child_trial_id:
            raise ValueError("parent_trial_id and child_trial_id must be non-empty")
        if not relation:
            raise ValueError("relation must be non-empty")

        edge = {
            "parent_trial_id": parent_trial_id,
            "child_trial_id": child_trial_id,
            "relation": relation,
        }
        edges = self._load_edges()
        if edge not in edges:
            edges.append(edge)
            self._save_edges(edges)

    def ancestors(self, trial_id: str) -> list[str]:
        edges = self._load_edges()
        parent_by_child: dict[str, set[str]] = {}
        for edge in edges:
            parent_by_child.setdefault(edge["child_trial_id"], set()).add(edge["parent_trial_id"])

        ordered: list[str] = []
        visited: set[str] = set()
        stack: list[str] = [trial_id]

        while stack:
            current = stack.pop()
            for parent in sorted(parent_by_child.get(current, set()), reverse=True):
                if parent in visited:
                    continue
                visited.add(parent)
                ordered.append(parent)
                stack.append(parent)

        return ordered

    def descendants(self, trial_id: str) -> list[str]:
        edges = self._load_edges()
        child_by_parent: dict[str, set[str]] = {}
        for edge in edges:
            child_by_parent.setdefault(edge["parent_trial_id"], set()).add(edge["child_trial_id"])

        ordered: list[str] = []
        visited: set[str] = set()
        stack: list[str] = [trial_id]

        while stack:
            current = stack.pop()
            for child in sorted(child_by_parent.get(current, set()), reverse=True):
                if child in visited:
                    continue
                visited.add(child)
                ordered.append(child)
                stack.append(child)

        return ordered
