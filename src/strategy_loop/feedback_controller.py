"""
strategy_loop/feedback_controller.py
-------------------------------------
Deterministic feedback controller:
  - derives authoritative metrics from backtest summary
  - determines diagnosis/severity/control/verdict in Python
"""
from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: float, digits: int = 4) -> str:
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    if "." not in text:
        return text
    return text


def compute_derived_metrics(backtest_summary: dict[str, Any]) -> dict[str, Any]:
    total_realized_pnl = _to_float(backtest_summary.get("total_realized_pnl"))
    total_unrealized_pnl = _to_float(backtest_summary.get("total_unrealized_pnl"))
    total_commission = _to_float(backtest_summary.get("total_commission"))
    total_slippage = _to_float(backtest_summary.get("total_slippage"))
    total_impact = _to_float(backtest_summary.get("total_impact"))
    signal_count = _to_float(backtest_summary.get("signal_count"))
    n_states = _to_float(backtest_summary.get("n_states"))
    n_fills = _to_float(backtest_summary.get("n_fills"))
    avg_holding_period = _to_float(backtest_summary.get("avg_holding_period"))
    net_pnl = _to_float(backtest_summary.get("net_pnl"))

    gross_pnl_before_explicit_fees = total_realized_pnl + total_unrealized_pnl
    estimated_total_cost = total_commission + total_slippage + total_impact
    fee_drain_ratio = (
        estimated_total_cost / gross_pnl_before_explicit_fees
        if gross_pnl_before_explicit_fees > 0
        else None
    )
    entry_frequency = signal_count / n_states if n_states > 0 else 0.0

    no_trades = signal_count <= 0
    no_fills_after_signal = signal_count > 0 and n_fills <= 0
    entry_too_frequent = entry_frequency > 0.05
    holding_too_short = avg_holding_period < 10

    return {
        "gross_pnl_before_explicit_fees": gross_pnl_before_explicit_fees,
        "estimated_total_cost": estimated_total_cost,
        "fee_drain_ratio": fee_drain_ratio,
        "entry_frequency": entry_frequency,
        "signal_count": signal_count,
        "n_states": n_states,
        "n_fills": n_fills,
        "avg_holding_period": avg_holding_period,
        "net_pnl": net_pnl,
        "gross_pnl_positive": gross_pnl_before_explicit_fees > 0,
        "no_trades": no_trades,
        "no_fills_after_signal": no_fills_after_signal,
        "entry_too_frequent": entry_too_frequent,
        "holding_too_short": holding_too_short,
    }


def compute_controller_decision(derived_metrics: dict[str, Any]) -> dict[str, Any]:
    signal_count = _to_float(derived_metrics.get("signal_count"))
    n_fills = _to_float(derived_metrics.get("n_fills"))
    avg_holding_period = _to_float(derived_metrics.get("avg_holding_period"))
    gross = _to_float(derived_metrics.get("gross_pnl_before_explicit_fees"))
    entry_frequency = _to_float(derived_metrics.get("entry_frequency"))
    net_pnl = _to_float(derived_metrics.get("net_pnl"))

    raw_fee_drain_ratio = derived_metrics.get("fee_drain_ratio")
    fee_drain_ratio = _to_float(raw_fee_drain_ratio) if raw_fee_drain_ratio is not None else None

    reasons: list[str] = []

    # Primary diagnosis order (strict)
    if signal_count <= 0:
        diagnosis_code = "no_trades"
        reasons.append(f"signal_count={_fmt(signal_count)} <= 0")
    elif signal_count > 0 and n_fills <= 0:
        diagnosis_code = "no_fills_after_signal"
        reasons.append(
            f"signal_count={_fmt(signal_count)} > 0 and n_fills={_fmt(n_fills)} <= 0"
        )
    elif entry_frequency > 0.05:
        diagnosis_code = "overtrading"
        reasons.append(f"entry_frequency={_fmt(entry_frequency)} > 0.05")
    elif avg_holding_period < 10:
        diagnosis_code = "exit_too_short"
        reasons.append(f"avg_holding_period={_fmt(avg_holding_period)} < 10")
    elif gross <= 0:
        diagnosis_code = "signal_negative_before_cost"
        reasons.append(f"gross_pnl_before_explicit_fees={_fmt(gross)} <= 0")
    elif gross > 0 and fee_drain_ratio is not None and fee_drain_ratio > 1.0:
        diagnosis_code = "fee_dominated"
        reasons.append(f"fee_drain_ratio={_fmt(fee_drain_ratio)} > 1.0")
    else:
        diagnosis_code = "inconclusive"
        reasons.append("no diagnosis threshold triggered")

    # Severity mapping
    if diagnosis_code == "signal_negative_before_cost":
        severity = "structural"
    elif diagnosis_code == "fee_dominated":
        freq_in_range = 0.001 <= entry_frequency <= 0.02
        if avg_holding_period >= 20 and freq_in_range:
            severity = "structural"
            reasons.append(f"avg_holding_period={_fmt(avg_holding_period)} >= 20")
            reasons.append(f"0.001 <= entry_frequency={_fmt(entry_frequency)} <= 0.02")
        else:
            severity = "parametric"
            if avg_holding_period < 20:
                reasons.append(f"avg_holding_period={_fmt(avg_holding_period)} < 20")
            if entry_frequency < 0.001:
                reasons.append(f"entry_frequency={_fmt(entry_frequency)} < 0.001")
            elif entry_frequency > 0.02:
                reasons.append(f"entry_frequency={_fmt(entry_frequency)} > 0.02")
    elif diagnosis_code in {
        "no_trades",
        "no_fills_after_signal",
        "overtrading",
        "exit_too_short",
    }:
        severity = "parametric"
    else:
        severity = "inconclusive"

    control_mode = {
        "structural": "explore",
        "parametric": "repair",
        "inconclusive": "neutral",
    }[severity]
    structural_change_required = severity == "structural"

    # Verdict mapping (authoritative)
    can_pass = (
        net_pnl > 0
        and avg_holding_period >= 10
        and 0.0005 <= entry_frequency <= 0.02
        and diagnosis_code == "inconclusive"
    )
    if can_pass:
        verdict = "pass"
        reasons.append(f"net_pnl={_fmt(net_pnl)} > 0")
        reasons.append(f"avg_holding_period={_fmt(avg_holding_period)} >= 10")
        reasons.append(f"0.0005 <= entry_frequency={_fmt(entry_frequency)} <= 0.02")
        reasons.append("diagnosis_code=inconclusive")
    elif severity == "structural":
        verdict = "fail"
        reasons.append("severity=structural -> verdict=fail")
    else:
        verdict = "retry"
        if net_pnl <= 0:
            reasons.append(f"net_pnl={_fmt(net_pnl)} <= 0")
        if avg_holding_period < 10:
            reasons.append(f"avg_holding_period={_fmt(avg_holding_period)} < 10")
        if entry_frequency < 0.0005:
            reasons.append(f"entry_frequency={_fmt(entry_frequency)} < 0.0005")
        elif entry_frequency > 0.02:
            reasons.append(f"entry_frequency={_fmt(entry_frequency)} > 0.02")
        if diagnosis_code != "inconclusive":
            reasons.append(f"diagnosis_code={diagnosis_code} != inconclusive")

    return {
        "diagnosis_code": diagnosis_code,
        "severity": severity,
        "control_mode": control_mode,
        "structural_change_required": structural_change_required,
        "verdict": verdict,
        "controller_reasons": reasons,
    }

