#!/usr/bin/env python
"""Compare Phase 4 benchmark freeze artifacts.

By default compares the canonical freeze artifact against another JSON.
- Contract field presence: exact match required.
- Numeric metrics: relative tolerance check (default 10%).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _check_exact_contract(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    b_contract = baseline.get("freeze_contracts", {})
    c_contract = candidate.get("freeze_contracts", {})
    if b_contract.get("summary_core_fields") != c_contract.get("summary_core_fields"):
        issues.append("summary_core_fields drift")
    if b_contract.get("realism_diagnostics_core_fields") != c_contract.get("realism_diagnostics_core_fields"):
        issues.append("realism_diagnostics_core_fields drift")
    if b_contract.get("review_pipeline_result_fields") != c_contract.get("review_pipeline_result_fields"):
        issues.append("review_pipeline_result_fields drift")
    return issues


def _index_runs(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if key in row and row[key] is not None:
            out[str(row[key])] = row
    return out


def _check_numeric_drift(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    tolerance_ratio: float,
) -> list[str]:
    issues: list[str] = []
    metrics = [
        "signal_count",
        "parent_order_count",
        "child_order_count",
        "children_per_parent",
        "n_fills",
        "cancel_rate",
        "net_pnl",
        "total_commission",
        "total_slippage",
        "total_impact",
        "loop_s",
        "total_s",
    ]
    b_rows = baseline.get("canonical_matrix", {}).get("single_symbol_runs", [])
    c_rows = candidate.get("canonical_matrix", {}).get("single_symbol_runs", [])
    b_ix = _index_runs(b_rows, "matrix_label")
    c_ix = _index_runs(c_rows, "matrix_label")

    for label, b_row in b_ix.items():
        c_row = c_ix.get(label)
        if c_row is None:
            issues.append(f"missing matrix label in candidate: {label}")
            continue
        for metric in metrics:
            b_val = _num(b_row.get(metric))
            c_val = _num(c_row.get(metric))
            if b_val is None or c_val is None:
                continue
            denom = max(1.0, abs(b_val))
            rel = abs(c_val - b_val) / denom
            if rel > tolerance_ratio:
                issues.append(
                    f"{label}.{metric} drift too large: baseline={b_val:.6g}, candidate={c_val:.6g}, rel={rel:.3f}"
                )
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare phase4 benchmark freeze artifacts")
    parser.add_argument(
        "--baseline",
        default="outputs/benchmarks/phase4_benchmark_freeze.json",
        help="Baseline freeze JSON",
    )
    parser.add_argument(
        "--candidate",
        required=True,
        help="Candidate freeze JSON",
    )
    parser.add_argument(
        "--tolerance-ratio",
        type=float,
        default=0.10,
        help="Relative tolerance for numeric drift checks",
    )
    args = parser.parse_args()

    baseline = _load(Path(args.baseline))
    candidate = _load(Path(args.candidate))

    issues = []
    issues.extend(_check_exact_contract(baseline, candidate))
    issues.extend(_check_numeric_drift(baseline, candidate, args.tolerance_ratio))

    if issues:
        print("PHASE4_BASELINE_COMPARE=FAILED")
        for issue in issues:
            print(f"- {issue}")
        raise SystemExit(1)

    print("PHASE4_BASELINE_COMPARE=PASSED")


if __name__ == "__main__":
    main()
