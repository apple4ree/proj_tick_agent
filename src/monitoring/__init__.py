"""
monitoring/__init__.py
----------------------
Backtest monitoring module.

Entry point:
  runner = attach_to_pipeline(runner, config)  →  InstrumentedPipelineRunner
  report = run_all_verifiers(runner.bus)
  export_monitoring_run(runner.bus, report, export_dir, result.run_id)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evaluation_orchestration.layer7_validation.pipeline_runner import PipelineRunner


@dataclass
class MonitorConfig:
    """Configuration for the monitoring module."""
    verbose: bool = False
    filter_order_ids: set[str] | None = None
    verify_fees: bool = True
    verify_slippage: bool = True
    verify_latency_ordering: bool = True
    verify_queue_arithmetic: bool = False
    export_dir: Path | None = None


def attach_to_pipeline(
    runner: "PipelineRunner",
    config: "MonitorConfig | None" = None,
) -> "InstrumentedPipelineRunner":
    """
    Wrap a PipelineRunner with monitoring instrumentation.

    The original runner must not have been run yet.  Its config, data_dir,
    output_dir, and strategy are forwarded to the InstrumentedPipelineRunner.

    Parameters
    ----------
    runner : PipelineRunner
        The runner to instrument.
    config : MonitorConfig | None
        Monitoring configuration.  Defaults to MonitorConfig() (non-verbose,
        all verifiers enabled except queue arithmetic).

    Returns
    -------
    InstrumentedPipelineRunner
        Access the EventBus via ``runner.bus``.

    Example
    -------
    runner = PipelineRunner(config=bc, data_dir=data_dir, strategy=strategy)
    runner = attach_to_pipeline(runner)
    result = runner.run(states)
    report = run_all_verifiers(runner.bus)
    export_monitoring_run(runner.bus, report, export_dir, result.run_id)
    """
    from monitoring.event_bus import EventBus
    from monitoring.instrumented_pipeline_runner import InstrumentedPipelineRunner

    mc = config or MonitorConfig()
    bus = EventBus(
        verbose=mc.verbose,
        filter_order_ids=mc.filter_order_ids,
    )
    return InstrumentedPipelineRunner(
        config=runner.config,
        data_dir=runner.data_dir,
        output_dir=runner.output_dir,
        strategy=runner._strategy,
        bus=bus,
    )
