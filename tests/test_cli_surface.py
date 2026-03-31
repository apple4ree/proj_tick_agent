from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_help(cmd: list[str]) -> str:
    proc = _run_cmd(cmd)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout + proc.stderr


def test_generate_strategy_help_surface():
    out = _run_help([sys.executable, "scripts/generate_strategy.py", "--help"])

    assert "--goal" in out
    assert "--backend" in out
    assert "--config" in out
    assert "--profile" in out
    assert "--direct" in out

    assert "--spec-format" not in out
    assert "--mode" not in out
    assert "--model" not in out
    assert "--auto-approve" not in out


def test_review_strategy_help_surface():
    out = _run_help([sys.executable, "scripts/review_strategy.py", "--help"])

    assert "spec_path" in out
    assert "--mode" in out
    assert "--config" in out
    assert "--profile" in out

    assert "--backend" not in out
    assert "--client-mode" not in out
    assert "--model" not in out
    assert "--save-artifacts" not in out
    assert "--output-dir" not in out


def test_wrapper_help_surface():
    out = _run_help(["bash", "scripts/run_generate_review_backtest.sh", "--help"])

    assert "--goal" in out
    assert "--symbol" in out
    assert "--universe" in out
    assert "--start-date" in out
    assert "--end-date" in out
    assert "--backend" in out
    assert "--review-mode" in out
    assert "--config" in out
    assert "--profile" in out

    assert "--mode" not in out
    assert "--auto-approve" not in out


def test_backtest_help_regression_kept():
    out = _run_help([sys.executable, "scripts/backtest.py", "--help"])
    assert "--spec" in out
    assert "--symbol" in out
    assert "--start-date" in out
    assert "--end-date" in out
    assert "--config" in out
    assert "--profile" in out


def test_backtest_universe_help_regression_kept():
    out = _run_help([sys.executable, "scripts/backtest_strategy_universe.py", "--help"])
    assert "--spec" in out
    assert "--data-dir" in out
    assert "--start-date" in out
    assert "--end-date" in out
    assert "--config" in out
    assert "--profile" in out


def test_evaluate_walk_forward_help_surface():
    out = _run_help([sys.executable, "scripts/evaluate_walk_forward.py", "--help"])
    assert "--spec" in out
    assert "--symbol" in out
    assert "--universe" in out
    assert "--start-date" in out
    assert "--end-date" in out
    assert "--profile" in out
    assert "--selection-config" in out
    assert "--trial-id" in out


def test_promote_candidate_help_surface():
    out = _run_help([sys.executable, "scripts/promote_candidate.py", "--help"])
    assert "--spec" in out
    assert "--walk-forward-report" in out
    assert "--trial-id" in out
    assert "--promotion-config" in out
    assert "--profile" in out
    assert "--out-dir" in out


def test_promote_candidate_key_value_output_on_fail(tmp_path: Path):
    report_path = tmp_path / "wf_report_fail.json"
    report_path.write_text(
        """
{
  "mode": "single",
  "symbol": "005930",
  "decision": {
    "passed": false,
    "aggregate_score": -0.5,
    "reasons": ["walk_forward_not_passed"],
    "metadata": {
      "n_valid_windows": 2,
      "n_pass_windows": 0,
      "churn_heavy_share": 0.2,
      "cost_dominated_share": 0.2,
      "adverse_selection_dominated_share": 0.1
    }
  },
  "window_results": [
    {"metadata": {"flags": {"queue_ineffective": false}}},
    {"metadata": {"flags": {"queue_ineffective": false}}}
  ]
}
        """.strip(),
        encoding="utf-8",
    )

    proc = _run_cmd(
        [
            sys.executable,
            "scripts/promote_candidate.py",
            "--spec",
            str(PROJECT_ROOT / "strategies" / "openai_imbalance_momentum_plan_v2.0.json"),
            "--walk-forward-report",
            str(report_path),
        ]
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0
    assert "PROMOTION_STATUS=FAILED" in out
    assert "PROMOTION_BUNDLE=" in out
    assert "PROMOTION_REASONS=" in out


def test_promote_candidate_key_value_output_on_pass(tmp_path: Path):
    report_path = tmp_path / "wf_report_pass.json"
    report_path.write_text(
        """
{
  "mode": "single",
  "symbol": "005930",
  "decision": {
    "passed": true,
    "aggregate_score": 0.2,
    "reasons": [],
    "metadata": {
      "n_valid_windows": 2,
      "n_pass_windows": 2,
      "churn_heavy_share": 0.1,
      "cost_dominated_share": 0.1,
      "adverse_selection_dominated_share": 0.1
    }
  },
  "window_results": [
    {"metadata": {"flags": {"queue_ineffective": false}}},
    {"metadata": {"flags": {"queue_ineffective": false}}}
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    promotion_cfg_path = tmp_path / "promotion_override.yaml"
    promotion_cfg_path.write_text(
        """
promotion:
  gate:
    require_family_id: false
        """.strip(),
        encoding="utf-8",
    )
    bundle_out = tmp_path / "bundle_out"

    proc = _run_cmd(
        [
            sys.executable,
            "scripts/promote_candidate.py",
            "--spec",
            str(PROJECT_ROOT / "strategies" / "openai_imbalance_momentum_plan_v2.0.json"),
            "--walk-forward-report",
            str(report_path),
            "--promotion-config",
            str(promotion_cfg_path),
            "--out-dir",
            str(bundle_out),
        ]
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "PROMOTION_STATUS=PASSED" in out
    assert "PROMOTION_BUNDLE=" in out
    assert "PROMOTION_REASONS=" in out
    assert (bundle_out / "contract.json").exists()
