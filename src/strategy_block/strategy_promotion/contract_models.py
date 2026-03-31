"""Promotion contract datamodels."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DeploymentContract:
    strategy_name: str
    strategy_version: str
    trial_id: str | None
    family_id: str | None
    allowed_symbols: list[str] = field(default_factory=list)
    expected_holding_horizon_s: tuple[float, float] | None = None
    max_turnover: float | None = None
    latency_budget_ms: float | None = None
    forbidden_time_ranges: list[str] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    regime_dependencies: list[str] = field(default_factory=list)
    disable_conditions: list[str] = field(default_factory=list)
    monitoring_metrics: list[str] = field(default_factory=list)
    known_failure_modes: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeploymentContract":
        return cls(**payload)
