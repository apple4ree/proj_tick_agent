"""Compiler v2 — converts StrategySpecV2 into an executable Strategy.

Phase 3 semantics:
1. Extract features + update history
2. If in-position: evaluate exits first (entry gates never block exits)
3. If flat: apply entry gating (do_not_trade/preconditions/state guards/regime)
4. Apply degradation + entry policies
5. Emit signal with execution hints (+ adaptation overrides)
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
    RiskPolicyV2,
    ExecutionPolicyV2,
)
from .runtime_v2 import RuntimeStateV2, evaluate_bool, evaluate_float
from .features import BUILTIN_FEATURES, extract_builtin_features

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

        self._state_defaults: dict[str, float] = {}
        self._state_events: dict[str, list] = {}
        if spec.state_policy is not None:
            self._state_defaults = {
                k: float(v) for k, v in spec.state_policy.vars.items()
            }
            for event in spec.state_policy.events:
                self._state_events.setdefault(event.on, []).append(event)

    @property
    def name(self) -> str:
        return f"CompiledV2:{self._spec.name}"

    def reset(self) -> None:
        self._states.clear()

    def _get_state(self, symbol: str) -> RuntimeStateV2:
        if symbol not in self._states:
            self._states[symbol] = RuntimeStateV2(
                state_vars=dict(self._state_defaults)
            )
        return self._states[symbol]

    def _build_exec_tags(self, policy: ExecutionPolicyV2 | None = None) -> dict[str, object]:
        """Build execution hint tags from execution policy."""
        xp = policy if policy is not None else self._spec.execution_policy
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

    def _apply_execution_overrides(self, tags: dict[str, object],
                                   policy: ExecutionPolicyV2 | None) -> None:
        if policy is None:
            return
        tags["placement_mode"] = policy.placement_mode
        if policy.cancel_after_ticks > 0:
            tags["cancel_after_ticks"] = policy.cancel_after_ticks
        else:
            tags.pop("cancel_after_ticks", None)
        if policy.max_reprices > 0:
            tags["max_reprices"] = policy.max_reprices
        else:
            tags.pop("max_reprices", None)

    def _apply_execution_adaptation_rules(
        self,
        policy: ExecutionPolicyV2 | None,
        rt: RuntimeStateV2,
        features: dict[str, float],
        prev_features: dict[str, float],
        tags: dict[str, object],
    ) -> None:
        """Apply hint-level execution adaptation rules on entry path only."""
        if policy is None:
            return

        for rule in policy.adaptation_rules:
            if not evaluate_bool(rule.condition, features, prev_features, rt):
                continue

            ov = rule.override
            if ov.placement_mode is not None:
                tags["placement_mode"] = ov.placement_mode
            if ov.cancel_after_ticks is not None:
                if ov.cancel_after_ticks > 0:
                    tags["cancel_after_ticks"] = ov.cancel_after_ticks
                else:
                    tags.pop("cancel_after_ticks", None)
            if ov.max_reprices is not None:
                if ov.max_reprices > 0:
                    tags["max_reprices"] = ov.max_reprices
                else:
                    tags.pop("max_reprices", None)

    def _evaluate_state_guards(
        self,
        rt: RuntimeStateV2,
        features: dict[str, float],
        prev_features: dict[str, float],
    ) -> bool:
        """Return True when entry must be blocked by state guards."""
        sp = self._spec.state_policy
        if sp is None:
            return False

        for guard in sp.guards:
            if guard.effect != "block_entry":
                continue
            if evaluate_bool(guard.condition, features, prev_features, rt):
                return True
        return False

    def _apply_state_event(self, rt: RuntimeStateV2, event_name: str) -> None:
        """Apply state updates for a named runtime event."""
        events = self._state_events.get(event_name, [])
        if not events:
            return

        for event in events:
            for upd in event.updates:
                if upd.var not in rt.state_vars:
                    rt.state_vars[upd.var] = self._state_defaults.get(upd.var, 0.0)

                if upd.op == "set":
                    rt.state_vars[upd.var] = float(upd.value)
                elif upd.op == "increment":
                    rt.state_vars[upd.var] += float(upd.value)
                elif upd.op == "reset":
                    rt.state_vars[upd.var] = self._state_defaults.get(upd.var, 0.0)

    def _apply_degradation_rules(
        self,
        rt: RuntimeStateV2,
        features: dict[str, float],
        prev_features: dict[str, float],
        risk: RiskPolicyV2,
    ) -> tuple[bool, float, float]:
        """Return (allow_entry, strength_scale, max_position_scale)."""
        allow_entry = True
        strength_scale = 1.0
        max_position_scale = 1.0

        for rule in risk.degradation_rules:
            if not evaluate_bool(rule.condition, features, prev_features, rt):
                continue

            action = rule.action
            if action.type == "block_new_entries":
                allow_entry = False
            elif action.type == "scale_strength":
                strength_scale *= max(0.0, float(action.factor))
            elif action.type == "scale_max_position":
                max_position_scale *= max(0.0, float(action.factor))

        return allow_entry, strength_scale, max_position_scale

    def _flatten_position(self, rt: RuntimeStateV2) -> None:
        rt.position_side = ""
        rt.position_size = 0.0
        rt.entry_tick = -1
        rt.entry_price = 0.0
        rt.trailing_high = 0.0
        rt.trailing_low = float("inf")

    def generate_signal(self, state: "MarketState") -> "Signal | None":
        from execution_planning.layer1_signal import Signal
        if state.lob.mid_price is None:
            return None

        symbol = state.symbol
        mid = state.lob.mid_price
        rt = self._get_state(symbol)
        rt.tick_count += 1
        tick = rt.tick_count

        features = extract_builtin_features(state)

        prev_features = rt.prev_features

        # Record feature history for lag/rolling/persist
        rt.record_features(features)

        # Base execution tags from top-level execution policy
        exec_tags = self._build_exec_tags(self._spec.execution_policy)

        # Exit-first semantics: in-position path never blocked by entry gates.
        if rt.position_side:
            _, active_exits, _, _, _ = self._select_regime(
                rt, features, prev_features, exec_tags, for_entry=False
            )
            exit_signal = self._evaluate_exits(
                rt, features, prev_features, mid, tick, active_exits
            )
            if exit_signal is not None:
                current_side = rt.position_side
                score = -1.0 if current_side == "long" else 1.0
                exit_type = exit_signal

                # Evaluate realized PnL sign before flattening.
                pnl = (mid - rt.entry_price) if current_side == "long" else (rt.entry_price - mid)
                if pnl > 0:
                    self._apply_state_event(rt, "on_exit_profit")
                elif pnl < 0:
                    self._apply_state_event(rt, "on_exit_loss")

                self._flatten_position(rt)
                self._apply_state_event(rt, "on_flatten")

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

            # No exit: keep position open and update trailing prices.
            if rt.position_side == "long":
                rt.trailing_high = max(rt.trailing_high, mid)
            else:
                rt.trailing_low = min(rt.trailing_low, mid)

            rt.prev_features = features
            return None

        # Entry path only (flat state)

        # 1) do_not_trade_when gating
        xp = self._spec.execution_policy
        if xp and xp.do_not_trade_when is not None:
            if evaluate_bool(xp.do_not_trade_when, features, prev_features, rt):
                rt.prev_features = features
                return None

        # 2) Preconditions
        for pc in self._spec.preconditions:
            if not evaluate_bool(pc.condition, features, prev_features, rt):
                rt.prev_features = features
                return None

        # 3) State guards
        if self._evaluate_state_guards(rt, features, prev_features):
            rt.prev_features = features
            return None

        # 4) Regime routing (flat no-match => no entry)
        active_entries, _, active_risk, active_exec_policy, regime_matched = self._select_regime(
            rt, features, prev_features, exec_tags, for_entry=True
        )
        if self._spec.regimes and not regime_matched:
            rt.prev_features = features
            return None

        # 5) Risk degradation rules (entry-only)
        allow_entry, strength_scale, max_pos_scale = self._apply_degradation_rules(
            rt, features, prev_features, active_risk
        )
        if not allow_entry:
            rt.prev_features = features
            return None

        # 6) Entry policy evaluation
        entry_result = self._evaluate_entries(
            rt, features, prev_features, tick, active_entries
        )
        if entry_result is None:
            rt.prev_features = features
            return None

        entry_name, side, strength = entry_result
        strength *= strength_scale

        # 7) Compute score
        score = strength if side == "long" else -strength
        score = max(-1.0, min(1.0, score))

        if score == 0.0:
            rt.prev_features = features
            return None

        # 8) Apply degraded sizing caps
        effective_max_position = max(0.0, active_risk.max_position * max_pos_scale)
        effective_max_size = max(0.0, active_risk.position_sizing.max_size * max_pos_scale)
        if effective_max_position <= 0.0 or effective_max_size <= 0.0:
            rt.prev_features = features
            return None

        position_size = abs(score) * effective_max_size
        position_size = min(position_size, effective_max_position)
        position_size = min(position_size, float(active_risk.inventory_cap))
        if position_size <= 0.0:
            rt.prev_features = features
            return None

        # Update position state
        rt.entry_price = mid
        rt.entry_tick = tick
        rt.trailing_high = mid
        rt.trailing_low = mid
        rt.position_side = side
        rt.position_size = position_size

        # Apply cooldown from the triggering entry policy
        for ep in self._spec.entry_policies:
            if ep.name == entry_name and ep.constraints.cooldown_ticks > 0:
                rt.cooldown_until = tick + ep.constraints.cooldown_ticks
                break

        # Entry event
        self._apply_state_event(rt, "on_entry")

        # 9) Execution adaptation rules (entry-only)
        entry_exec_tags = dict(exec_tags)
        self._apply_execution_adaptation_rules(
            active_exec_policy, rt, features, prev_features, entry_exec_tags
        )

        rt.prev_features = features

        tags = {"strategy": self.name, "entry_policy": entry_name,
                "spec_format": "v2"}
        tags.update(entry_exec_tags)

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
        *,
        for_entry: bool,
    ) -> tuple[list[EntryPolicyV2], list[ExitPolicyV2], RiskPolicyV2,
               ExecutionPolicyV2 | None, bool]:
        """Select active regime and return policy context.

        Returns: (entries, exits, risk, execution_policy, matched)

        Regime no-match semantics:
        - for_entry=True: no entry (empty entries)
        - for_entry=False: exits fall back to global exit policies
        """
        default_exec = self._spec.execution_policy

        if not self._spec.regimes:
            return (
                self._spec.entry_policies,
                self._spec.exit_policies,
                self._spec.risk_policy,
                default_exec,
                True,
            )

        sorted_regimes = sorted(self._spec.regimes, key=lambda r: r.priority)

        for regime in sorted_regimes:
            if not evaluate_bool(regime.when, features, prev_features, rt):
                continue

            entries = [
                self._entry_by_name[ref]
                for ref in regime.entry_policy_refs
                if ref in self._entry_by_name
            ]

            if regime.exit_policy_ref and regime.exit_policy_ref in self._exit_by_name:
                exits = [self._exit_by_name[regime.exit_policy_ref]]
            else:
                exits = self._spec.exit_policies

            risk = regime.risk_override or self._spec.risk_policy
            exec_policy = regime.execution_override or default_exec

            if regime.execution_override is not None:
                self._apply_execution_overrides(exec_tags, regime.execution_override)

            return entries, exits, risk, exec_policy, True

        if for_entry:
            return [], [], self._spec.risk_policy, default_exec, False

        # In-position fallback: exits must remain evaluable.
        return [], self._spec.exit_policies, self._spec.risk_policy, default_exec, False

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
            if tick < rt.cooldown_until:
                continue
            if ep.constraints.no_reentry_until_flat and rt.position_side:
                continue
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
        """Evaluate exit policies. Returns exit rule name if flattened."""
        del mid, tick  # currently unused by declarative exit rules

        policies = exits if exits is not None else self._spec.exit_policies
        triggered: list[tuple[int, str, ExitRuleV2]] = []
        for xp in policies:
            for rule in xp.rules:
                if evaluate_bool(rule.condition, features, prev_features, rt):
                    triggered.append((rule.priority, rule.name, rule))

        if not triggered:
            return None

        triggered.sort(key=lambda t: t[0])
        _, name, rule = triggered[0]

        if rule.action.type == "close_all":
            return name

        if rule.action.type == "reduce_position":
            rt.position_size *= (1.0 - rule.action.reduce_fraction)
            if rt.position_size < 1.0:
                return name
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
        unknown = all_features - BUILTIN_FEATURES
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
