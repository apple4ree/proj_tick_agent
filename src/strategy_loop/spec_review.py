"""
strategy_loop/spec_review.py
------------------------------
Validates a StrategySpec v2.2/v2.3 and returns a SpecReview.

Checks:
  1. entry_conditions is non-empty
  2. derived_features: no duplicate names; no collision with BUILTIN_FEATURES; inputs in BUILTIN_FEATURES
  3. SpecCondition source validation:
     - source_type == "feature"         → source must be in BUILTIN_FEATURES
     - source_type == "derived_feature" → source must be in declared derived_features.name
  4. exit_time_ticks > 0 (warns if < 5)
  5. exit_signal_conditions is non-empty (warns if absent)
  6. All tunable_param names are UPPER_CASE; no duplicate names
  7. archetype is in [1, 4] or None
  8. features_used entries must be in BUILTIN_FEATURES
  9. threshold_param linkage (v2.3+):
     - v2.3 spec: condition without threshold_param → error
     - v2.1/v2.2 spec: condition without threshold_param → warning
     - threshold_param value must match a declared tunable_params.name → error if not

A SpecReview is valid if there are no errors (warnings do not block).
normalized_spec is set to spec on valid, None on invalid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from strategy_block.strategy_compiler.v2.features import BUILTIN_FEATURES
from strategy_loop.spec_schema import StrategySpec

VALID_OPS: frozenset[str] = frozenset({">", ">=", "<", "<=", "==", "!="})
VALID_ARCHETYPES: frozenset[int] = frozenset({1, 2, 3, 4})


@dataclass
class SpecReview:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknown_features: list[str] = field(default_factory=list)
    # normalized_spec is the (possibly lightly fixed) spec ready for precode_eval
    normalized_spec: StrategySpec | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "1",
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "unknown_features": self.unknown_features,
            "normalized_spec": (
                self.normalized_spec.to_dict() if self.normalized_spec is not None else None
            ),
        }


def review_spec(spec: StrategySpec) -> SpecReview:
    """Validate a StrategySpec and return a SpecReview."""
    errors: list[str] = []
    warnings: list[str] = []
    unknown: list[str] = []

    def _record_unknown(fname: str) -> None:
        if fname not in BUILTIN_FEATURES and fname not in unknown:
            unknown.append(fname)

    # ── derived_features validation ───────────────────────────────────
    declared_derived: set[str] = set()
    seen_derived_names: list[str] = []

    for i, df in enumerate(spec.derived_features):
        # duplicate name check
        if df.name in seen_derived_names:
            errors.append(
                f"derived_features[{i}].name={df.name!r} is a duplicate"
            )
        else:
            seen_derived_names.append(df.name)
            declared_derived.add(df.name)

        # collision with BUILTIN_FEATURES
        if df.name in BUILTIN_FEATURES:
            errors.append(
                f"derived_features[{i}].name={df.name!r} collides with a BUILTIN_FEATURE — "
                "choose a different name"
            )

        # inputs must be in BUILTIN_FEATURES
        for inp in df.inputs:
            if inp not in BUILTIN_FEATURES:
                errors.append(
                    f"derived_features[{i}] ({df.name!r}) input {inp!r} "
                    "is not in BUILTIN_FEATURES"
                )

    # ── condition source validation helper ────────────────────────────
    def _check_condition_source(c, context: str) -> None:
        if c.op not in VALID_OPS:
            errors.append(
                f"{context}.op={c.op!r} is not a valid operator "
                f"(valid: {sorted(VALID_OPS)})"
            )
        if c.source_type == "feature":
            _record_unknown(c.source)
        elif c.source_type == "derived_feature":
            if c.source not in declared_derived:
                errors.append(
                    f"{context} references derived_feature {c.source!r} "
                    "which is not declared in derived_features"
                )
        else:
            errors.append(
                f"{context}.source_type={c.source_type!r} is invalid "
                "(must be 'feature' or 'derived_feature')"
            )

    # ── 1. entry conditions ───────────────────────────────────────────
    if not spec.entry_conditions:
        errors.append("entry_conditions is empty — at least one entry condition is required")
    else:
        for i, c in enumerate(spec.entry_conditions):
            _check_condition_source(c, f"entry_conditions[{i}]")

    # ── 2. exit time ──────────────────────────────────────────────────
    if spec.exit_time_ticks <= 0:
        errors.append(
            f"exit_time_ticks={spec.exit_time_ticks} must be positive"
        )
    elif spec.exit_time_ticks < 5:
        warnings.append(
            f"exit_time_ticks={spec.exit_time_ticks} is very short (<5 ticks) — "
            "unlikely to recover round-trip costs (~3 bps)"
        )

    # ── 3. exit signal conditions ─────────────────────────────────────
    if not spec.exit_signal_conditions:
        warnings.append(
            "exit_signal_conditions is empty — no signal-reversal exit defined; "
            "time-only exits tend to overfit"
        )
    else:
        for i, c in enumerate(spec.exit_signal_conditions):
            _check_condition_source(c, f"exit_signal_conditions[{i}]")

    # ── 4. features_used ──────────────────────────────────────────────
    for fname in spec.features_used:
        _record_unknown(fname)

    # Unknown feature errors
    if unknown:
        errors.append(f"Unknown features (not in BUILTIN_FEATURES): {unknown}")

    # ── 5. tunable params ─────────────────────────────────────────────
    seen_param_names: list[str] = []
    declared_param_names: set[str] = set()
    for p in spec.tunable_params:
        if p.name in seen_param_names:
            errors.append(
                f"tunable_params name {p.name!r} is a duplicate"
            )
        else:
            seen_param_names.append(p.name)
            declared_param_names.add(p.name)

        if not p.name.isupper():
            warnings.append(
                f"tunable_param {p.name!r} is not UPPER_CASE — "
                "Optuna pattern-matching will not recognize it"
            )
        if p.range[0] >= p.range[1]:
            warnings.append(
                f"tunable_param {p.name!r} has invalid range {list(p.range)} (lo >= hi)"
            )

    # ── 5b. threshold_param linkage ───────────────────────────────────
    _is_v23 = spec.version.startswith("2.3")
    all_conditions = list(spec.entry_conditions) + list(spec.exit_signal_conditions)
    for i, c in enumerate(all_conditions):
        ctx = (
            f"entry_conditions[{i}]"
            if i < len(spec.entry_conditions)
            else f"exit_signal_conditions[{i - len(spec.entry_conditions)}]"
        )
        if c.threshold_param is None:
            if _is_v23:
                errors.append(
                    f"{ctx} has no threshold_param — "
                    "v2.3 requires every condition to link its threshold to a tunable_param"
                )
            else:
                warnings.append(
                    f"{ctx} has no threshold_param — "
                    "consider adding threshold_param for Optuna linkage (required in v2.3)"
                )
        else:
            if c.threshold_param not in declared_param_names:
                errors.append(
                    f"{ctx}.threshold_param={c.threshold_param!r} "
                    "does not match any declared tunable_params.name"
                )

    # ── 6. archetype ──────────────────────────────────────────────────
    if spec.archetype is not None and spec.archetype not in VALID_ARCHETYPES:
        errors.append(
            f"archetype={spec.archetype} is not in {sorted(VALID_ARCHETYPES)}"
        )

    valid = len(errors) == 0
    return SpecReview(
        valid=valid,
        errors=errors,
        warnings=warnings,
        unknown_features=unknown,
        normalized_spec=spec if valid else None,
    )
