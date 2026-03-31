"""
monitoring/reporters/fill_report.py
-------------------------------------
Aggregate fill-level statistics from EventBus.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class FillReport:
    df: pd.DataFrame
    summary: dict


def build_fill_report(bus) -> FillReport:
    """Build a FillReport from the EventBus.

    Parameters
    ----------
    bus : EventBus
    """
    from monitoring.events import FillEvent as MonFillEvent

    df = bus.to_dataframe(MonFillEvent)

    if df.empty:
        return FillReport(df=df, summary={
            "n_fills": 0, "n_buys": 0, "n_sells": 0, "n_maker": 0, "n_taker": 0,
            "avg_slippage_bps": 0.0, "p50_slippage_bps": 0.0, "p95_slippage_bps": 0.0,
            "avg_impact_bps": 0.0,
            "avg_fee_krw": 0.0, "total_fee_krw": 0.0,
            "avg_latency_ms": 0.0, "p50_latency_ms": 0.0, "p95_latency_ms": 0.0,
            "avg_queue_wait_ticks": 0.0, "avg_queue_wait_ms": 0.0,
            "fill_rate_by_side": {"BUY": 0.0, "SELL": 0.0},
        })

    n = len(df)
    n_buys  = int((df["side"] == "BUY").sum())
    n_sells = int((df["side"] == "SELL").sum())
    n_maker = int(df["is_maker"].sum())

    def _pct(col, q):
        return float(np.percentile(df[col].dropna(), q * 100)) if len(df[col].dropna()) > 0 else 0.0

    summary = {
        "n_fills":            n,
        "n_buys":             n_buys,
        "n_sells":            n_sells,
        "n_maker":            n_maker,
        "n_taker":            n - n_maker,
        "avg_slippage_bps":   float(df["slippage_bps"].mean()),
        "p50_slippage_bps":   _pct("slippage_bps", 0.5),
        "p95_slippage_bps":   _pct("slippage_bps", 0.95),
        "avg_impact_bps":     float(df["impact_bps"].mean()),
        "avg_fee_krw":        float(df["fee"].mean()),
        "total_fee_krw":      float(df["fee"].sum()),
        "avg_latency_ms":     float(df["latency_ms"].mean()),
        "p50_latency_ms":     _pct("latency_ms", 0.5),
        "p95_latency_ms":     _pct("latency_ms", 0.95),
        "avg_queue_wait_ticks": float(df["queue_wait_ticks"].mean()),
        "avg_queue_wait_ms":    float(df["queue_wait_ms"].mean()),
        "fill_rate_by_side":  {
            "BUY":  n_buys / n,
            "SELL": n_sells / n,
        },
    }
    return FillReport(df=df, summary=summary)
