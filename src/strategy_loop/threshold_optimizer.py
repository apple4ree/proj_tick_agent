"""
strategy_loop/threshold_optimizer.py
---------------------------------------
Optimize UPPER_CASE constants in generated code with Optuna.

Objective is based on real backtest summary metrics per trial.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import optuna
    from data.layer0_data.market_state import MarketState
    from evaluation_orchestration.layer7_validation.backtest_config import BacktestConfig

from strategy_loop.distribution_filter import MAX_ENTRY_FREQ, MIN_ENTRY_FREQ

logger = logging.getLogger(__name__)

# Objective penalty for invalid / errored trials
_FREQ_PENALTY: float = -1e6

# Entry frequency range (must match distribution_filter defaults)
_MIN_FREQ: float = MIN_ENTRY_FREQ
_MAX_FREQ: float = MAX_ENTRY_FREQ

# Defaults for real backtest objective
_DEFAULT_STAGE_PREFIXES: tuple[float, ...] = (0.2, 0.5, 1.0)
_DEFAULT_LAMBDA_MDD: float = 1.0

# Fixed Optuna MedianPruner settings
_PRUNER_STARTUP_TRIALS: int = 5
_PRUNER_WARMUP_STEPS: int = 1
_PRUNER_INTERVAL_STEPS: int = 1

# UPPER_CASE constant search ranges
# (regex_pattern, (lo, hi, log, is_int))
_CODE_CONST_RANGES: list[tuple[str, tuple]] = [
    (r"HOLDING.?TICKS",                (5.0,   120.0, False, True)),
    (r"IMBALANCE|OI_|_OI",             (-0.9,    0.9, False, False)),
    (r"EMA|DELTA",                     (-0.9,    0.9, False, False)),
    (r"SPREAD.*BPS|BPS.*SPREAD",       (1.0,   200.0, False, False)),
    (r"IMPACT.*BPS|BPS.*IMPACT",       (0.5,    50.0, False, False)),
    (r"VOLUME.*SURPRISE|SURPRISE",     (-2.0,    5.0, False, False)),
    (r"MULTIPLIER|RATIO|FACTOR",       (0.5,    10.0, False, False)),
]
_DEFAULT_CODE_RANGE: tuple = (-5.0, 5.0, False, False)

# UPPER_CASE module-level numeric assignment pattern
_UPPER_CONST_RE = re.compile(
    r"^([A-Z][A-Z0-9_]+)\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?:#.*)?$",
    re.MULTILINE,
)


@dataclass
class CodeOptimizationResult:
    best_code: str
    best_score: float
    n_trials_run: int
    entry_frequency: float
    best_net_return_bps: float
    best_max_drawdown: float
    n_trials_pruned: int


@dataclass
class _StageEvaluation:
    score: float
    net_return_bps: float
    max_drawdown: float
    entry_frequency: float
    is_valid: bool


def extract_code_params(code: str) -> dict[str, tuple]:
    """Extract UPPER_CASE numeric constants from code.

    Returns:
        {const_name: (lo, hi, log, is_int, current_value)}
    """
    params: dict[str, tuple] = {}
    for m in _UPPER_CONST_RE.finditer(code):
        name = m.group(1)
        current_val = float(m.group(2))
        lo, hi, log, is_int = _DEFAULT_CODE_RANGE
        for pattern, range_spec in _CODE_CONST_RANGES:
            if re.search(pattern, name, re.IGNORECASE):
                lo, hi, log, is_int = range_spec
                break
        params[name] = (lo, hi, log, is_int, current_val)
    return params


def inject_code_params(code: str, values: dict[str, float | int]) -> str:
    """Return a new code string with UPPER_CASE constants replaced."""

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            return m.group(0)
        val = values[name]

        # Keep original inline comment
        original_line = m.group(0)
        comment = ""
        if "#" in original_line:
            comment = "  " + original_line[original_line.index("#"):]

        if isinstance(val, int) or (isinstance(val, float) and val == int(val)):
            return f"{name} = {int(val)}{comment}"
        return f"{name} = {val:.6g}{comment}"

    return _UPPER_CONST_RE.sub(_replace, code)


def _normalize_stage_prefixes(stage_prefixes: tuple[float, ...] | list[float] | None) -> tuple[float, ...]:
    prefixes = stage_prefixes or _DEFAULT_STAGE_PREFIXES
    normalized: list[float] = []
    for raw in prefixes:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0.0:
            continue
        normalized.append(min(value, 1.0))

    if not normalized:
        normalized = list(_DEFAULT_STAGE_PREFIXES)

    normalized.sort()
    if normalized[-1] < 1.0:
        normalized.append(1.0)
    return tuple(normalized)


def _build_stage_state_slices(
    states: "list[MarketState]",
    stage_prefixes: tuple[float, ...] | list[float] | None,
) -> list["list[MarketState]"]:
    if not states:
        return []

    n_states = len(states)
    slices: list[list[MarketState]] = []
    for prefix in _normalize_stage_prefixes(stage_prefixes):
        size = max(1, int(math.ceil(n_states * prefix)))
        size = min(size, n_states)
        slices.append(states[:size])
    return slices


def _score_from_backtest_summary(
    summary: dict[str, Any],
    *,
    initial_cash: float,
    lambda_mdd: float,
) -> tuple[float, float, float]:
    if initial_cash <= 0.0:
        raise ValueError("initial_cash must be positive")

    net_pnl = float(summary.get("net_pnl") or 0.0)
    max_drawdown = float(summary.get("max_drawdown") or 0.0)

    net_return_bps = net_pnl / initial_cash * 10000.0
    mdd_bps = max_drawdown * 10000.0
    score = net_return_bps - (lambda_mdd * mdd_bps)
    return score, net_return_bps, mdd_bps


def _invalid_stage_evaluation(entry_frequency: float = 0.0) -> _StageEvaluation:
    return _StageEvaluation(
        score=_FREQ_PENALTY,
        net_return_bps=0.0,
        max_drawdown=0.0,
        entry_frequency=entry_frequency,
        is_valid=False,
    )


def _evaluate_stage_summary(
    summary: dict[str, Any],
    *,
    initial_cash: float,
    lambda_mdd: float,
) -> _StageEvaluation:
    n_states = float(summary.get("n_states") or 0.0)
    signal_count = float(summary.get("signal_count") or 0.0)
    if n_states <= 0.0 or signal_count <= 0.0:
        return _invalid_stage_evaluation()

    entry_frequency = signal_count / n_states
    if entry_frequency < _MIN_FREQ or entry_frequency > _MAX_FREQ:
        return _invalid_stage_evaluation(entry_frequency=entry_frequency)

    try:
        score, net_return_bps, _mdd_bps = _score_from_backtest_summary(
            summary,
            initial_cash=initial_cash,
            lambda_mdd=lambda_mdd,
        )
    except Exception:
        return _invalid_stage_evaluation(entry_frequency=entry_frequency)

    return _StageEvaluation(
        score=score,
        net_return_bps=net_return_bps,
        max_drawdown=float(summary.get("max_drawdown") or 0.0),
        entry_frequency=entry_frequency,
        is_valid=True,
    )


def _run_candidate_stage_backtest(
    *,
    candidate_code: str,
    stage_states: "list[MarketState]",
    backtest_config: "BacktestConfig",
    data_dir: str | Path,
) -> dict[str, Any]:
    from evaluation_orchestration.layer7_validation import PipelineRunner
    from strategy_loop.code_strategy import CodeStrategy

    strategy = CodeStrategy(code=candidate_code, name="optuna_candidate")
    runner = PipelineRunner(
        config=backtest_config,
        data_dir=str(data_dir),
        output_dir=None,
        strategy=strategy,
    )
    return runner.run(stage_states).summary()


def _report_stage_and_maybe_prune(
    trial: "optuna.Trial",
    *,
    score: float,
    step_idx: int,
    enable_pruning: bool,
) -> None:
    import optuna

    trial.report(score, step_idx)
    if enable_pruning and trial.should_prune():
        raise optuna.TrialPruned()


def _run_stage_objective(
    *,
    trial: "optuna.Trial",
    candidate_code: str,
    stage_state_slices: "list[list[MarketState]]",
    backtest_config: "BacktestConfig",
    data_dir: str | Path,
    lambda_mdd: float,
    enable_pruning: bool,
) -> _StageEvaluation:
    if not stage_state_slices:
        return _invalid_stage_evaluation()

    initial_cash = float(getattr(backtest_config, "initial_cash", 0.0) or 0.0)
    if initial_cash <= 0.0:
        return _invalid_stage_evaluation()

    last_valid = _invalid_stage_evaluation()
    for step_idx, stage_states in enumerate(stage_state_slices):
        try:
            summary = _run_candidate_stage_backtest(
                candidate_code=candidate_code,
                stage_states=stage_states,
                backtest_config=backtest_config,
                data_dir=data_dir,
            )
        except Exception:
            stage_eval = _invalid_stage_evaluation()
        else:
            stage_eval = _evaluate_stage_summary(
                summary,
                initial_cash=initial_cash,
                lambda_mdd=lambda_mdd,
            )

        _report_stage_and_maybe_prune(
            trial,
            score=stage_eval.score,
            step_idx=step_idx,
            enable_pruning=enable_pruning,
        )

        if not stage_eval.is_valid:
            return stage_eval
        last_valid = stage_eval

    return last_valid


def _evaluate_code_once(
    *,
    code: str,
    states: "list[MarketState]",
    backtest_config: "BacktestConfig",
    data_dir: str | Path,
    lambda_mdd: float,
) -> _StageEvaluation:
    if not states:
        return _invalid_stage_evaluation()

    initial_cash = float(getattr(backtest_config, "initial_cash", 0.0) or 0.0)
    if initial_cash <= 0.0:
        return _invalid_stage_evaluation()

    try:
        summary = _run_candidate_stage_backtest(
            candidate_code=code,
            stage_states=states,
            backtest_config=backtest_config,
            data_dir=data_dir,
        )
    except Exception:
        return _invalid_stage_evaluation()

    return _evaluate_stage_summary(
        summary,
        initial_cash=initial_cash,
        lambda_mdd=lambda_mdd,
    )


def optimize_code_thresholds(
    code: str,
    states: "list[MarketState]",
    backtest_config: "BacktestConfig",
    data_dir: str | Path,
    n_trials: int = 20,
    sampler_seed: int = 42,
    lambda_mdd: float = _DEFAULT_LAMBDA_MDD,
    stage_prefixes: tuple[float, ...] | list[float] = _DEFAULT_STAGE_PREFIXES,
    enable_pruning: bool = True,
) -> CodeOptimizationResult:
    """Optimize UPPER_CASE constants with real-backtest objective."""
    import optuna
    from optuna.trial import TrialState
    from strategy_loop.code_sandbox import CodeSandboxError, exec_strategy_code

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    stage_state_slices = _build_stage_state_slices(states, stage_prefixes)
    params_meta = extract_code_params(code)
    lambda_mdd = float(lambda_mdd)

    if n_trials <= 0 or not params_meta:
        try:
            exec_strategy_code(code)
            one_eval = _evaluate_code_once(
                code=code,
                states=states,
                backtest_config=backtest_config,
                data_dir=data_dir,
                lambda_mdd=lambda_mdd,
            )
        except Exception:
            one_eval = _invalid_stage_evaluation()

        return CodeOptimizationResult(
            best_code=code,
            best_score=one_eval.score,
            n_trials_run=0,
            entry_frequency=one_eval.entry_frequency,
            best_net_return_bps=one_eval.net_return_bps,
            best_max_drawdown=one_eval.max_drawdown,
            n_trials_pruned=0,
        )

    _original_code: str = code
    best_code: str = code
    best_score: float = _FREQ_PENALTY
    best_net_return_bps: float = 0.0
    best_max_drawdown: float = 0.0
    best_freq: float = 0.0
    saw_valid_trial: bool = False

    def objective(trial: "optuna.Trial") -> float:
        nonlocal best_code, best_score, best_net_return_bps, best_max_drawdown, best_freq, saw_valid_trial

        values: dict[str, float | int] = {}
        for name, (lo, hi, log, is_int, _current) in params_meta.items():
            if is_int:
                values[name] = trial.suggest_int(name, int(lo), int(hi))
            elif log and lo > 0:
                values[name] = trial.suggest_float(name, lo, hi, log=True)
            else:
                values[name] = trial.suggest_float(name, lo, hi)

        candidate_code = inject_code_params(code, values)

        try:
            # Explicit compile/validate before real backtest stages
            exec_strategy_code(candidate_code)
        except CodeSandboxError:
            return _FREQ_PENALTY

        try:
            stage_eval = _run_stage_objective(
                trial=trial,
                candidate_code=candidate_code,
                stage_state_slices=stage_state_slices,
                backtest_config=backtest_config,
                data_dir=data_dir,
                lambda_mdd=lambda_mdd,
                enable_pruning=enable_pruning,
            )
        except optuna.TrialPruned:
            raise
        except Exception:
            return _FREQ_PENALTY

        if stage_eval.is_valid:
            saw_valid_trial = True
            if stage_eval.score > best_score:
                best_score = stage_eval.score
                best_code = candidate_code
                best_net_return_bps = stage_eval.net_return_bps
                best_max_drawdown = stage_eval.max_drawdown
                best_freq = stage_eval.entry_frequency

        return stage_eval.score

    sampler = optuna.samplers.TPESampler(seed=sampler_seed)
    pruner = (
        optuna.pruners.MedianPruner(
            n_startup_trials=_PRUNER_STARTUP_TRIALS,
            n_warmup_steps=_PRUNER_WARMUP_STEPS,
            interval_steps=_PRUNER_INTERVAL_STEPS,
        )
        if enable_pruning
        else optuna.pruners.NopPruner()
    )
    study = optuna.create_study(direction="maximize", sampler=sampler, pruner=pruner)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    n_trials_pruned = sum(1 for t in study.trials if t.state == TrialState.PRUNED)

    # all-invalid / all-pruned fallback
    if not saw_valid_trial:
        return CodeOptimizationResult(
            best_code=_original_code,
            best_score=_FREQ_PENALTY,
            n_trials_run=len(study.trials),
            entry_frequency=0.0,
            best_net_return_bps=0.0,
            best_max_drawdown=0.0,
            n_trials_pruned=n_trials_pruned,
        )

    return CodeOptimizationResult(
        best_code=best_code,
        best_score=best_score,
        n_trials_run=len(study.trials),
        entry_frequency=best_freq,
        best_net_return_bps=best_net_return_bps,
        best_max_drawdown=best_max_drawdown,
        n_trials_pruned=n_trials_pruned,
    )
