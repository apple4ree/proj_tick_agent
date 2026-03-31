"""
monitoring/reporters/queue_report.py
--------------------------------------
Aggregate queue-level statistics from EventBus.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class QueueReport:
    df_init: pd.DataFrame
    df_tick: pd.DataFrame
    summary: dict


def build_queue_report(bus) -> QueueReport:
    """Build a QueueReport from the EventBus."""
    from monitoring.events import QueueInitEvent, QueueTickEvent

    df_init = bus.to_dataframe(QueueInitEvent)
    df_tick = bus.to_dataframe(QueueTickEvent)

    empty_summary = {
        "n_orders_queued": 0,
        "avg_initial_queue_ahead": 0.0,
        "avg_ticks_to_ready": 0.0,
        "gate_pass_rate": 0.0,
        "avg_depth_advancement_per_tick": 0.0,
        "avg_trade_advancement_per_tick": 0.0,
        "depth_vs_trade_ratio": 0.0,
    }

    if df_init.empty:
        return QueueReport(df_init=df_init, df_tick=df_tick, summary=empty_summary)

    n_orders = int(df_init["child_id"].nunique())
    avg_initial = float(df_init["queue_ahead_qty_init"].mean()) if "queue_ahead_qty_init" in df_init.columns else 0.0

    if df_tick.empty:
        return QueueReport(df_init=df_init, df_tick=df_tick, summary={
            **empty_summary,
            "n_orders_queued": n_orders,
            "avg_initial_queue_ahead": avg_initial,
        })

    # Ticks to ready: count ticks per child_id until gate_passed=True
    ticks_to_ready = []
    for cid, grp in df_tick.groupby("child_id"):
        grp_sorted = grp.sort_values("tick_index")
        gate_ticks = grp_sorted[grp_sorted["gate_passed"]]["tick_index"]
        if len(gate_ticks) > 0:
            first_pass = gate_ticks.iloc[0]
            ticks_to_ready.append(int(first_pass - grp_sorted["tick_index"].iloc[0] + 1))

    gate_pass_rate = float(df_tick["gate_passed"].mean()) if "gate_passed" in df_tick.columns else 0.0
    avg_depth = float(df_tick["depth_advancement"].mean()) if "depth_advancement" in df_tick.columns else 0.0
    avg_trade = float(df_tick["trade_advancement"].mean()) if "trade_advancement" in df_tick.columns else 0.0
    total_adv = avg_depth + avg_trade
    ratio = (avg_depth / total_adv) if total_adv > 0 else 0.0

    summary = {
        "n_orders_queued":               n_orders,
        "avg_initial_queue_ahead":       avg_initial,
        "avg_ticks_to_ready":            float(np.mean(ticks_to_ready)) if ticks_to_ready else 0.0,
        "gate_pass_rate":                gate_pass_rate,
        "avg_depth_advancement_per_tick": avg_depth,
        "avg_trade_advancement_per_tick": avg_trade,
        "depth_vs_trade_ratio":          ratio,
    }
    return QueueReport(df_init=df_init, df_tick=df_tick, summary=summary)
