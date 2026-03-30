"""
utils/config.py
---------------
YAML-based configuration loader with profile override and env-var expansion.

Usage
-----
    from utils.config import load_config

    # Load with defaults
    cfg = load_config()

    # Load with profile override
    cfg = load_config(profile="dev")

    # Load from explicit path
    cfg = load_config(config_path="conf/generation.yaml")
"""
from __future__ import annotations

import os
import copy
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Project root: two levels up from src/utils/config.py
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONF_DIR = _PROJECT_ROOT / "conf"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (returns new dict)."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _expand_env(data: Any) -> Any:
    """Recursively expand ``${ENV_VAR}`` and ``${ENV_VAR:-default}`` in
    string values.  Non-string leaves are returned unchanged."""
    if isinstance(data, str):
        return _expand_env_str(data)
    if isinstance(data, dict):
        return {k: _expand_env(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_env(item) for item in data]
    return data


def _expand_env_str(s: str) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` patterns in a string."""
    import re
    def _repl(m: re.Match) -> str:
        var = m.group(1)
        default: str | None = m.group(3)  # group 3 is after :-
        value = os.environ.get(var)
        if value is not None:
            return value
        if default is not None:
            return default
        return m.group(0)  # leave as-is if no default and not in env
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-(.*?))?\}", _repl, s)


def _load_yaml(path: Path) -> dict:
    """Load a single YAML file, returning empty dict if missing."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def resolve_paths(cfg: dict, root: Path | None = None) -> dict:
    """Resolve relative paths in the ``paths`` section to absolute paths."""
    root = root or _PROJECT_ROOT
    paths = cfg.get("paths", {})
    for key, value in paths.items():
        if isinstance(value, str) and not os.path.isabs(value):
            paths[key] = str(root / value)
    return cfg


def load_config(
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
    conf_dir: str | Path | None = None,
    resolve: bool = True,
) -> dict[str, Any]:
    """Load and merge configuration from YAML files.

    Merge order (later wins):
        1. ``conf/app.yaml``            — app-level defaults
        2. ``conf/paths.yaml``          — path defaults
        3. ``conf/generation.yaml``     — generation plane
        4. ``conf/backtest_base.yaml``  — backtest core defaults
        5. ``conf/backtest_worker.yaml``— worker/orchestration backtest settings
        6. ``conf/workers.yaml``        — worker behaviour
        7. ``conf/profiles/<profile>.yaml`` — environment override
        8. *config_path* if given       — explicit override file

    Parameters
    ----------
    config_path : path, optional
        Explicit YAML file to merge on top of everything else.
    profile : str, optional
        Profile name (``dev``, ``smoke``, ``prod``).
    conf_dir : path, optional
        Override config directory (default ``<project>/conf``).
    resolve : bool
        If True, resolve relative paths in ``paths`` section.
    """
    cdir = Path(conf_dir) if conf_dir else _CONF_DIR

    # Layer 1–5: base config files
    merged: dict[str, Any] = {}
    for name in ("app", "paths", "generation", "backtest_base", "backtest_worker", "workers"):
        merged = _deep_merge(merged, _load_yaml(cdir / f"{name}.yaml"))

    # Layer 6: profile override
    if profile:
        profile_path = cdir / "profiles" / f"{profile}.yaml"
        profile_data = _load_yaml(profile_path)
        if profile_data:
            merged = _deep_merge(merged, profile_data)
            logger.debug("Applied profile: %s", profile)
        else:
            logger.warning("Profile file not found: %s", profile_path)

    # Layer 7: explicit config file
    if config_path:
        merged = _deep_merge(merged, _load_yaml(Path(config_path)))

    # Env-var expansion
    merged = _expand_env(merged)

    # Path resolution
    if resolve:
        merged = resolve_paths(merged, _PROJECT_ROOT)

    return merged


# -- Convenience accessors ---------------------------------------------------

def get_paths(cfg: dict) -> dict[str, str]:
    """Extract the ``paths`` section with sensible defaults."""
    defaults = {
        "data_dir": "/home/dgu/tick/open-trading-api/data/realtime/H0STASP0",
        "registry_dir": "strategies",
        "jobs_dir": "jobs",
        "outputs_dir": "outputs",
        "traces_dir": "outputs/strategy_traces",
        "replays_dir": "outputs/replays",
        "logs_dir": "logs",
    }
    paths = cfg.get("paths", {})
    for k, v in defaults.items():
        paths.setdefault(k, v)
    return paths


def get_generation(cfg: dict) -> dict[str, Any]:
    """Extract the ``generation`` section with sensible defaults."""
    defaults = {
        "spec_format": "v2",
        "backend": "template",
        "mode": "live",
        "latency_ms": 1.0,
        "auto_approve": False,
        "n_ideas": 3,
        "idea_index": 0,
        "openai_model": "gpt-4o",
        "static_review_required": True,
        "allow_template_fallback": True,
        "allow_heuristic_fallback": True,
        "fail_on_fallback": False,
    }
    gen = cfg.get("generation", {})
    for k, v in defaults.items():
        gen.setdefault(k, v)
    return gen


def _resample_to_canonical_tick_ms(resample: Any) -> float:
    """Convert a supported resample string to canonical tick milliseconds."""
    value = str(resample or "1s").strip().lower()
    if value.endswith("ms"):
        return float(value[:-2])
    if value.endswith("s"):
        return float(value[:-1]) * 1000.0
    raise ValueError(f"Unsupported resample format: {resample!r}")


def _fmt_constraint_value(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.3f}".rstrip("0").rstrip(".")


def build_backtest_constraint_summary(backtest_environment: dict[str, Any] | None) -> str:
    """Render canonical, compact backtest constraint summary for LLM prompts."""
    if not backtest_environment:
        return (
            "- Backtest constraint summary: not provided (legacy latency hint only).\n"
            "  - Constraint-aware generation/review is degraded without environment context."
        )

    latency = dict(backtest_environment.get("latency") or {})
    queue = dict(backtest_environment.get("queue") or {})
    semantics = dict(backtest_environment.get("semantics") or {})

    replace_model = str(semantics.get("replace_model", "unknown"))
    if replace_model == "minimal_immediate":
        replace_note = "replace is minimal immediate, not staged venue replace"
    else:
        replace_note = f"replace_model={replace_model}"

    lines = [
        "- Backtest constraint summary (canonical):",
        f"  - Time/cadence: resample={backtest_environment.get('resample', 'unknown')}, canonical_tick_interval_ms={_fmt_constraint_value(backtest_environment.get('canonical_tick_interval_ms'))} (tick = resample step)",
        f"  - Observation/decision delay: market_data_delay_ms={_fmt_constraint_value(backtest_environment.get('market_data_delay_ms'))}, decision_compute_ms={_fmt_constraint_value(backtest_environment.get('decision_compute_ms'))}, effective_delay_ms={_fmt_constraint_value(backtest_environment.get('effective_delay_ms'))}",
        f"  - Venue latency: order_submit_ms={_fmt_constraint_value(latency.get('order_submit_ms'))}, order_ack_ms={_fmt_constraint_value(latency.get('order_ack_ms'))}, cancel_ms={_fmt_constraint_value(latency.get('cancel_ms'))}, order_ack_used_for_fill_gating={bool(latency.get('order_ack_used_for_fill_gating', False))}",
        f"  - Queue semantics: queue_model={queue.get('queue_model', 'unknown')}, queue_position_assumption={_fmt_constraint_value(queue.get('queue_position_assumption'))}",
        "  - passive fills require queue waiting",
        "  - repricing resets queue position",
        f"  - Replace semantics: {replace_note}",
        "  - submit/cancel latency compounds churn cost under repeated cancel/repost loops",
        "  - short-horizon strategies are more vulnerable to these frictions",
        "  - low-churn execution is preferred under queue and latency friction",
    ]

    return "\n".join(lines)


def build_backtest_environment_context(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build canonical backtest-environment context for generation/review agents."""
    from evaluation_orchestration.layer7_validation.backtest_config import BacktestConfig

    bt = get_backtest(cfg)
    resolved = BacktestConfig.from_dict({
        "symbol": "__generation__",
        "start_date": "19700101",
        "end_date": "19700101",
        **bt,
    })
    canonical_tick_ms = _resample_to_canonical_tick_ms(bt.get("resample", "1s"))
    effective_delay_ms = float(resolved.market_data_delay_ms + resolved.decision_compute_ms)

    return {
        "resample": str(bt.get("resample", "1s")),
        "canonical_tick_interval_ms": float(canonical_tick_ms),
        "market_data_delay_ms": float(resolved.market_data_delay_ms),
        "decision_compute_ms": float(resolved.decision_compute_ms),
        "effective_delay_ms": effective_delay_ms,
        "latency": {
            "order_submit_ms": float(resolved.latency.order_submit_ms or 0.0),
            "order_ack_ms": float(resolved.latency.order_ack_ms or 0.0),
            "cancel_ms": float(resolved.latency.cancel_ms or 0.0),
            "order_ack_used_for_fill_gating": False,
            "latency_alias_applied": bool(getattr(resolved, "_latency_alias_applied", False)),
            "profile": str(resolved.latency.profile),
        },
        "queue": {
            "queue_model": str(resolved.exchange.queue_model),
            "queue_position_assumption": float(resolved.exchange.queue_position_assumption),
        },
        "semantics": {
            "tick_is_resample_step": True,
            "submit_latency_gating": True,
            "cancel_latency_gating": True,
            "replace_model": "minimal_immediate",
        },
    }


def get_backtest(cfg: dict) -> dict[str, Any]:
    """Extract the ``backtest`` section with sensible defaults.

    Core defaults shared by all consumers (PipelineRunner, scripts, workers).
    For worker/orchestration-specific settings, use :func:`get_backtest_worker`.
    """
    defaults = {
        "initial_cash": 1e8,
        "seed": 42,
        "resample": "1s",
        "fee_model": "krx",
        "impact_model": "linear",
        "slicing_algo": "TWAP",
        "placement_style": "spread_adaptive",
        "compute_attribution": True,
    }
    bt = cfg.get("backtest", {})
    for k, v in defaults.items():
        bt.setdefault(k, v)
    return bt


def get_backtest_worker(cfg: dict) -> dict[str, Any]:
    """Extract the ``backtest_worker`` section with sensible defaults.

    Worker/orchestration-specific settings: latency sweep, execution gate, etc.
    """
    defaults = {
        "latencies_ms": [0.0, 50.0, 100.0, 500.0, 1000.0],
        "review_gate_required": True,
    }
    bw = cfg.get("backtest_worker", {})
    for k, v in defaults.items():
        bw.setdefault(k, v)
    return bw


def get_workers(cfg: dict) -> dict[str, Any]:
    """Extract the ``workers`` section with sensible defaults."""
    defaults = {
        "generation_poll_interval": 5.0,
        "backtest_poll_interval": 5.0,
        "once": False,
    }
    w = cfg.get("workers", {})
    for k, v in defaults.items():
        w.setdefault(k, v)
    return w
