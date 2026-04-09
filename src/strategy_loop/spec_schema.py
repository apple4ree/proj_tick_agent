"""
strategy_loop/spec_schema.py
------------------------------
StrategySpec v2.3 — structured JSON schema for the spec-centric pipeline.

Changelog v2.1 → v2.2:
  - SpecCondition: new canonical shape {source_type, source, op, threshold}.
    Old shape {feature, op, threshold} still accepted in from_dict() (backward compat).
  - StrategySpec: added derived_features list (DerivedFeature).
  - all_referenced_features() now includes derived_features[*].inputs, not derived names.

Changelog v2.2 → v2.3:
  - SpecCondition: added optional threshold_param (UPPER_CASE tunable param name).
    When set, implementer renders the constant name instead of the numeric literal.
  - spec_review enforces threshold_param for v2.3 specs (error if missing);
    v2.1/v2.2 specs get a warning only.
  - effective_condition_features() added to StrategySpec (narrower than
    all_referenced_features — excludes features_used and unused derived inputs).

The planner LLM produces a StrategySpec alongside strategy_text.md.
spec_review.py validates it; precode_eval.py scores it;
implementer_prompt_builder.py injects it into the code-generation prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DerivedFeature:
    """A named expression derived from BUILTIN_FEATURES.

    Example:
      {"name": "spread_ticks",
       "formula": "(ask_1_price - bid_1_price) / tick_size",
       "inputs": ["ask_1_price", "bid_1_price", "tick_size"]}
    """
    name: str
    formula: str
    inputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "formula": self.formula, "inputs": list(self.inputs)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DerivedFeature":
        return cls(
            name=str(d["name"]),
            formula=str(d.get("formula", "")),
            inputs=list(d.get("inputs", [])),
        )


@dataclass
class SpecCondition:
    """Single comparison condition — references either a BUILTIN_FEATURE or a derived feature.

    Canonical v2.3 shape:
      {"source_type": "feature"|"derived_feature", "source": "<name>",
       "op": ">", "threshold": 0.3, "threshold_param": "ORDER_IMBALANCE_THRESHOLD"}

    threshold_param (v2.3): UPPER_CASE name of the tunable_param that controls
    this threshold.  When set, the implementer renders the constant name instead
    of the numeric literal (e.g. `> ORDER_IMBALANCE_THRESHOLD` not `> 0.3`).

    Backward-compat v2.1 shape (from_dict still accepts):
      {"feature": "<name>", "op": ">", "threshold": 0.3}
      → normalized to source_type="feature", source=feature, threshold_param=None.
    """
    source_type: str = "feature"        # "feature" | "derived_feature"
    source: str = ""
    op: str = ">"
    threshold: float = 0.0
    threshold_param: str | None = None  # UPPER_CASE tunable param name (v2.3+)

    # ── backward-compat read accessor ────────────────────────────────
    @property
    def feature(self) -> str:
        """Backward-compat alias: returns source (meaningful for source_type='feature')."""
        return self.source

    # ── serialisation ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source_type": self.source_type,
            "source": self.source,
            "op": self.op,
            "threshold": self.threshold,
        }
        if self.threshold_param is not None:
            d["threshold_param"] = self.threshold_param
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SpecCondition":
        tp = d.get("threshold_param")
        threshold_param = str(tp) if tp is not None else None
        if "source_type" in d:
            # v2.2/v2.3 canonical format
            return cls(
                source_type=str(d["source_type"]),
                source=str(d["source"]),
                op=str(d["op"]),
                threshold=float(d["threshold"]),
                threshold_param=threshold_param,
            )
        # v2.1 backward-compat: {"feature": "...", "op": "...", "threshold": ...}
        return cls(
            source_type="feature",
            source=str(d["feature"]),
            op=str(d["op"]),
            threshold=float(d["threshold"]),
            threshold_param=threshold_param,
        )


@dataclass
class TunableParam:
    """An UPPER_CASE numeric constant that Optuna will optimize."""
    name: str
    default: float
    type: str                     # "float" | "int"
    range: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default": self.default,
            "type": self.type,
            "range": list(self.range),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TunableParam":
        r = d.get("range", [0.0, 1.0])
        return cls(
            name=str(d["name"]),
            default=float(d.get("default", 0.0)),
            type=str(d.get("type", "float")),
            range=(float(r[0]), float(r[1])),
        )


@dataclass
class StrategySpec:
    """Structured strategy spec produced by the planner LLM."""
    version: str = "2.3"
    archetype: int | None = None           # 1-4, None = not specified
    archetype_name: str = ""
    entry_conditions: list[SpecCondition] = field(default_factory=list)
    exit_time_ticks: int = 20              # mandatory time-based exit
    exit_signal_conditions: list[SpecCondition] = field(default_factory=list)
    tunable_params: list[TunableParam] = field(default_factory=list)
    derived_features: list[DerivedFeature] = field(default_factory=list)
    features_used: list[str] = field(default_factory=list)
    rationale: str = ""

    # ── serialisation ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "archetype": self.archetype,
            "archetype_name": self.archetype_name,
            "entry_conditions": [c.to_dict() for c in self.entry_conditions],
            "exit_time_ticks": self.exit_time_ticks,
            "exit_signal_conditions": [c.to_dict() for c in self.exit_signal_conditions],
            "tunable_params": [p.to_dict() for p in self.tunable_params],
            "derived_features": [df.to_dict() for df in self.derived_features],
            "features_used": list(self.features_used),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategySpec":
        return cls(
            version=str(d.get("version", "2.3")),
            archetype=int(d["archetype"]) if d.get("archetype") is not None else None,
            archetype_name=str(d.get("archetype_name", "")),
            entry_conditions=[
                SpecCondition.from_dict(c) for c in d.get("entry_conditions", [])
            ],
            exit_time_ticks=int(d.get("exit_time_ticks", 20)),
            exit_signal_conditions=[
                SpecCondition.from_dict(c) for c in d.get("exit_signal_conditions", [])
            ],
            tunable_params=[
                TunableParam.from_dict(p) for p in d.get("tunable_params", [])
            ],
            derived_features=[
                DerivedFeature.from_dict(df) for df in d.get("derived_features", [])
            ],
            features_used=list(d.get("features_used", [])),
            rationale=str(d.get("rationale", "")),
        )

    # ── feature introspection ─────────────────────────────────────────

    def all_referenced_features(self) -> set[str]:
        """Return all BUILTIN_FEATURES referenced anywhere in this spec.

        Includes:
          - features_used (raw feature names)
          - entry_conditions where source_type == "feature"
          - exit_signal_conditions where source_type == "feature"
          - derived_features[*].inputs (raw BUILTIN_FEATURES used inside formulas)

        Derived feature names themselves are NOT included — they are not BUILTIN_FEATURES.
        This makes the set suitable for feature_validity / archetype_alignment scoring.
        """
        features: set[str] = set(self.features_used)
        for c in self.entry_conditions:
            if c.source_type == "feature":
                features.add(c.source)
        for c in self.exit_signal_conditions:
            if c.source_type == "feature":
                features.add(c.source)
        for df in self.derived_features:
            features.update(df.inputs)
        return features

    def derived_feature_names(self) -> set[str]:
        """Return the set of declared derived feature names."""
        return {df.name for df in self.derived_features}

    def effective_condition_features(self) -> set[str]:
        """Return only BUILTIN_FEATURES that actually influence entry/exit decisions.

        This is a narrower set than all_referenced_features():
          - feature-type condition sources (directly referenced BUILTIN_FEATURES)
          - inputs of derived features that are USED in at least one condition
            (i.e., inputs of unused derived features are excluded)

        Excludes:
          - features_used (declared hints, not necessarily in conditions)
          - inputs of derived features that are declared but never appear
            in any entry or exit condition

        Use this for scoring (feature_validity, economic_plausibility,
        archetype_alignment) to prevent inflation from unused declarations.
        """
        features: set[str] = set()
        used_derived: set[str] = set()

        for c in self.entry_conditions:
            if c.source_type == "feature":
                features.add(c.source)
            elif c.source_type == "derived_feature":
                used_derived.add(c.source)

        for c in self.exit_signal_conditions:
            if c.source_type == "feature":
                features.add(c.source)
            elif c.source_type == "derived_feature":
                used_derived.add(c.source)

        # Only include inputs of derived features actually used in conditions
        for df in self.derived_features:
            if df.name in used_derived:
                features.update(df.inputs)

        return features
