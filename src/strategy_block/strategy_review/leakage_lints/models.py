from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class LeakageLintIssue:
    code: str
    severity: Literal["warning", "error"]
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class LeakageLintResult:
    issues: list[LeakageLintIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(issue.severity != "error" for issue in self.issues)
