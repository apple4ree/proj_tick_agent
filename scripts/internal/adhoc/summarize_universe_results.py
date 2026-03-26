"""
Universe 백테스트 결과 집계 스크립트.

종목별 / latency별 결과를 평균, 중앙값, 표준편차, 승률로 요약합니다.

사용법:
    cd /home/dgu/tick/proj_rl_agent

    PYTHONPATH=src python scripts/internal/adhoc/summarize_universe_results.py \
        --results outputs/universe_backtest/imbalance_momentum/universe_results.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


_DEFAULT_GROUP_BY = "latency_ms"
_DEFAULT_METRICS = ["net_pnl", "sharpe_ratio", "max_drawdown", "fill_rate"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize universe backtest results")
    parser.add_argument("--results", required=True, help="Path to universe_results.csv")
    return parser.parse_args()


def aggregate_metrics(df: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    """Compute mean, median, std, win_rate for each metric."""
    records = []
    for metric in metrics:
        if metric not in df.columns:
            continue
        vals = df[metric].dropna()
        if len(vals) == 0:
            continue
        records.append({
            "metric": metric,
            "count": len(vals),
            "mean": float(vals.mean()),
            "median": float(vals.median()),
            "std": float(vals.std()) if len(vals) > 1 else 0.0,
            "min": float(vals.min()),
            "max": float(vals.max()),
            "win_rate": float((vals > 0).mean()) if metric in ("net_pnl", "sharpe_ratio") else np.nan,
        })
    return pd.DataFrame(records)


def summarize_grouped(df: pd.DataFrame, group_col: str, metrics: list[str]) -> pd.DataFrame:
    """Aggregate per group."""
    all_rows = []
    for group_val, group_df in df.groupby(group_col):
        agg = aggregate_metrics(group_df, metrics)
        agg.insert(0, group_col, group_val)
        all_rows.append(agg)
    if not all_rows:
        return pd.DataFrame()
    return pd.concat(all_rows, ignore_index=True)


def main() -> None:
    args = parse_args()
    results_path = Path(args.results)
    if not results_path.exists():
        print(f"File not found: {results_path}")
        sys.exit(1)

    df = pd.read_csv(results_path)
    metrics = _DEFAULT_METRICS

    print(f"\n{'=' * 70}")
    print(f"Universe Results Summary")
    print(f"{'=' * 70}")
    print(f"Source: {results_path}")
    print(f"Total runs: {len(df)}")

    if "symbol" in df.columns:
        print(f"Unique symbols: {df['symbol'].nunique()}")
    if "latency_ms" in df.columns:
        print(f"Latency levels: {sorted(df['latency_ms'].unique())}")

    # Overall summary
    print(f"\n{'─' * 70}")
    print("OVERALL")
    print(f"{'─' * 70}")
    overall = aggregate_metrics(df, metrics)
    if not overall.empty:
        print(overall.to_string(index=False, float_format="%.4f"))

    # Grouped summary
    group_by = _DEFAULT_GROUP_BY
    if group_by in df.columns:
        print(f"\n{'─' * 70}")
        print(f"BY {group_by.upper()}")
        print(f"{'─' * 70}")
        grouped = summarize_grouped(df, group_by, metrics)
        if not grouped.empty:
            print(grouped.to_string(index=False, float_format="%.4f"))

    # Save output
    output_path = results_path.parent / "universe_summary.csv"

    overall.to_csv(output_path, index=False)
    print(f"\nSummary saved: {output_path}")

    # Latency impact analysis
    if "latency_ms" in df.columns and "net_pnl" in df.columns:
        print(f"\n{'─' * 70}")
        print("LATENCY IMPACT ANALYSIS")
        print(f"{'─' * 70}")
        for lat in sorted(df["latency_ms"].unique()):
            lat_df = df[df["latency_ms"] == lat]
            pnl = lat_df["net_pnl"]
            sharpe = lat_df["sharpe_ratio"] if "sharpe_ratio" in lat_df.columns else pd.Series()
            print(f"  Latency {lat:>7.0f}ms | "
                  f"PnL mean={pnl.mean():>12,.0f} median={pnl.median():>12,.0f} | "
                  f"Sharpe mean={sharpe.mean():>6.3f} | "
                  f"Win rate={float((pnl > 0).mean()):>5.1%} | "
                  f"N={len(lat_df)}")

    # Per-latency CSV breakdown
    if "latency_ms" in df.columns:
        latency_vals = sorted(df["latency_ms"].unique())
        if len(latency_vals) > 1:
            for lat in latency_vals:
                lat_df = df[df["latency_ms"] == lat]
                lat_path = results_path.parent / f"summary_latency_{int(lat)}ms.csv"
                agg = aggregate_metrics(lat_df, metrics)
                agg.to_csv(lat_path, index=False)


if __name__ == "__main__":
    main()
