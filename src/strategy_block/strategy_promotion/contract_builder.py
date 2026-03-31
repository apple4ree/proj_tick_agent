"""Deterministic deployment-contract builder."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from strategy_block.strategy_registry.trial_registry import TrialRecord
from strategy_block.strategy_specs.v2.ast_nodes import ComparisonExpr, ExprNode, PositionAttrExpr
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2

from .contract_models import DeploymentContract


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _string_list(values: Any) -> list[str]:
    if isinstance(values, str):
        raw = [values]
    elif isinstance(values, Iterable):
        raw = [str(v) for v in values if v is not None]
    else:
        raw = []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _resolve_promotion_cfg(selection_cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(selection_cfg, Mapping):
        return {}
    if isinstance(selection_cfg.get("promotion"), Mapping):
        return dict(selection_cfg.get("promotion") or {})
    return dict(selection_cfg)


class DeploymentContractBuilder:
    """Build handoff-ready deployment contracts from spec + WF aggregate outputs."""

    _DEFAULT_MONITORING = [
        "aggregate_score",
        "n_pass_windows",
        "children_per_parent",
        "cancel_rate",
        "maker_fill_ratio",
        "queue_blocked_count",
        "blocked_miss_count",
        "adverse_selection_share",
        "total_commission",
        "total_slippage",
        "total_impact",
    ]

    def build(
        self,
        *,
        spec: StrategySpecV2,
        trial_record: TrialRecord | None,
        walk_forward_report: dict[str, Any],
        selection_cfg: dict[str, Any] | None = None,
    ) -> DeploymentContract:
        promo_cfg = _resolve_promotion_cfg(selection_cfg)
        contract_cfg = dict(promo_cfg.get("contract") or {})
        gate_cfg = dict(promo_cfg.get("gate") or {})

        allowed_symbols = self._resolve_allowed_symbols(
            walk_forward_report=walk_forward_report,
            trial_record=trial_record,
            contract_cfg=contract_cfg,
        )
        required_features = sorted(spec.collect_all_features())
        regime_dependencies = [regime.name for regime in spec.regimes]
        if not regime_dependencies:
            regime_dependencies = ["default"]

        expected_holding_horizon_s = self._resolve_holding_horizon_seconds(
            spec=spec,
            walk_forward_report=walk_forward_report,
            trial_record=trial_record,
            contract_cfg=contract_cfg,
        )
        max_turnover = self._resolve_max_turnover(walk_forward_report)
        latency_budget_ms = self._resolve_latency_budget_ms(
            walk_forward_report=walk_forward_report,
            trial_record=trial_record,
        )

        forbidden_time_ranges = self._resolve_forbidden_time_ranges(spec, trial_record)
        monitoring_metrics = self._resolve_monitoring_metrics(contract_cfg)
        disable_conditions = self._resolve_disable_conditions(
            spec=spec,
            trial_record=trial_record,
            gate_cfg=gate_cfg,
            contract_cfg=contract_cfg,
        )

        known_failure_modes = self._resolve_known_failure_modes(
            walk_forward_report=walk_forward_report,
            trial_record=trial_record,
        )

        decision = dict(walk_forward_report.get("decision") or {})
        decision_meta = dict(decision.get("metadata") or {})
        notes = {
            "builder": "deterministic_contract_builder_v1",
            "walk_forward_passed": bool(decision.get("passed", False)),
            "walk_forward_aggregate_score": _safe_float(decision.get("aggregate_score"), 0.0),
            "walk_forward_reasons": [str(v) for v in decision.get("reasons") or []],
            "window_count": int(walk_forward_report.get("n_windows", len(walk_forward_report.get("window_results") or []))),
            "n_valid_windows": int(decision_meta.get("n_valid_windows", 0)),
            "n_pass_windows": int(decision_meta.get("n_pass_windows", 0)),
            "flag_shares": {
                "churn_heavy_share": _safe_float(decision_meta.get("churn_heavy_share"), 0.0),
                "cost_dominated_share": _safe_float(decision_meta.get("cost_dominated_share"), 0.0),
                "adverse_selection_dominated_share": _safe_float(decision_meta.get("adverse_selection_dominated_share"), 0.0),
                "queue_ineffective_share": self._share_flag(walk_forward_report, "queue_ineffective"),
            },
        }
        if trial_record is not None:
            notes["trial_stage"] = trial_record.stage
            notes["trial_status"] = trial_record.status

        return DeploymentContract(
            strategy_name=spec.name,
            strategy_version=spec.version,
            trial_id=trial_record.trial_id if trial_record else None,
            family_id=trial_record.family_id if trial_record else None,
            allowed_symbols=allowed_symbols,
            expected_holding_horizon_s=expected_holding_horizon_s,
            max_turnover=max_turnover,
            latency_budget_ms=latency_budget_ms,
            forbidden_time_ranges=forbidden_time_ranges,
            required_features=required_features,
            regime_dependencies=regime_dependencies,
            disable_conditions=disable_conditions,
            monitoring_metrics=monitoring_metrics,
            known_failure_modes=known_failure_modes,
            notes=notes,
        )

    def _resolve_allowed_symbols(
        self,
        *,
        walk_forward_report: dict[str, Any],
        trial_record: TrialRecord | None,
        contract_cfg: dict[str, Any],
    ) -> list[str]:
        if trial_record is not None:
            trial_allowed = _string_list((trial_record.metadata or {}).get("allowed_symbols"))
            if trial_allowed:
                return trial_allowed

        mode = str(walk_forward_report.get("mode") or "").strip().lower()
        symbol = walk_forward_report.get("symbol")
        if mode == "single" and symbol:
            return [str(symbol)]
        if mode == "universe":
            symbols = _string_list(walk_forward_report.get("symbols"))
            if symbols:
                return symbols
            return [str(contract_cfg.get("universe_symbol_token", "__universe__"))]

        defaults = _string_list(contract_cfg.get("allowed_symbols_default"))
        return defaults

    def _resolve_holding_horizon_seconds(
        self,
        *,
        spec: StrategySpecV2,
        walk_forward_report: dict[str, Any],
        trial_record: TrialRecord | None,
        contract_cfg: dict[str, Any],
    ) -> tuple[float, float] | None:
        tick_s = self._tick_seconds(
            walk_forward_report=walk_forward_report,
            trial_record=trial_record,
            contract_cfg=contract_cfg,
        )
        lower, upper = self._infer_holding_ticks(spec, trial_record)
        if lower is None and upper is None:
            return None
        lo = float(lower if lower is not None else 1.0)
        hi = float(upper if upper is not None else max(lo, lo * 2.0))
        if hi < lo:
            lo, hi = hi, lo
        return (round(lo * tick_s, 6), round(hi * tick_s, 6))

    def _infer_holding_ticks(
        self,
        spec: StrategySpecV2,
        trial_record: TrialRecord | None,
    ) -> tuple[float | None, float | None]:
        lowers: list[float] = []
        uppers: list[float] = []
        for xp in spec.exit_policies:
            for rule in xp.rules:
                for node in self._iter_expr_nodes(rule.condition):
                    if not isinstance(node, ComparisonExpr):
                        continue
                    if not self._is_holding_ticks_expr(node):
                        continue
                    th = float(node.threshold)
                    if node.op in {">", ">="}:
                        lowers.append(th if node.op == ">=" else th + 1.0)
                    elif node.op in {"<", "<="}:
                        uppers.append(th if node.op == "<=" else max(1.0, th - 1.0))
                    elif node.op == "==":
                        lowers.append(th)
                        uppers.append(th)

        if lowers or uppers:
            lo = max(lowers) if lowers else None
            hi = min(uppers) if uppers else None
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
            return lo, hi

        if spec.execution_policy is not None and spec.execution_policy.cancel_after_ticks > 0:
            return 1.0, float(spec.execution_policy.cancel_after_ticks)

        for source in ((trial_record.metadata if trial_record else None), spec.metadata):
            if not isinstance(source, Mapping):
                continue
            value = source.get("inferred_holding_horizon_ticks")
            parsed = _safe_float(value, None)
            if parsed is not None and parsed > 0:
                return max(1.0, parsed / 2.0), parsed
        return None, None

    def _is_holding_ticks_expr(self, node: ComparisonExpr) -> bool:
        if node.left is not None:
            return isinstance(node.left, PositionAttrExpr) and node.left.name == "holding_ticks"
        return str(node.feature) == "holding_ticks"

    def _iter_expr_nodes(self, root: ExprNode) -> list[ExprNode]:
        stack: list[ExprNode] = [root]
        out: list[ExprNode] = []
        while stack:
            node = stack.pop()
            out.append(node)
            children = getattr(node, "children", None)
            if isinstance(children, list):
                stack.extend(children)
            child = getattr(node, "child", None)
            if isinstance(child, ExprNode):
                stack.append(child)
            expr = getattr(node, "expr", None)
            if isinstance(expr, ExprNode):
                stack.append(expr)
            left = getattr(node, "left", None)
            if isinstance(left, ExprNode):
                stack.append(left)
        return out

    def _tick_seconds(
        self,
        *,
        walk_forward_report: dict[str, Any],
        trial_record: TrialRecord | None,
        contract_cfg: dict[str, Any],
    ) -> float:
        sources: list[Any] = []
        if trial_record is not None and isinstance(trial_record.metadata, Mapping):
            sources.append(trial_record.metadata.get("canonical_tick_interval_ms"))
        decision_meta = dict((walk_forward_report.get("decision") or {}).get("metadata") or {})
        sources.append(decision_meta.get("canonical_tick_interval_ms"))

        for window in walk_forward_report.get("window_results") or []:
            run_dir = Path(str(window.get("run_dir") or ""))
            if not run_dir:
                continue
            summary_path = run_dir / "summary.json"
            if summary_path.exists():
                try:
                    payload = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                sources.append(payload.get("canonical_tick_interval_ms"))
                break

        for candidate in sources:
            ms = _safe_float(candidate, None)
            if ms is not None and ms > 0:
                return ms / 1000.0

        return float(contract_cfg.get("tick_seconds_default", 1.0))

    def _resolve_max_turnover(self, walk_forward_report: dict[str, Any]) -> float | None:
        values: list[float] = []
        for window in walk_forward_report.get("window_results") or []:
            meta = dict(window.get("metadata") or {})
            val = _safe_float(meta.get("children_per_parent"), None)
            if val is not None:
                values.append(val)
        if not values:
            return None
        return round(sum(values) / len(values), 6)

    def _resolve_latency_budget_ms(
        self,
        *,
        walk_forward_report: dict[str, Any],
        trial_record: TrialRecord | None,
    ) -> float | None:
        if trial_record is not None:
            md = dict(trial_record.metadata or {})
            explicit = _safe_float(md.get("latency_budget_ms"), None)
            if explicit is not None:
                return explicit
            submit = _safe_float(md.get("configured_order_submit_ms"), 0.0) or 0.0
            cancel = _safe_float(md.get("configured_cancel_ms"), 0.0) or 0.0
            effective = _safe_float(md.get("effective_delay_ms"), 0.0) or 0.0
            if submit or cancel or effective:
                return round(submit + cancel + effective, 6)

        for window in walk_forward_report.get("window_results") or []:
            run_dir = Path(str(window.get("run_dir") or ""))
            summary_path = run_dir / "summary.json"
            if not summary_path.exists():
                continue
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            submit = _safe_float(payload.get("configured_order_submit_ms"), 0.0) or 0.0
            cancel = _safe_float(payload.get("configured_cancel_ms"), 0.0) or 0.0
            effective = _safe_float(payload.get("effective_delay_ms"), 0.0) or 0.0
            if submit or cancel or effective:
                return round(submit + cancel + effective, 6)
        return None

    def _resolve_forbidden_time_ranges(
        self,
        spec: StrategySpecV2,
        trial_record: TrialRecord | None,
    ) -> list[str]:
        out: list[str] = []
        out.extend(_string_list(spec.metadata.get("forbidden_time_ranges")))
        if trial_record is not None:
            out.extend(_string_list((trial_record.metadata or {}).get("forbidden_time_ranges")))
        if spec.execution_policy is not None and spec.execution_policy.do_not_trade_when is not None:
            out.append("execution_policy.do_not_trade_when")
        return _string_list(out)

    def _resolve_monitoring_metrics(self, contract_cfg: dict[str, Any]) -> list[str]:
        required = _string_list(contract_cfg.get("required_monitoring_metrics"))
        return _string_list(self._DEFAULT_MONITORING + required)

    def _resolve_disable_conditions(
        self,
        *,
        spec: StrategySpecV2,
        trial_record: TrialRecord | None,
        gate_cfg: dict[str, Any],
        contract_cfg: dict[str, Any],
    ) -> list[str]:
        out: list[str] = []
        out.extend(_string_list(spec.metadata.get("disable_conditions")))
        if trial_record is not None:
            out.extend(_string_list((trial_record.metadata or {}).get("disable_conditions")))
        out.extend(_string_list(contract_cfg.get("required_disable_conditions")))

        if gate_cfg.get("min_aggregate_score") is not None:
            out.append(f"disable_if_aggregate_score_below:{gate_cfg['min_aggregate_score']}")
        if gate_cfg.get("max_churn_heavy_share") is not None:
            out.append(f"disable_if_churn_heavy_share_above:{gate_cfg['max_churn_heavy_share']}")
        if gate_cfg.get("max_cost_dominated_share") is not None:
            out.append(f"disable_if_cost_dominated_share_above:{gate_cfg['max_cost_dominated_share']}")
        if gate_cfg.get("max_adverse_selection_dominated_share") is not None:
            out.append(
                f"disable_if_adverse_selection_dominated_share_above:{gate_cfg['max_adverse_selection_dominated_share']}"
            )
        if gate_cfg.get("max_queue_ineffective_share") is not None:
            out.append(f"disable_if_queue_ineffective_share_above:{gate_cfg['max_queue_ineffective_share']}")

        return _string_list(out)

    def _resolve_known_failure_modes(
        self,
        *,
        walk_forward_report: dict[str, Any],
        trial_record: TrialRecord | None,
    ) -> list[str]:
        out: list[str] = []
        decision = dict(walk_forward_report.get("decision") or {})
        out.extend([f"walk_forward_reason:{reason}" for reason in decision.get("reasons") or []])

        for key in ("churn_heavy", "queue_ineffective", "cost_dominated", "adverse_selection_dominated"):
            share = self._share_flag(walk_forward_report, key)
            if share > 0:
                out.append(f"flag_prevalence:{key}={share:.3f}")

        if trial_record is not None and isinstance(trial_record.metadata, Mapping):
            static_review = trial_record.metadata.get("static_review")
            if isinstance(static_review, Mapping):
                for issue in static_review.get("issues") or []:
                    if isinstance(issue, Mapping):
                        category = str(issue.get("category") or "unknown")
                        out.append(f"review_issue:{category}")

        return _string_list(out)

    def _share_flag(self, report: Mapping[str, Any], key: str) -> float:
        windows = report.get("window_results") or []
        if not windows:
            return 0.0
        flags: list[bool] = []
        for window in windows:
            metadata = dict((window or {}).get("metadata") or {})
            flag_map = dict(metadata.get("flags") or {})
            flags.append(bool(flag_map.get(key)))
        return round(sum(1 for v in flags if v) / len(flags), 6)
