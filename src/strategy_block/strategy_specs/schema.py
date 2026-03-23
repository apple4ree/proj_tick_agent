"""
strategy_specs/schema.py
------------------------
Strategy specification schema: the structured output format that LLM agents
produce and the strategy compiler consumes.

A StrategySpec is a complete, serializable description of a trading strategy
that can be compiled into an executable Strategy object for backtesting.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class SignalRule:
    """A single signal generation rule.

    Attributes
    ----------
    feature : str
        Feature name from FeaturePipeline (e.g. 'order_imbalance', 'spread_bps',
        'ewm_alpha', 'trade_flow_imbalance').
    operator : str
        Comparison operator: '>', '<', '>=', '<=', '==', 'cross_above', 'cross_below'.
    threshold : float
        Threshold value for the comparison.
    score_contribution : float
        How much this rule contributes to the signal score when triggered.
        Positive = bullish, negative = bearish.
    description : str
        Human-readable description of this rule's intent.
    """
    feature: str
    operator: str
    threshold: float
    score_contribution: float
    description: str = ""


@dataclass
class FilterRule:
    """Pre-trade filter that must pass before a signal is acted upon.

    Attributes
    ----------
    feature : str
        Feature to check.
    operator : str
        Comparison operator.
    threshold : float
        Filter threshold.
    action : str
        What to do when filter triggers: 'block' (skip signal) or 'reduce' (halve score).
    description : str
        Human-readable description.
    """
    feature: str
    operator: str
    threshold: float
    action: str = "block"
    description: str = ""


@dataclass
class PositionRule:
    """Position sizing and holding rules.

    Attributes
    ----------
    max_position : int
        Maximum position size in shares.
    sizing_mode : str
        How to size: 'fixed', 'signal_proportional', 'kelly'.
    fixed_size : int
        Order size when sizing_mode='fixed'.
    holding_period_ticks : int
        Minimum holding period in ticks before exit is allowed.
        0 means no minimum.
    inventory_cap : int
        Hard cap on absolute inventory. Signals that would exceed this are blocked.
    """
    max_position: int = 1000
    sizing_mode: str = "signal_proportional"
    fixed_size: int = 100
    holding_period_ticks: int = 0
    inventory_cap: int = 1000


@dataclass
class ExitRule:
    """Exit / stop-loss / take-profit rule.

    Attributes
    ----------
    exit_type : str
        'stop_loss', 'take_profit', 'trailing_stop', 'time_exit', 'signal_reversal'.
    threshold_bps : float
        Threshold in basis points (for stop_loss, take_profit, trailing_stop).
    timeout_ticks : int
        Maximum ticks before forced exit (for time_exit). 0 = disabled.
    description : str
        Human-readable description.
    """
    exit_type: str
    threshold_bps: float = 0.0
    timeout_ticks: int = 0
    description: str = ""


@dataclass
class StrategySpec:
    """Complete strategy specification.

    This is the central artifact of the Multi-Agent pipeline:
    - Researcher Agent proposes ideas
    - Factor Designer Agent fills signal_rules and filters
    - Risk/Execution Agent fills position_rule, exit_rules, latency notes
    - Reviewer Agent validates and may modify any section

    The StrategyCompiler converts this spec into an executable Strategy object
    that plugs into the existing PipelineRunner backtest engine.

    Attributes
    ----------
    name : str
        Strategy name (e.g. 'imbalance_momentum_v1').
    version : str
        Spec version for tracking.
    description : str
        High-level strategy description.
    signal_rules : list[SignalRule]
        Rules that generate the directional signal.
    filters : list[FilterRule]
        Pre-trade filters.
    position_rule : PositionRule
        Position sizing and holding configuration.
    exit_rules : list[ExitRule]
        Exit conditions.
    metadata : dict
        Arbitrary metadata (agent versions, generation timestamp, etc.).
    """
    name: str
    version: str = "1.0"
    description: str = ""
    signal_rules: list[SignalRule] = field(default_factory=list)
    filters: list[FilterRule] = field(default_factory=list)
    position_rule: PositionRule = field(default_factory=PositionRule)
    exit_rules: list[ExitRule] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> StrategySpec:
        signal_rules = [SignalRule(**r) for r in d.get("signal_rules", [])]
        filters = [FilterRule(**f) for f in d.get("filters", [])]
        position_rule = PositionRule(**d["position_rule"]) if "position_rule" in d else PositionRule()
        exit_rules = [ExitRule(**e) for e in d.get("exit_rules", [])]
        return cls(
            name=d["name"],
            version=d.get("version", "1.0"),
            description=d.get("description", ""),
            signal_rules=signal_rules,
            filters=filters,
            position_rule=position_rule,
            exit_rules=exit_rules,
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> StrategySpec:
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> StrategySpec:
        path = Path(path)
        return cls.from_json(path.read_text(encoding="utf-8"))

    def validate(self) -> list[str]:
        """Return a list of validation errors. Empty list means valid."""
        errors: list[str] = []
        if not self.name:
            errors.append("Strategy name is required")
        if not self.signal_rules:
            errors.append("At least one signal rule is required")

        valid_operators = {">", "<", ">=", "<=", "==", "cross_above", "cross_below"}
        for i, rule in enumerate(self.signal_rules):
            if rule.operator not in valid_operators:
                errors.append(f"signal_rules[{i}]: invalid operator '{rule.operator}'")
            if not rule.feature:
                errors.append(f"signal_rules[{i}]: feature name is required")

        valid_filter_actions = {"block", "reduce"}
        for i, f in enumerate(self.filters):
            if f.action not in valid_filter_actions:
                errors.append(f"filters[{i}]: invalid action '{f.action}'")

        valid_sizing = {"fixed", "signal_proportional", "kelly"}
        if self.position_rule.sizing_mode not in valid_sizing:
            errors.append(f"position_rule.sizing_mode must be one of {valid_sizing}")

        valid_exit_types = {"stop_loss", "take_profit", "trailing_stop", "time_exit", "signal_reversal"}
        for i, e in enumerate(self.exit_rules):
            if e.exit_type not in valid_exit_types:
                errors.append(f"exit_rules[{i}]: invalid exit_type '{e.exit_type}'")

        return errors
