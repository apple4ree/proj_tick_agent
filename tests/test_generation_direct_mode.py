"""Tests for generate_strategy.py --direct mode.

Verifies that direct generation respects config's canonical path keys
(registry_dir, traces_dir) and saves artifacts to the correct locations.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _run_direct_with_config(tmp_path: Path, extra_paths: dict | None = None) -> str:
    """Run _run_direct with a synthetic config and return the GENERATED_SPEC path.

    Patches sys.argv and captures stdout to extract the GENERATED_SPEC line.
    """
    import importlib
    # Ensure generate_strategy module is importable from scripts/
    scripts_dir = PROJECT_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    # Build config with custom paths
    registry_dir = str(extra_paths.get("registry_dir", tmp_path / "strategies"))
    traces_dir = str(extra_paths.get("traces_dir", tmp_path / "traces"))
    outputs_dir = str(extra_paths.get("outputs_dir", tmp_path / "outputs"))

    cfg = {
        "app": {"log_level": "WARNING"},
        "paths": {
            "data_dir": "/tmp/fake_data",
            "registry_dir": registry_dir,
            "traces_dir": traces_dir,
            "outputs_dir": outputs_dir,
            "jobs_dir": str(tmp_path / "jobs"),
            "logs_dir": str(tmp_path / "logs"),
            "replays_dir": str(tmp_path / "replays"),
        },
        "generation": {
            "spec_format": "v2",
            "backend": "template",
            "mode": "live",
            "latency_ms": 1.0,
            "auto_approve": False,
            "n_ideas": 3,
            "idea_index": 0,
        },
    }

    from utils.config import get_paths, get_generation

    # Import the _run_direct function
    from generate_strategy import _run_direct
    import argparse

    args = argparse.Namespace(
        goal="order imbalance alpha",
        backend=None,
        config=None,
        profile=None,
        direct=True,
    )

    # Capture stdout
    import io
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        _run_direct(args, cfg)

    output = captured.getvalue()
    # Extract GENERATED_SPEC line
    for line in output.strip().splitlines():
        if line.startswith("GENERATED_SPEC="):
            return line.split("=", 1)[1]

    raise AssertionError(f"GENERATED_SPEC not found in output:\n{output}")


@pytest.mark.v2_core
class TestDirectModeCanonicalPaths:

    def test_default_registry_dir(self, tmp_path: Path):
        """Default config saves spec under paths.registry_dir."""
        registry_dir = tmp_path / "strategies"
        spec_path_str = _run_direct_with_config(tmp_path, {
            "registry_dir": str(registry_dir),
            "traces_dir": str(tmp_path / "traces"),
        })
        spec_path = Path(spec_path_str)

        # Spec must be under registry_dir
        assert spec_path.parent == registry_dir
        assert spec_path.exists()
        assert spec_path.suffix == ".json"

        # Meta file must also be in registry_dir
        meta_path = spec_path.with_suffix("").with_suffix(".meta.json")
        # StrategyRegistry naming: <name>_v<version>.meta.json
        stem = spec_path.stem  # e.g. <strategy_name>_v2.0
        meta_path = registry_dir / f"{stem}.meta.json"
        assert meta_path.exists()

    def test_custom_registry_dir_override(self, tmp_path: Path):
        """Custom registry_dir is respected — spec is NOT in default strategies/."""
        custom_registry = tmp_path / "my_custom_registry"
        spec_path_str = _run_direct_with_config(tmp_path, {
            "registry_dir": str(custom_registry),
            "traces_dir": str(tmp_path / "traces"),
        })
        spec_path = Path(spec_path_str)

        assert spec_path.parent == custom_registry
        assert spec_path.exists()

        # Should NOT be in the default strategies/ dir
        default_dir = PROJECT_ROOT / "strategies"
        assert not spec_path.is_relative_to(default_dir) or custom_registry == default_dir

    def test_trace_saved_to_traces_dir(self, tmp_path: Path):
        """Trace file is saved under paths.traces_dir, not under outputs/strategy_traces."""
        custom_traces = tmp_path / "my_traces"
        _run_direct_with_config(tmp_path, {
            "registry_dir": str(tmp_path / "reg"),
            "traces_dir": str(custom_traces),
        })

        # At least one trace file should exist
        trace_files = list(custom_traces.glob("*_trace.json"))
        assert len(trace_files) == 1
        # Validate it's valid JSON
        data = json.loads(trace_files[0].read_text())
        assert "generation_outcome" in data
        assert data["input"]["backtest_environment"]["resample"] == "1s"
        assert data["input"]["backtest_environment"]["latency"]["order_submit_ms"] == 0.3

    def test_spec_content_is_valid(self, tmp_path: Path):
        """Generated spec is a valid canonical v2 StrategySpec JSON."""
        from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
        from strategy_block.strategy_registry.registry import _detect_spec_format

        registry_dir = tmp_path / "reg"
        spec_path_str = _run_direct_with_config(tmp_path, {
            "registry_dir": str(registry_dir),
            "traces_dir": str(tmp_path / "traces"),
        })
        spec_path = Path(spec_path_str)
        fmt = _detect_spec_format(spec_path)
        assert fmt == "v2"

        spec = StrategySpecV2.load(spec_path)
        assert spec.name
        assert spec.version
        assert len(spec.entry_policies) >= 1
