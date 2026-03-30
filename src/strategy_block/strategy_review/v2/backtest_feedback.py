"""Backtest feedback summary extraction for review/repair prompts (Phase 3A)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    BacktestFeedbackCancelMix,
    BacktestFeedbackContext,
    BacktestFeedbackCost,
    BacktestFeedbackFlags,
    BacktestFeedbackLifecycle,
    BacktestFeedbackQueue,
    BacktestFeedbackSummary,
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return _dict(payload)


def _safe_share(shares: dict[str, Any], key: str) -> float | None:
    return _to_float(shares.get(key))


def _derived_flags(*, lifecycle: BacktestFeedbackLifecycle, queue: BacktestFeedbackQueue, cancel: BacktestFeedbackCancelMix, cost: BacktestFeedbackCost) -> BacktestFeedbackFlags:
    cancel_rate = lifecycle.cancel_rate
    children_per_parent = lifecycle.children_per_parent
    max_children = lifecycle.max_children_per_parent

    churn_heavy = bool(
        (cancel_rate is not None and cancel_rate >= 0.80)
        or (children_per_parent is not None and children_per_parent >= 8.0)
        or (max_children is not None and max_children >= 100.0)
    )

    queue_blocked = queue.queue_blocked_count or 0.0
    blocked_miss = queue.blocked_miss_count or 0.0
    queue_ready = queue.queue_ready_count or 0.0
    maker_fill_ratio = queue.maker_fill_ratio
    queue_ineffective = bool(
        (blocked_miss > 0.0 and (maker_fill_ratio is None or maker_fill_ratio <= 0.10))
        or (queue_blocked > max(5.0, 2.0 * queue_ready))
    )

    cost_total = 0.0
    has_cost = False
    for value in (cost.total_commission, cost.total_slippage, cost.total_impact):
        if value is None:
            continue
        cost_total += abs(value)
        has_cost = True

    net_pnl = cost.net_pnl
    cost_dominated = False
    if has_cost and net_pnl is not None:
        if net_pnl <= 0.0:
            cost_dominated = cost_total >= max(1.0, abs(net_pnl) * 0.5)
        else:
            cost_dominated = cost_total > net_pnl

    adverse_share = cancel.adverse_selection_share
    timeout_share = cancel.timeout_share or 0.0
    adverse_selection_dominated = bool(
        adverse_share is not None
        and adverse_share >= 0.50
        and adverse_share >= timeout_share
    )

    return BacktestFeedbackFlags(
        churn_heavy=churn_heavy,
        queue_ineffective=queue_ineffective,
        cost_dominated=cost_dominated,
        adverse_selection_dominated=adverse_selection_dominated,
    )


def load_backtest_feedback(run_dir: str | Path) -> BacktestFeedbackSummary:
    """Load compact aggregate feedback from backtest artifacts.

    Only aggregate artifacts are used:
    - summary.json
    - realism_diagnostics.json
    """
    root = Path(run_dir)
    summary = _load_json(root / "summary.json")
    diagnostics = _load_json(root / "realism_diagnostics.json")

    lifecycle_d = _dict(diagnostics.get("lifecycle"))
    queue_d = _dict(diagnostics.get("queue"))
    cancel_d = _dict(diagnostics.get("cancel_reasons"))
    cancel_shares = _dict(cancel_d.get("shares"))
    latency_d = _dict(diagnostics.get("latency"))
    tick_d = _dict(diagnostics.get("tick_time"))

    signal_count = _to_float(_coalesce(lifecycle_d.get("signal_count"), summary.get("signal_count")))
    parent_order_count = _to_float(_coalesce(lifecycle_d.get("parent_order_count"), summary.get("parent_order_count")))
    child_order_count = _to_float(_coalesce(lifecycle_d.get("child_order_count"), summary.get("child_order_count")))

    children_per_parent = _to_float(lifecycle_d.get("children_per_parent"))
    if children_per_parent is None and child_order_count is not None and parent_order_count not in (None, 0.0):
        children_per_parent = child_order_count / float(parent_order_count)

    lifecycle = BacktestFeedbackLifecycle(
        signal_count=signal_count,
        parent_order_count=parent_order_count,
        child_order_count=child_order_count,
        children_per_parent=children_per_parent,
        cancel_rate=_to_float(_coalesce(lifecycle_d.get("cancel_rate"), summary.get("cancel_rate"))),
        avg_child_lifetime_seconds=_to_float(_coalesce(lifecycle_d.get("avg_child_lifetime_seconds"), summary.get("avg_child_lifetime_seconds"))),
        max_children_per_parent=_to_float(lifecycle_d.get("max_children_per_parent")),
    )

    queue = BacktestFeedbackQueue(
        queue_model=str(_coalesce(queue_d.get("queue_model"), summary.get("queue_model"))) if _coalesce(queue_d.get("queue_model"), summary.get("queue_model")) is not None else None,
        queue_blocked_count=_to_float(queue_d.get("queue_blocked_count")),
        blocked_miss_count=_to_float(queue_d.get("blocked_miss_count")),
        queue_ready_count=_to_float(queue_d.get("queue_ready_count")),
        maker_fill_ratio=_to_float(_coalesce(queue_d.get("maker_fill_ratio"), summary.get("maker_fill_ratio"))),
    )

    cancel_reasons = BacktestFeedbackCancelMix(
        adverse_selection_share=_safe_share(cancel_shares, "adverse_selection"),
        timeout_share=_safe_share(cancel_shares, "timeout"),
        stale_price_share=_safe_share(cancel_shares, "stale_price"),
        max_reprices_reached_share=_safe_share(cancel_shares, "max_reprices_reached"),
        micro_event_block_share=_safe_share(cancel_shares, "micro_event_block"),
    )

    cost = BacktestFeedbackCost(
        net_pnl=_to_float(summary.get("net_pnl")),
        total_commission=_to_float(summary.get("total_commission")),
        total_slippage=_to_float(summary.get("total_slippage")),
        total_impact=_to_float(summary.get("total_impact")),
    )

    context = BacktestFeedbackContext(
        resample=(
            str(_coalesce(summary.get("resample_interval"), tick_d.get("resample_interval")))
            if _coalesce(summary.get("resample_interval"), tick_d.get("resample_interval")) is not None
            else None
        ),
        canonical_tick_interval_ms=_to_float(_coalesce(summary.get("canonical_tick_interval_ms"), tick_d.get("canonical_tick_interval_ms"))),
        configured_order_submit_ms=_to_float(_coalesce(summary.get("configured_order_submit_ms"), latency_d.get("configured_order_submit_ms"))),
        configured_cancel_ms=_to_float(_coalesce(summary.get("configured_cancel_ms"), latency_d.get("configured_cancel_ms"))),
    )

    feedback_available = bool(summary or diagnostics)
    flags = _derived_flags(
        lifecycle=lifecycle,
        queue=queue,
        cancel=cancel_reasons,
        cost=cost,
    )

    return BacktestFeedbackSummary(
        feedback_available=feedback_available,
        lifecycle=lifecycle,
        queue=queue,
        cancel_reasons=cancel_reasons,
        cost=cost,
        context=context,
        flags=flags,
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        val = float(value)
        if abs(val) >= 1000:
            return f"{val:,.2f}"
        return f"{val:.4f}".rstrip("0").rstrip(".")
    return str(value)


def build_backtest_feedback_summary(feedback: BacktestFeedbackSummary | None) -> str:
    """Render compact human-readable feedback summary for prompts."""
    if feedback is None or not feedback.feedback_available:
        return (
            "No recent backtest feedback provided; critique spec using static review + "
            "environment context only."
        )

    lifecycle = feedback.lifecycle
    queue = feedback.queue
    cancel = feedback.cancel_reasons
    cost = feedback.cost
    context = feedback.context
    flags = feedback.flags

    lines = [
        "Recent backtest feedback (aggregate-only):",
        (
            "- lifecycle: "
            f"signals={_fmt(lifecycle.signal_count)}, parents={_fmt(lifecycle.parent_order_count)}, "
            f"children={_fmt(lifecycle.child_order_count)}, children_per_parent={_fmt(lifecycle.children_per_parent)}, "
            f"cancel_rate={_fmt(lifecycle.cancel_rate)}, avg_child_lifetime_s={_fmt(lifecycle.avg_child_lifetime_seconds)}, "
            f"max_children_per_parent={_fmt(lifecycle.max_children_per_parent)}"
        ),
        (
            "- queue: "
            f"queue_model={_fmt(queue.queue_model)}, queue_blocked_count={_fmt(queue.queue_blocked_count)}, "
            f"blocked_miss_count={_fmt(queue.blocked_miss_count)}, queue_ready_count={_fmt(queue.queue_ready_count)}, "
            f"maker_fill_ratio={_fmt(queue.maker_fill_ratio)}"
        ),
        (
            "- cancel_reason_shares: "
            f"adverse_selection={_fmt(cancel.adverse_selection_share)}, timeout={_fmt(cancel.timeout_share)}, "
            f"stale_price={_fmt(cancel.stale_price_share)}, max_reprices_reached={_fmt(cancel.max_reprices_reached_share)}, "
            f"micro_event_block={_fmt(cancel.micro_event_block_share)}"
        ),
        (
            "- pnl_cost: "
            f"net_pnl={_fmt(cost.net_pnl)}, total_commission={_fmt(cost.total_commission)}, "
            f"total_slippage={_fmt(cost.total_slippage)}, total_impact={_fmt(cost.total_impact)}"
        ),
        (
            "- context: "
            f"resample={_fmt(context.resample)}, canonical_tick_interval_ms={_fmt(context.canonical_tick_interval_ms)}, "
            f"configured_order_submit_ms={_fmt(context.configured_order_submit_ms)}, "
            f"configured_cancel_ms={_fmt(context.configured_cancel_ms)}"
        ),
        (
            "- derived_flags: "
            f"churn_heavy={_fmt(flags.churn_heavy)}, queue_ineffective={_fmt(flags.queue_ineffective)}, "
            f"cost_dominated={_fmt(flags.cost_dominated)}, adverse_selection_dominated={_fmt(flags.adverse_selection_dominated)}"
        ),
    ]
    return "\n".join(lines)
