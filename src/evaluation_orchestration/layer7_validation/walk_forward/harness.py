"""Walk-forward harness built on top of existing backtest entrypoints."""
from __future__ import annotations

import importlib.util
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from evaluation_orchestration.layer6_evaluator.selection_metrics import SelectionMetrics, SelectionScore
from strategy_block.strategy_compiler import compile_strategy
from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
from utils.config import get_backtest, get_paths, load_config

from .window_plan import WalkForwardWindow, WalkForwardWindowPlanner


@dataclass
class WindowExecutionArtifact:
    run_dir: str
    summary: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None


@dataclass
class WalkForwardRunResult:
    trial_id: str | None
    window: WalkForwardWindow
    run_dir: str
    summary: dict[str, Any]
    diagnostics: dict[str, Any]
    selection_score: SelectionScore


class WalkForwardHarness:
    """Execute the same spec over rolling windows and score each run."""

    def __init__(
        self,
        *,
        window_planner: WalkForwardWindowPlanner | None = None,
        selection_metrics: SelectionMetrics | None = None,
        run_executor: Callable[..., WindowExecutionArtifact] | None = None,
    ) -> None:
        self._window_planner = window_planner or WalkForwardWindowPlanner()
        self._selection_metrics = selection_metrics or SelectionMetrics()
        self._run_executor = run_executor or self._default_execute_window
        self._backtest_module = None
        self._universe_module = None

    def run_spec(
        self,
        *,
        spec_path: str,
        symbol: str | None = None,
        universe: bool = False,
        cfg: dict[str, Any],
        trial_id: str | None = None,
        selection_context: dict[str, Any] | None = None,
    ) -> list[WalkForwardRunResult]:
        if not universe and not symbol:
            raise ValueError("symbol is required when universe=False")

        start_date = str(cfg.get("start_date") or "").strip()
        end_date = str(cfg.get("end_date") or "").strip()
        if not start_date or not end_date:
            raise ValueError("cfg must include start_date and end_date")

        windows = self._window_planner.build(
            start_date=start_date,
            end_date=end_date,
            cfg=cfg,
        )

        results: list[WalkForwardRunResult] = []
        for idx, window in enumerate(windows):
            try:
                artifact = self._run_executor(
                    spec_path=spec_path,
                    symbol=symbol,
                    universe=universe,
                    cfg=cfg,
                    trial_id=trial_id,
                    window=window,
                    window_index=idx,
                )
                summary = dict(artifact.summary or self._load_json(Path(artifact.run_dir) / "summary.json"))
                diagnostics = dict(
                    artifact.diagnostics or self._load_json(Path(artifact.run_dir) / "realism_diagnostics.json")
                )
            except Exception as exc:  # noqa: BLE001
                fallback_dir = self._window_output_dir(spec_path, symbol, universe, cfg, trial_id, idx, window)
                fallback_dir.mkdir(parents=True, exist_ok=True)
                (fallback_dir / "walk_forward_error.json").write_text(
                    json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                summary = {"execution_error": str(exc)}
                diagnostics = {}
                artifact = WindowExecutionArtifact(run_dir=str(fallback_dir))

            score = self._selection_metrics.score_run(
                summary=summary,
                diagnostics=diagnostics,
                context=selection_context,
            )
            score.metadata.setdefault("window_index", idx)
            score.metadata.setdefault("forward_start", window.forward_start)
            score.metadata.setdefault("forward_end", window.forward_end)
            score.metadata.setdefault("trial_id", trial_id)

            results.append(
                WalkForwardRunResult(
                    trial_id=trial_id,
                    window=window,
                    run_dir=str(artifact.run_dir),
                    summary=summary,
                    diagnostics=diagnostics,
                    selection_score=score,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Internal execution paths
    # ------------------------------------------------------------------

    def _default_execute_window(
        self,
        *,
        spec_path: str,
        symbol: str | None,
        universe: bool,
        cfg: dict[str, Any],
        trial_id: str | None,
        window: WalkForwardWindow,
        window_index: int,
    ) -> WindowExecutionArtifact:
        if universe:
            return self._execute_universe_window(
                spec_path=spec_path,
                cfg=cfg,
                trial_id=trial_id,
                window=window,
                window_index=window_index,
            )
        return self._execute_single_window(
            spec_path=spec_path,
            symbol=str(symbol),
            cfg=cfg,
            trial_id=trial_id,
            window=window,
            window_index=window_index,
        )

    def _execute_single_window(
        self,
        *,
        spec_path: str,
        symbol: str,
        cfg: dict[str, Any],
        trial_id: str | None,
        window: WalkForwardWindow,
        window_index: int,
    ) -> WindowExecutionArtifact:
        backtest_script = self._load_backtest_script()
        yaml_cfg = load_config(
            config_path=cfg.get("config_path"),
            profile=cfg.get("profile"),
        )
        paths = get_paths(yaml_cfg)
        bt = get_backtest(yaml_cfg)
        data_dir = cfg.get("data_dir") or paths["data_dir"]

        backtest_config = backtest_script.backtest_config_from_cfg(
            yaml_cfg,
            symbol=symbol,
            start_date=window.forward_start,
            end_date=window.forward_end,
        )

        states = backtest_script.build_states_for_range(
            data_dir=data_dir,
            symbol=symbol,
            start_date=window.forward_start,
            end_date=window.forward_end,
            resample_freq=bt.get("resample", "1s"),
            trade_lookback=int(bt.get("trade_lookback", 100)),
        )

        strategy = compile_strategy(StrategySpecV2.load(spec_path))
        window_out = self._window_output_dir(spec_path, symbol, False, cfg, trial_id, window_index, window)
        window_out.mkdir(parents=True, exist_ok=True)

        result = backtest_script.run_backtest_with_states(
            config=backtest_config,
            states=states,
            data_dir=str(data_dir),
            output_dir=str(window_out),
            strategy=strategy,
            yaml_cfg=yaml_cfg,
        )

        run_dir = window_out / result.run_id
        return WindowExecutionArtifact(
            run_dir=str(run_dir),
            summary=result.summary(),
            diagnostics=dict(result.metadata.get("realism_diagnostics") or {}),
        )

    def _execute_universe_window(
        self,
        *,
        spec_path: str,
        cfg: dict[str, Any],
        trial_id: str | None,
        window: WalkForwardWindow,
        window_index: int,
    ) -> WindowExecutionArtifact:
        universe_script = self._load_universe_backtest_script()
        yaml_cfg = load_config(
            config_path=cfg.get("config_path"),
            profile=cfg.get("profile"),
        )
        paths = get_paths(yaml_cfg)
        bt = get_backtest(yaml_cfg)

        data_dir = cfg.get("data_dir") or paths["data_dir"]
        symbols = universe_script.discover_symbols(data_dir, window.forward_start, window.forward_end)
        if not symbols:
            raise RuntimeError(
                f"No symbols discovered in universe for {window.forward_start}..{window.forward_end}"
            )

        latency_ms = float(cfg.get("latency_ms", bt.get("latency_ms", 0.0)))
        decision_compute_ms = float(bt.get("decision_compute_ms", 0.0))
        market_data_delay_ms = float(bt.get("market_data_delay_ms", 0.0))
        initial_cash = float(bt.get("initial_cash", 1e8))
        seed = int(bt.get("seed", 42))
        compute_attribution = bool(bt.get("compute_attribution", True))
        resample = bt.get("resample", "1s")

        compiled_spec = StrategySpecV2.load(spec_path)

        def strategy_factory():
            return compile_strategy(compiled_spec)

        success_rows: list[dict[str, Any]] = []
        failed_rows: list[dict[str, Any]] = []

        t0 = time.monotonic()
        for symbol in symbols:
            try:
                states = universe_script.build_states(
                    data_dir,
                    symbol,
                    window.forward_start,
                    window.forward_end,
                    resample,
                    None,
                )
            except Exception as exc:  # noqa: BLE001
                failed_rows.append({"symbol": symbol, "error": f"build_states: {exc}"})
                continue

            run_result = universe_script.run_single_backtest(
                strategy_cls=strategy_factory,
                symbol=symbol,
                states=states,
                data_dir=data_dir,
                latency_ms=latency_ms,
                initial_cash=initial_cash,
                seed=seed,
                compute_attribution=compute_attribution,
                decision_compute_ms=decision_compute_ms,
                market_data_delay_ms=market_data_delay_ms,
                start_date=self._to_dash(window.forward_start),
                end_date=self._to_dash(window.forward_end),
                summary_only=True,
            )
            if run_result.ok:
                success_rows.append(dict(run_result.summary or {}))
            else:
                failed_rows.append({"symbol": symbol, "error": run_result.error or "run_failed"})

        elapsed_s = time.monotonic() - t0
        aggregate_summary = self._aggregate_universe_summary(success_rows)
        aggregate_summary["universe_symbol_count"] = float(len(success_rows))
        aggregate_summary["universe_failed_count"] = float(len(failed_rows))
        aggregate_summary["universe_total_symbols"] = float(len(symbols))

        diagnostics = {
            "lifecycle": {
                "parent_order_count": aggregate_summary.get("parent_order_count"),
                "child_order_count": aggregate_summary.get("child_order_count"),
                "cancel_rate": aggregate_summary.get("cancel_rate"),
                "signal_count": aggregate_summary.get("signal_count"),
            },
            "queue": {
                "maker_fill_ratio": aggregate_summary.get("maker_fill_ratio"),
            },
            "timings": {
                "total_s": round(elapsed_s, 3),
            },
        }

        run_dir = self._window_output_dir(
            spec_path,
            symbol="__universe__",
            universe=True,
            cfg=cfg,
            trial_id=trial_id,
            window_index=window_index,
            window=window,
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary.json").write_text(
            json.dumps(aggregate_summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (run_dir / "realism_diagnostics.json").write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (run_dir / "universe_results.json").write_text(
            json.dumps(success_rows, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if failed_rows:
            (run_dir / "failed_runs.json").write_text(
                json.dumps(failed_rows, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

        return WindowExecutionArtifact(
            run_dir=str(run_dir),
            summary=aggregate_summary,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _to_dash(value: str) -> str:
        raw = str(value).strip().replace("-", "")
        if len(raw) != 8:
            return str(value)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"

    def _window_output_dir(
        self,
        spec_path: str,
        symbol: str | None,
        universe: bool,
        cfg: dict[str, Any],
        trial_id: str | None,
        window_index: int,
        window: WalkForwardWindow,
    ) -> Path:
        root = Path(str(cfg.get("output_root") or cfg.get("output_dir") or "outputs/walk_forward"))
        spec_stem = Path(spec_path).stem
        scope = "universe" if universe else str(symbol or "single")
        trial_part = f"trial_{trial_id}" if trial_id else "adhoc"
        window_part = f"window_{window_index:03d}_{window.forward_start}_{window.forward_end}"
        return root / spec_stem / trial_part / scope / window_part

    @staticmethod
    def _aggregate_universe_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"net_pnl": 0.0, "n_fills": 0.0}

        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        passthrough_keys = {
            "resample_interval",
            "canonical_tick_interval_ms",
            "queue_model",
            "queue_position_assumption",
        }
        passthrough: dict[str, Any] = {}

        for row in rows:
            for key, value in row.items():
                if key in passthrough_keys and key not in passthrough:
                    passthrough[key] = value
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                sums[key] = sums.get(key, 0.0) + numeric
                counts[key] = counts.get(key, 0) + 1

        aggregated = {key: (sums[key] / counts[key]) for key in sums.keys()}
        aggregated.update(passthrough)
        return aggregated

    def _load_backtest_script(self):
        if self._backtest_module is None:
            self._backtest_module = self._load_script_module("walk_forward_backtest_script", "backtest.py")
        return self._backtest_module

    def _load_universe_backtest_script(self):
        if self._universe_module is None:
            self._universe_module = self._load_script_module(
                "walk_forward_universe_backtest_script",
                "backtest_strategy_universe.py",
            )
        return self._universe_module

    @staticmethod
    def _load_script_module(module_name: str, script_name: str):
        project_root = Path(__file__).resolve().parents[4]
        script_path = project_root / "scripts" / script_name
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load script module: {script_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
