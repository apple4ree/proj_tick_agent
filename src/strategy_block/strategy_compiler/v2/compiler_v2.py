"""Compiler v2 — converts StrategySpecV2 into an executable Strategy.

The compiled strategy implements the Strategy ABC so it can plug directly
into the existing PipelineRunner backtest engine.

Evaluation order per tick:
1. Extract features from MarketState
2. Record feature history (for lag/rolling/persist)
3. Check do_not_trade_when (execution policy)
4. Check preconditions (all must pass)
5. Select active regime (if regimes defined)
6. Check exit policies (if in a position)
7. Check entry policies (if not blocked by cooldown/constraints)
8. Apply risk policy (inventory cap, position sizing)
9. Emit Signal with execution hints in tags
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from strategy_block.strategy.base import Strategy
from strategy_block.strategy_specs.v2.schema_v2 import (
    StrategySpecV2,
    EntryPolicyV2,
    ExitPolicyV2,
    ExitRuleV2,
    RegimeV2,
)
from .runtime_v2 import RuntimeStateV2, evaluate_bool, evaluate_float

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState
    from execution_planning.layer1_signal import Signal

logger = logging.getLogger(__name__)


class CompiledStrategyV2(Strategy):
    """Executable strategy compiled from a StrategySpecV2."""

    def __init__(self, spec: StrategySpecV2) -> None:
        self._spec = spec
        self._states: dict[str, RuntimeStateV2] = {}
        # Build lookup maps for regime-based policy routing
        self._entry_by_name: dict[str, EntryPolicyV2] = {
            ep.name: ep for ep in spec.entry_policies
        }
        self._exit_by_name: dict[str, ExitPolicyV2] = {
            xp.name: xp for xp in spec.exit_policies
        }

    @property
    def name(self) -> str:
        return f"CompiledV2:{self._spec.name}"

    def reset(self) -> None:
        self._states.clear()

    def _get_state(self, symbol: str) -> RuntimeStateV2:
        if symbol not in self._states:
            self._states[symbol] = RuntimeStateV2()
        return self._states[symbol]

    def _build_exec_tags(self) -> dict[str, object]:
        """Build execution hint tags from execution_policy."""
        xp = self._spec.execution_policy
        if xp is None:
            return {}
        tags: dict[str, object] = {
            "placement_mode": xp.placement_mode,
        }
        if xp.cancel_after_ticks > 0:
            tags["cancel_after_ticks"] = xp.cancel_after_ticks
        if xp.max_reprices > 0:
            tags["max_reprices"] = xp.max_reprices
        return tags

    def generate_signal(self, state: "MarketState") -> "Signal | None":
        from execution_planning.layer1_signal import Signal
        # Reuse v1 feature extraction (same contract)
        from strategy_block.strategy_compiler.compiler import CompiledStrategy

        if state.lob.mid_price is None:
            return None

        symbol = state.symbol
        mid = state.lob.mid_price
        rt = self._get_state(symbol)
        rt.tick_count += 1
        tick = rt.tick_count

        features = CompiledStrategy._extract_features(
            CompiledStrategy.__new__(CompiledStrategy), state
        )

        prev_features = rt.prev_features

        # Record feature history for lag/rolling/persist
        rt.record_features(features)

        # Build base tags including execution hints
        exec_tags = self._build_exec_tags()

        # 0. Check do_not_trade_when (execution policy)
        xp = self._spec.execution_policy
        if xp and xp.do_not_trade_when is not None:
            if evaluate_bool(xp.do_not_trade_when, features, prev_features, rt):
                rt.prev_features = features
                return None

        # 1. Check preconditions
        for pc in self._spec.preconditions:
            if not evaluate_bool(pc.condition, features, prev_features, rt):
                rt.prev_features = features
                return None

        # 2. Select active regime (if regimes defined)
        active_entries, active_exits, active_risk = self._select_regime(
            rt, features, prev_features, exec_tags
        )

        # 3. Check exit policies (if in a position)
        if rt.position_side:
            exit_signal = self._evaluate_exits(
                rt, features, prev_features, mid, tick, active_exits
            )
            if exit_signal is not None:
                score = -1.0 if rt.position_side == "long" else 1.0
                exit_type = exit_signal
                # Reset position state
                rt.position_side = ""
                rt.position_size = 0.0
                rt.entry_tick = -1
                rt.entry_price = 0.0
                rt.trailing_high = 0.0
                rt.trailing_low = float("inf")
                rt.prev_features = features
                tags = {"strategy": self.name, "exit_type": exit_type,
                        "spec_format": "v2"}
                tags.update(exec_tags)
                return Signal(
                    timestamp=state.timestamp,
                    symbol=symbol,
                    score=score,
                    expected_return=score * 5.0,
                    confidence=0.8,
                    horizon_steps=1,
                    tags=tags,
                    is_valid=True,
                )

            # Update trailing prices
            if rt.position_side == "long":
                rt.trailing_high = max(rt.trailing_high, mid)
            else:
                rt.trailing_low = min(rt.trailing_low, mid)

        # 4. Check entry policies
        entry_result = self._evaluate_entries(
            rt, features, prev_features, tick, active_entries
        )
        if entry_result is None:
            rt.prev_features = features
            return None

        entry_name, side, strength = entry_result

        # 5. Apply risk policy — inventory cap
        rp = active_risk
        if rt.position_side:
            if abs(rt.position_size) >= rp.inventory_cap:
                rt.prev_features = features
                return None

        # 6. Compute score and size
        score = strength if side == "long" else -strength
        score = max(-1.0, min(1.0, score))

        if score == 0.0:
            rt.prev_features = features
            return None

        # Update position state
        if not rt.position_side or (side != rt.position_side):
            rt.entry_price = mid
            rt.entry_tick = tick
            rt.trailing_high = mid
            rt.trailing_low = mid
        rt.position_side = side
        rt.position_size = abs(score) * rp.position_sizing.max_size

        # Apply cooldown
        for ep in self._spec.entry_policies:
            if ep.name == entry_name and ep.constraints.cooldown_ticks > 0:
                rt.cooldown_until = tick + ep.constraints.cooldown_ticks

        rt.prev_features = features

        tags = {"strategy": self.name, "entry_policy": entry_name,
                "spec_format": "v2"}
        tags.update(exec_tags)

        return Signal(
            timestamp=state.timestamp,
            symbol=symbol,
            score=score,
            expected_return=score * 10.0,
            confidence=min(1.0, abs(score)),
            horizon_steps=1,
            tags=tags,
            is_valid=True,
        )

    def _select_regime(
        self,
        rt: RuntimeStateV2,
        features: dict[str, float],
        prev_features: dict[str, float],
        exec_tags: dict[str, object],
    ) -> tuple[list[EntryPolicyV2], list[ExitPolicyV2], object]:
        """Select the active regime and return (entries, exits, risk).

        If no regimes are defined, returns all policies (Phase 1 behavior).
        If regimes are defined but none match, returns empty lists (no trade).
        """
        if not self._spec.regimes:
            # No regimes: use all policies (Phase 1 fallback)
            return (
                self._spec.entry_policies,
                self._spec.exit_policies,
                self._spec.risk_policy,
            )

        # Sort regimes by priority (lower = higher priority)
        sorted_regimes = sorted(self._spec.regimes, key=lambda r: r.priority)

        for regime in sorted_regimes:
            if evaluate_bool(regime.when, features, prev_features, rt):
                # Matched — resolve policy refs
                entries = [
                    self._entry_by_name[ref]
                    for ref in regime.entry_policy_refs
                    if ref in self._entry_by_name
                ]
                exits = []
                if regime.exit_policy_ref and regime.exit_policy_ref in self._exit_by_name:
                    exits = [self._exit_by_name[regime.exit_policy_ref]]
                else:
                    exits = self._spec.exit_policies

                risk = regime.risk_override or self._spec.risk_policy

                # Apply regime-level execution override to tags
                if regime.execution_override is not None:
                    eo = regime.execution_override
                    exec_tags["placement_mode"] = eo.placement_mode
                    if eo.cancel_after_ticks > 0:
                        exec_tags["cancel_after_ticks"] = eo.cancel_after_ticks
                    if eo.max_reprices > 0:
                        exec_tags["max_reprices"] = eo.max_reprices

                return entries, exits, risk

        # No regime matched — no trading
        return [], [], self._spec.risk_policy

    def _evaluate_entries(
        self,
        rt: RuntimeStateV2,
        features: dict[str, float],
        prev_features: dict[str, float],
        tick: int,
        entries: list[EntryPolicyV2] | None = None,
    ) -> tuple[str, str, float] | None:
        """Evaluate entry policies. Returns (name, side, strength) or None."""
        policies = entries if entries is not None else self._spec.entry_policies
        for ep in policies:
            # Cooldown check
            if tick < rt.cooldown_until:
                continue
            # no_reentry_until_flat check
            if ep.constraints.no_reentry_until_flat and rt.position_side:
                continue
            # Same-side position check — don't add to existing same-side position
            if rt.position_side == ep.side:
                continue

            if evaluate_bool(ep.trigger, features, prev_features, rt):
                strength = evaluate_float(ep.strength, features, rt)
                if strength != 0.0:
                    return (ep.name, ep.side, strength)
        return None

    def _evaluate_exits(
        self,
        rt: RuntimeStateV2,
        features: dict[str, float],
        prev_features: dict[str, float],
        mid: float,
        tick: int,
        exits: list[ExitPolicyV2] | None = None,
    ) -> str | None:
        """Evaluate exit policies. Returns exit rule name if triggered."""
        policies = exits if exits is not None else self._spec.exit_policies
        # Collect all triggered rules across exit policies, sort by priority
        triggered: list[tuple[int, str, ExitRuleV2]] = []
        for xp in policies:
            for rule in xp.rules:
                if evaluate_bool(rule.condition, features, prev_features, rt):
                    triggered.append((rule.priority, rule.name, rule))

        if not triggered:
            return None

        # Take highest priority (lowest number)
        triggered.sort(key=lambda t: t[0])
        _, name, rule = triggered[0]

        if rule.action.type == "close_all":
            return name
        elif rule.action.type == "reduce_position":
            rt.position_size *= (1.0 - rule.action.reduce_fraction)
            if rt.position_size < 1.0:
                return name  # position too small, close entirely
            # Reduced but not closed — no exit signal
            return None

        return name


class StrategyCompilerV2:
    """Compiles StrategySpecV2 into an executable CompiledStrategyV2."""

    @staticmethod
    def compile(spec: StrategySpecV2) -> CompiledStrategyV2:
        errors = spec.validate()
        if errors:
            raise ValueError(
                f"Invalid v2 strategy spec '{spec.name}':\n  - "
                + "\n  - ".join(errors)
            )

        # Warn about unknown features
        all_features = spec.collect_all_features()
        from strategy_block.strategy_compiler.compiler import CompiledStrategy
        unknown = all_features - CompiledStrategy.BUILTIN_FEATURES
        if unknown:
            logger.warning(
                "V2 strategy '%s' references unknown features %s",
                spec.name, sorted(unknown),
            )

        logger.info(
            "Compiled v2 strategy '%s' (v%s) with %d entry policies, "
            "%d exit policies, %d preconditions, %d regimes",
            spec.name, spec.version,
            len(spec.entry_policies), len(spec.exit_policies),
            len(spec.preconditions), len(spec.regimes),
        )
        return CompiledStrategyV2(spec)
