from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def test_phase4_benchmark_freeze_artifact_contract():
    path = PROJECT_ROOT / "outputs" / "benchmarks" / "phase4_benchmark_freeze.json"
    assert path.exists(), "phase4_benchmark_freeze.json must be generated"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "canonical_matrix" in payload
    assert "freeze_contracts" in payload
    assert "behavioral_freeze" in payload
    assert "regression_tolerance" in payload

    runs = payload["canonical_matrix"]["single_symbol_runs"]
    labels = {row.get("matrix_label") for row in runs}
    assert {"A", "B", "C", "D"}.issubset(labels)

    variants = payload["canonical_matrix"]["review_variants"]
    variant_names = {row.get("variant") for row in variants}
    assert {
        "static_only",
        "llm_review",
        "auto_repair",
        "feedback_aware_auto_repair",
    }.issubset(variant_names)

    contract = payload["freeze_contracts"]
    assert "summary_core_fields" in contract
    assert "realism_diagnostics_core_fields" in contract
    assert "review_pipeline_result_fields" in contract

    for row in runs:
        assert "run_dir" in row
        assert "plots" in row
        assert "all_required_present" in row["plots"]


def test_compare_phase4_baselines_detects_contract_drift(tmp_path: Path):
    baseline = {
        "freeze_contracts": {
            "summary_core_fields": ["a", "b"],
            "realism_diagnostics_core_fields": {"x": ["y"]},
            "review_pipeline_result_fields": ["k"],
        },
        "canonical_matrix": {
            "single_symbol_runs": [
                {
                    "matrix_label": "A",
                    "signal_count": 10,
                    "parent_order_count": 5,
                    "child_order_count": 20,
                    "children_per_parent": 4,
                    "n_fills": 3,
                    "cancel_rate": 0.5,
                    "net_pnl": -10,
                    "total_commission": 1,
                    "total_slippage": 1,
                    "total_impact": 1,
                    "loop_s": 1,
                    "total_s": 2,
                }
            ]
        },
    }
    candidate = copy.deepcopy(baseline)
    candidate["freeze_contracts"]["summary_core_fields"] = ["a", "c"]

    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    script = PROJECT_ROOT / "scripts" / "internal" / "adhoc" / "compare_phase4_baselines.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert "summary_core_fields drift" in proc.stdout
