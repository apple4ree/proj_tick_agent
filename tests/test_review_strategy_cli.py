from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_review_module():
    script_path = PROJECT_ROOT / "scripts" / "review_strategy.py"
    spec = importlib.util.spec_from_file_location("review_strategy_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_valid_spec(path: Path) -> Path:
    spec_json = {
        "spec_format": "v2",
        "name": "cli_valid",
        "version": "2.0",
        "entry_policies": [
            {
                "name": "long_entry",
                "side": "long",
                "trigger": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.1},
                "strength": {"type": "const", "value": 0.5},
            },
        ],
        "exit_policies": [
            {
                "name": "exits",
                "rules": [
                    {
                        "name": "spread_exit",
                        "priority": 1,
                        "condition": {"type": "comparison", "feature": "spread_bps", "op": ">", "threshold": 40.0},
                        "action": {"type": "close_all"},
                    },
                ],
            },
        ],
        "risk_policy": {
            "max_position": 100,
            "inventory_cap": 200,
            "position_sizing": {"mode": "fixed", "base_size": 10, "max_size": 50},
        },
    }
    path.write_text(json.dumps(spec_json), encoding="utf-8")
    return path


def _write_invalid_spec_no_close_all(path: Path) -> Path:
    spec_json = {
        "spec_format": "v2",
        "name": "cli_invalid",
        "version": "2.0",
        "entry_policies": [
            {
                "name": "long_entry",
                "side": "long",
                "trigger": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.1},
                "strength": {"type": "const", "value": 0.5},
            },
        ],
        "exit_policies": [
            {
                "name": "exits",
                "rules": [
                    {
                        "name": "partial_only",
                        "priority": 1,
                        "condition": {"type": "comparison", "feature": "spread_bps", "op": ">", "threshold": 20.0},
                        "action": {"type": "reduce_position", "reduce_fraction": 0.5},
                    },
                ],
            },
        ],
        "risk_policy": {
            "max_position": 100,
            "inventory_cap": 200,
            "position_sizing": {"mode": "fixed", "base_size": 10, "max_size": 50},
        },
    }
    path.write_text(json.dumps(spec_json), encoding="utf-8")
    return path


def test_cli_static_mode_backward_compatible(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path):
    mod = _load_review_module()
    spec_path = _write_valid_spec(tmp_path / "spec_valid.json")

    monkeypatch.setattr(sys, "argv", ["review_strategy.py", str(spec_path)])
    mod.main()

    out = capsys.readouterr().out
    assert "REVIEW_STATUS=PASSED" in out
    assert "LLM_REVIEW_RUN=false" in out
    assert "REPAIR_APPLIED=false" in out



def test_cli_llm_review_mode_auto_saves_artifacts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path):
    mod = _load_review_module()
    spec_path = _write_valid_spec(tmp_path / "spec_valid.json")
    out_dir = spec_path.parent / f"{spec_path.stem}_review_artifacts"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_strategy.py",
            str(spec_path),
            "--mode",
            "llm-review",
        ],
    )
    mod.main()

    out = capsys.readouterr().out
    assert "REVIEW_STATUS=PASSED" in out
    assert "LLM_REVIEW_RUN=true" in out
    assert (out_dir / "static_review.json").exists()
    assert (out_dir / "llm_review.json").exists()
    assert (out_dir / "final_static_review.json").exists()



def test_cli_auto_repair_mode_auto_saves_artifacts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path):
    mod = _load_review_module()
    spec_path = _write_invalid_spec_no_close_all(tmp_path / "spec_invalid.json")
    out_dir = spec_path.parent / f"{spec_path.stem}_review_artifacts"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_strategy.py",
            str(spec_path),
            "--mode",
            "auto-repair",
        ],
    )
    mod.main()

    out = capsys.readouterr().out
    assert "REPAIR_APPLIED=true" in out
    assert "REVIEW_STATUS=PASSED" in out
    assert (out_dir / "static_review.json").exists()
    assert (out_dir / "llm_review.json").exists()
    assert (out_dir / "repair_plan.json").exists()
    assert (out_dir / "repaired_spec.json").exists()
    assert (out_dir / "final_static_review.json").exists()

    final_review = json.loads((out_dir / "final_static_review.json").read_text(encoding="utf-8"))
    assert final_review["passed"] is True



def _write_env_sensitive_spec(path: Path) -> Path:
    spec_json = {
        "spec_format": "v2",
        "name": "cli_env_sensitive",
        "version": "2.0",
        "entry_policies": [
            {
                "name": "long_entry",
                "side": "long",
                "trigger": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.1},
                "strength": {"type": "const", "value": 0.5},
            },
        ],
        "exit_policies": [
            {
                "name": "exits",
                "rules": [
                    {
                        "name": "stop_loss",
                        "priority": 1,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "unrealized_pnl_bps"},
                            "op": "<=",
                            "threshold": -25.0,
                        },
                        "action": {"type": "close_all"},
                    },
                    {
                        "name": "time_exit",
                        "priority": 2,
                        "condition": {
                            "type": "comparison",
                            "left": {"type": "position_attr", "name": "holding_ticks"},
                            "op": ">=",
                            "threshold": 10.0,
                        },
                        "action": {"type": "close_all"},
                    },
                ],
            },
        ],
        "execution_policy": {
            "placement_mode": "passive_join",
            "cancel_after_ticks": 5,
            "max_reprices": 1,
        },
        "risk_policy": {
            "max_position": 100,
            "inventory_cap": 200,
            "position_sizing": {"mode": "fixed", "base_size": 10, "max_size": 50},
        },
    }
    path.write_text(json.dumps(spec_json), encoding="utf-8")
    return path


def test_cli_static_mode_passes_env_context_to_reviewer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path):
    mod = _load_review_module()
    spec_path = _write_env_sensitive_spec(tmp_path / "spec_env_sensitive.json")

    cfg_path = tmp_path / "review_env.yaml"
    cfg_path.write_text(
        """
backtest:
  resample: 500ms
  market_data_delay_ms: 0.0
  decision_compute_ms: 0.0
  latency:
    order_submit_ms: 50.0
    cancel_ms: 50.0
""".strip() + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_strategy.py",
            str(spec_path),
            "--mode",
            "static",
            "--config",
            str(cfg_path),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert int(exc.value.code) == 1

    out = capsys.readouterr().out
    assert "REVIEW_STATUS=FAILED" in out
    assert "churn_risk_high" in out
