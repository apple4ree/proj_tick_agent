from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_help(cmd: list[str]) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
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
