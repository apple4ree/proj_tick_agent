"""
tests/test_config.py
--------------------
Tests for YAML config loader, merge, profile override, and env expansion.
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path

from utils.config import (
    _deep_merge,
    _expand_env,
    _load_yaml,
    load_config,
    resolve_paths,
    get_paths,
    get_generation,
    get_backtest,
    get_backtest_worker,
    get_workers,
)


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_flat_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}, "y": 10}
        override = {"x": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}, "y": 10}

    def test_override_replaces_non_dict(self):
        base = {"x": {"a": 1}}
        override = {"x": "replaced"}
        result = _deep_merge(base, override)
        assert result == {"x": "replaced"}

    def test_does_not_mutate_base(self):
        base = {"x": {"a": 1}}
        override = {"x": {"b": 2}}
        _deep_merge(base, override)
        assert base == {"x": {"a": 1}}


# ---------------------------------------------------------------------------
# Env expansion
# ---------------------------------------------------------------------------

class TestEnvExpansion:
    def test_simple_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_1", "hello")
        result = _expand_env({"key": "${TEST_VAR_1}"})
        assert result == {"key": "hello"}

    def test_env_with_default(self):
        result = _expand_env({"key": "${NONEXISTENT_VAR_XYZ:-fallback}"})
        assert result == {"key": "fallback"}

    def test_missing_env_no_default_untouched(self):
        result = _expand_env({"key": "${NONEXISTENT_VAR_XYZ}"})
        assert result == {"key": "${NONEXISTENT_VAR_XYZ}"}

    def test_nested_expansion(self, monkeypatch):
        monkeypatch.setenv("TEST_NESTED", "expanded")
        result = _expand_env({"a": {"b": [{"c": "${TEST_NESTED}"}]}})
        assert result["a"]["b"][0]["c"] == "expanded"

    def test_non_string_passthrough(self):
        result = _expand_env({"num": 42, "flag": True, "items": [1, 2]})
        assert result == {"num": 42, "flag": True, "items": [1, 2]}


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

class TestLoadYaml:
    def test_load_existing_file(self, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text("key: value\nnested:\n  a: 1\n")
        result = _load_yaml(p)
        assert result == {"key": "value", "nested": {"a": 1}}

    def test_load_missing_file(self, tmp_path):
        result = _load_yaml(tmp_path / "missing.yaml")
        assert result == {}

    def test_load_empty_file(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        result = _load_yaml(p)
        assert result == {}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

class TestPathResolution:
    def test_relative_paths_resolved(self):
        cfg = {"paths": {"jobs_dir": "jobs", "data_dir": "/abs/path"}}
        root = Path("/project")
        result = resolve_paths(cfg, root)
        assert result["paths"]["jobs_dir"] == "/project/jobs"
        assert result["paths"]["data_dir"] == "/abs/path"


# ---------------------------------------------------------------------------
# Profile override
# ---------------------------------------------------------------------------

class TestProfileOverride:
    def test_profile_merges_on_top(self, tmp_path):
        # Create minimal conf dir
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()
        (conf_dir / "app.yaml").write_text("app:\n  env: base\n  log_level: INFO\n")

        profiles_dir = conf_dir / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "test.yaml").write_text("app:\n  env: test\n  log_level: DEBUG\n")

        cfg = load_config(profile="test", conf_dir=conf_dir, resolve=False)
        assert cfg["app"]["env"] == "test"
        assert cfg["app"]["log_level"] == "DEBUG"

    def test_missing_profile_returns_base(self, tmp_path):
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()
        (conf_dir / "app.yaml").write_text("app:\n  env: base\n")

        cfg = load_config(profile="nonexistent", conf_dir=conf_dir, resolve=False)
        assert cfg["app"]["env"] == "base"


# ---------------------------------------------------------------------------
# Config file override
# ---------------------------------------------------------------------------

class TestConfigFileOverride:
    def test_explicit_config_merges_last(self, tmp_path):
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()
        (conf_dir / "app.yaml").write_text("app:\n  env: base\n")

        override = tmp_path / "override.yaml"
        override.write_text("app:\n  env: custom\n  extra: 123\n")

        cfg = load_config(
            config_path=override, conf_dir=conf_dir, resolve=False,
        )
        assert cfg["app"]["env"] == "custom"
        assert cfg["app"]["extra"] == 123


# ---------------------------------------------------------------------------
# Accessors with defaults
# ---------------------------------------------------------------------------

class TestAccessors:
    def test_get_paths_defaults(self):
        paths = get_paths({})
        assert "data_dir" in paths
        assert "jobs_dir" in paths
        assert "registry_dir" in paths

    def test_get_generation_defaults(self):
        gen = get_generation({})
        assert gen["backend"] == "template"
        assert gen["mode"] == "live"
        assert gen["latency_ms"] == 1.0

    def test_get_backtest_defaults(self):
        bt = get_backtest({})
        assert bt["initial_cash"] == 1e8
        assert bt["seed"] == 42
        assert "latencies_ms" not in bt  # moved to backtest_worker

    def test_get_backtest_worker_defaults(self):
        bw = get_backtest_worker({})
        assert bw["latencies_ms"] == [0.0, 50.0, 100.0, 500.0, 1000.0]
        assert bw["review_gate_required"] is True

    def test_get_workers_defaults(self):
        w = get_workers({})
        assert w["generation_poll_interval"] == 5.0
        assert w["once"] is False

    def test_accessors_preserve_overrides(self):
        cfg = {"generation": {"backend": "openai", "mode": "mock"}}
        gen = get_generation(cfg)
        assert gen["backend"] == "openai"
        assert gen["mode"] == "mock"
        assert gen["latency_ms"] == 1.0  # default still filled


# ---------------------------------------------------------------------------
# Full load from real conf dir
# ---------------------------------------------------------------------------

class TestRealConfLoad:
    def test_load_project_config(self):
        """Smoke test: load from the actual project conf/ directory."""
        cfg = load_config()
        assert "app" in cfg
        assert "paths" in cfg
        assert "generation" in cfg
        assert "backtest" in cfg
        assert "backtest_worker" in cfg
        assert "workers" in cfg

    def test_load_dev_profile(self):
        cfg = load_config(profile="dev")
        assert cfg["app"]["env"] == "dev"
        gen = get_generation(cfg)
        assert gen["mode"] == "mock"

    def test_load_smoke_profile(self):
        cfg = load_config(profile="smoke")
        bw = get_backtest_worker(cfg)
        assert len(bw["latencies_ms"]) == 2

    def test_load_prod_profile(self):
        cfg = load_config(profile="prod")
        gen = get_generation(cfg)
        assert gen["backend"] == "openai"


# ---------------------------------------------------------------------------
# Worker config injection
# ---------------------------------------------------------------------------

class TestWorkerConfigInjection:
    def test_worker_uses_config_paths(self, tmp_path):
        """Verify worker scripts can read config and wire up correct paths."""
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()
        (conf_dir / "paths.yaml").write_text(
            f"paths:\n"
            f"  jobs_dir: {tmp_path / 'custom_jobs'}\n"
            f"  registry_dir: {tmp_path / 'custom_strats'}\n"
            f"  traces_dir: {tmp_path / 'custom_traces'}\n"
            f"  data_dir: /tmp/data\n"
        )

        cfg = load_config(conf_dir=conf_dir, resolve=False)
        paths = get_paths(cfg)
        assert "custom_jobs" in paths["jobs_dir"]
        assert "custom_strats" in paths["registry_dir"]
        assert "custom_traces" in paths["traces_dir"]
