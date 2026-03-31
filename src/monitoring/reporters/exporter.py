"""
monitoring/reporters/exporter.py
----------------------------------
Write all monitoring artefacts to disk under output_dir/run_id/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def export_monitoring_run(
    bus,
    report,
    output_dir: Path,
    run_id: str,
) -> dict[str, Path]:
    """
    Export all monitoring data for one run to output_dir/run_id/.

    Files created
    -------------
    fills.csv
    queue_init.csv
    queue_ticks.csv         (only when bus has QueueTickEvent rows)
    order_submits.csv
    cancel_requests.csv
    fill_report.json
    queue_report.json
    verification.json

    Returns
    -------
    dict mapping filename → absolute Path
    """
    from monitoring.events import (
        FillEvent as MonFillEvent,
        QueueInitEvent,
        QueueTickEvent,
        OrderSubmitEvent,
        CancelRequestEvent,
    )
    from monitoring.reporters.fill_report import build_fill_report
    from monitoring.reporters.queue_report import build_queue_report
    from monitoring.reporters.verification_report import build_verification_report

    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    created: dict[str, Path] = {}

    def _save_df(df: pd.DataFrame, name: str) -> None:
        path = run_dir / name
        df.to_csv(path, index=False)
        created[name] = path

    def _save_json(obj: dict, name: str) -> None:
        path = run_dir / name
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        created[name] = path

    # --- CSV exports ---
    _save_df(bus.to_dataframe(MonFillEvent),      "fills.csv")
    _save_df(bus.to_dataframe(QueueInitEvent),    "queue_init.csv")
    _save_df(bus.to_dataframe(OrderSubmitEvent),  "order_submits.csv")
    _save_df(bus.to_dataframe(CancelRequestEvent),"cancel_requests.csv")

    qt_df = bus.to_dataframe(QueueTickEvent)
    if not qt_df.empty:
        _save_df(qt_df, "queue_ticks.csv")

    # --- JSON exports ---
    fill_rpt  = build_fill_report(bus)
    queue_rpt = build_queue_report(bus)
    ver_dict  = build_verification_report(report)

    _save_json(fill_rpt.summary,  "fill_report.json")
    _save_json(queue_rpt.summary, "queue_report.json")
    _save_json(ver_dict,          "verification.json")

    return created
