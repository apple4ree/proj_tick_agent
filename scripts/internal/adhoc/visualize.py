"""
visualize.py
------------
Backtest 결과 시각화 스크립트.

시그널, 체결, PnL, 비용, realism diagnostics를 한눈에 파악할 수 있는
static matplotlib 차트를 생성합니다.

사용법:
    cd /home/dgu/tick/proj_rl_agent
    python scripts/internal/adhoc/visualize.py --run-dir outputs/backtests/<run_id>
    python scripts/internal/adhoc/visualize.py --run-dir outputs/backtests/<run_id> --no-show
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────────
# 데이터 로딩
# ──────────────────────────────────────────────────────────────────────

def load_run(run_dir: Path) -> dict[str, pd.DataFrame | dict]:
    """Load CSV/JSON artifacts from a backtest run directory."""
    data: dict[str, pd.DataFrame | dict] = {}

    signals_path = run_dir / "signals.csv"
    if signals_path.exists():
        data["signals"] = pd.read_csv(signals_path, parse_dates=["timestamp"])

    fills_path = run_dir / "fills.csv"
    if fills_path.exists():
        data["fills"] = pd.read_csv(fills_path, parse_dates=["timestamp"])

    orders_path = run_dir / "orders.csv"
    if orders_path.exists():
        data["orders"] = pd.read_csv(orders_path)

    quotes_path = run_dir / "market_quotes.csv"
    if quotes_path.exists():
        data["market_quotes"] = pd.read_csv(quotes_path, parse_dates=["timestamp"])

    pnl_path = run_dir / "pnl_series.csv"
    if pnl_path.exists():
        df = pd.read_csv(pnl_path, index_col=0, parse_dates=True)
        if "cumulative_net_pnl" not in df.columns and len(df.columns) == 1:
            df.columns = ["cumulative_net_pnl"]
        data["pnl_series"] = df

    pnl_entries_path = run_dir / "pnl_entries.csv"
    if pnl_entries_path.exists():
        # report_builder 기본 저장 형태(index=timestamp)도 처리되도록 유연하게 로딩
        df = pd.read_csv(pnl_entries_path)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        elif len(df.columns) > 0:
            first = df.columns[0]
            ts = pd.to_datetime(df[first], errors="coerce")
            if ts.notna().any():
                df = df.rename(columns={first: "timestamp"})
                df["timestamp"] = ts
        data["pnl_entries"] = df

    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        with open(summary_path, encoding="utf-8") as fh:
            data["summary"] = json.load(fh)

    realism_path = run_dir / "realism_diagnostics.json"
    if realism_path.exists():
        with open(realism_path, encoding="utf-8") as fh:
            data["realism_diagnostics"] = json.load(fh)

    return data


# ──────────────────────────────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────────────────────────────

def _safe_float(value: Any, default: float = np.nan) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_value(value: Any, ndigits: int = 3) -> str:
    v = _safe_float(value)
    if np.isnan(v):
        return "N/A"
    if abs(v) >= 1000:
        return f"{v:,.{ndigits}f}"
    return f"{v:.{ndigits}f}"


def _summary_float(summary: dict, key: str, default: float = np.nan) -> float:
    return _safe_float(summary.get(key), default=default)


def _save_placeholder_plot(
    out_path: Path,
    *,
    title: str,
    message: str,
    show: bool,
    figsize: tuple[float, float] = (10, 4),
) -> Path:
    fig, ax = plt.subplots(figsize=figsize)
    ax.axis("off")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.text(
        0.5,
        0.5,
        message,
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=11,
        color="dimgray",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")
    if show:
        plt.show()
    plt.close(fig)
    return out_path


def _extract_arrival_mid_series(orders: pd.DataFrame, fills: pd.DataFrame) -> pd.DataFrame:
    """Build timestamped arrival-mid points by mapping parent_id -> orders.arrival_mid."""
    if orders.empty or fills.empty:
        return pd.DataFrame(columns=["timestamp", "arrival_mid"])
    if "arrival_mid" not in orders.columns or "parent_id" not in fills.columns:
        return pd.DataFrame(columns=["timestamp", "arrival_mid"])

    if "order_id" not in orders.columns:
        return pd.DataFrame(columns=["timestamp", "arrival_mid"])

    mids = pd.to_numeric(orders["arrival_mid"], errors="coerce")
    mid_map = dict(zip(orders["order_id"], mids))

    f = fills.copy()
    if "timestamp" not in f.columns:
        return pd.DataFrame(columns=["timestamp", "arrival_mid"])
    f = f.sort_values("timestamp").drop_duplicates("parent_id")
    f["arrival_mid"] = f["parent_id"].map(mid_map)
    f = f.dropna(subset=["arrival_mid"])
    if f.empty:
        return pd.DataFrame(columns=["timestamp", "arrival_mid"])
    return f[["timestamp", "arrival_mid"]].sort_values("timestamp")


def _compute_rolling_window(n: int) -> int:
    if n <= 0:
        return 10
    return max(10, n // 20)


def _to_datetime_column(df: pd.DataFrame, col: str = "timestamp") -> pd.DataFrame:
    out = df.copy()
    if col in out.columns:
        out[col] = pd.to_datetime(out[col], errors="coerce")
        out = out.loc[out[col].notna()].copy()
    return out


# ──────────────────────────────────────────────────────────────────────
# Figure 1: Trade-Aware Overview (기존 overview 확장)
# ──────────────────────────────────────────────────────────────────────

def plot_overview(data: dict, run_dir: Path, show: bool = True) -> Path:
    """4-panel overview: market price+fills, signal score, cumulative PnL, per-fill costs."""
    signals: pd.DataFrame = _to_datetime_column(data.get("signals", pd.DataFrame()))
    fills: pd.DataFrame = _to_datetime_column(data.get("fills", pd.DataFrame()))
    quotes: pd.DataFrame = _to_datetime_column(data.get("market_quotes", pd.DataFrame()))
    pnl_series: pd.DataFrame = data.get("pnl_series", pd.DataFrame())
    pnl_entries: pd.DataFrame = _to_datetime_column(data.get("pnl_entries", pd.DataFrame()))
    orders: pd.DataFrame = data.get("orders", pd.DataFrame())
    summary: dict = data.get("summary", {})

    sharpe = _summary_float(summary, "sharpe_ratio")
    n_fills = _summary_float(summary, "n_fills", default=0.0)
    net_pnl = _summary_float(summary, "net_pnl", default=0.0)

    sharpe_s = "N/A" if np.isnan(sharpe) else f"{sharpe:.2f}"
    fills_s = "N/A" if np.isnan(n_fills) else f"{n_fills:.0f}"

    fig, axes = plt.subplots(
        4,
        1,
        figsize=(16, 14),
        sharex=True,
        gridspec_kw={"height_ratios": [2.8, 2.0, 2.0, 1.5]},
    )
    fig.suptitle(
        f"Backtest Overview — {run_dir.name[:12]}…\n"
        f"Sharpe={sharpe_s}  Fills={fills_s}  Net PnL={net_pnl:,.0f} KRW",
        fontsize=13,
        fontweight="bold",
    )

    # ── Panel 1: Market quotes + fills + arrival mid ──
    ax1 = axes[0]
    has_market_price = False

    if not quotes.empty and "timestamp" in quotes.columns:
        quotes = quotes.sort_values("timestamp")

        if "mid_price" in quotes.columns:
            mid = pd.to_numeric(quotes["mid_price"], errors="coerce")
            if mid.notna().any():
                ax1.plot(
                    quotes["timestamp"],
                    mid,
                    color="#2c3e50",
                    linewidth=1.1,
                    alpha=0.95,
                    label="mid_price",
                    zorder=2,
                )
                has_market_price = True

        if "best_bid" in quotes.columns:
            bid = pd.to_numeric(quotes["best_bid"], errors="coerce")
            if bid.notna().any():
                ax1.plot(
                    quotes["timestamp"],
                    bid,
                    color="#27ae60",
                    linewidth=0.8,
                    alpha=0.45,
                    label="best_bid",
                    zorder=1,
                )

        if "best_ask" in quotes.columns:
            ask = pd.to_numeric(quotes["best_ask"], errors="coerce")
            if ask.notna().any():
                ax1.plot(
                    quotes["timestamp"],
                    ask,
                    color="#c0392b",
                    linewidth=0.8,
                    alpha=0.45,
                    label="best_ask",
                    zorder=1,
                )

    # fills-only line은 보조 표현으로 낮춤
    if not fills.empty and "fill_price" in fills.columns:
        fills_sorted = fills.sort_values("timestamp")
        ax1.plot(
            fills_sorted["timestamp"],
            pd.to_numeric(fills_sorted["fill_price"], errors="coerce"),
            color="#95a5a6",
            linewidth=0.6,
            alpha=0.35,
            zorder=0,
            label="fill_price path",
        )

    if not fills.empty and "side" in fills.columns and "fill_price" in fills.columns:
        fill_qty = pd.to_numeric(fills.get("filled_qty", 10), errors="coerce").fillna(10.0).abs()
        sizes = np.clip(fill_qty.values * 0.8, 20, 200)

        buys = fills["side"].astype(str).str.upper() == "BUY"
        sells = fills["side"].astype(str).str.upper() == "SELL"

        if buys.any():
            ax1.scatter(
                fills.loc[buys, "timestamp"],
                pd.to_numeric(fills.loc[buys, "fill_price"], errors="coerce"),
                marker="^",
                s=sizes[buys.values],
                color="#2ecc71",
                alpha=0.9,
                edgecolors="black",
                linewidths=0.4,
                label="BUY fill",
                zorder=5,
            )
        if sells.any():
            ax1.scatter(
                fills.loc[sells, "timestamp"],
                pd.to_numeric(fills.loc[sells, "fill_price"], errors="coerce"),
                marker="v",
                s=sizes[sells.values],
                color="#e74c3c",
                alpha=0.9,
                edgecolors="black",
                linewidths=0.4,
                label="SELL fill",
                zorder=5,
            )

    arrival_mid_series = _extract_arrival_mid_series(orders, fills)
    if not arrival_mid_series.empty:
        ax1.plot(
            arrival_mid_series["timestamp"],
            arrival_mid_series["arrival_mid"],
            color="#3498db",
            linewidth=1.0,
            alpha=0.75,
            linestyle="--",
            label="arrival_mid",
            zorder=3,
        )

    if not has_market_price and fills.empty:
        ax1.text(
            0.5,
            0.5,
            "No market_quotes.csv or fills.csv",
            transform=ax1.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color="dimgray",
        )

    ax1.set_ylabel("Price (KRW)")
    ax1.set_title("Market Price (mid/bid/ask) + Fills + Arrival Mid", fontsize=10)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax1.grid(True, alpha=0.2, linestyle="--")
    if ax1.get_legend_handles_labels()[0]:
        ax1.legend(loc="upper left", fontsize=8, ncol=2)

    # ── Panel 2: Signal score + confidence ──
    ax2 = axes[1]
    if not signals.empty and "score" in signals.columns:
        score = pd.to_numeric(signals["score"], errors="coerce").fillna(0.0)
        ax2.fill_between(signals["timestamp"], score, color="#3498db", alpha=0.28, label="signal score")
        ax2.plot(signals["timestamp"], score, color="#2980b9", linewidth=0.8)

        if "confidence" in signals.columns:
            conf = pd.to_numeric(signals["confidence"], errors="coerce")
            ax2b = ax2.twinx()
            ax2b.scatter(
                signals["timestamp"],
                conf,
                s=6,
                color="#e67e22",
                alpha=0.35,
                label="confidence",
            )
            ax2b.set_ylabel("Confidence", fontsize=9, color="#e67e22")
            ax2b.set_ylim(0, 1.1)
            ax2b.tick_params(axis="y", labelcolor="#e67e22")

        if not fills.empty and "timestamp" in fills.columns:
            # fill 타임스탬프 가독성 표시
            for ts in fills["timestamp"]:
                ax2.axvline(ts, color="#7f8c8d", alpha=0.08, linewidth=0.6)
    else:
        ax2.text(
            0.5,
            0.5,
            "No signals.csv",
            transform=ax2.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color="dimgray",
        )

    ax2.axhline(0, color="gray", linewidth=0.5, linestyle="--")
    ax2.set_ylabel("Signal Score")
    ax2.set_title("Signal Score & Confidence", fontsize=10)
    ax2.grid(True, alpha=0.2, linestyle="--")
    if ax2.get_legend_handles_labels()[0]:
        ax2.legend(loc="upper left", fontsize=8)

    # ── Panel 3: Cumulative PnL + Drawdown ──
    ax3 = axes[2]
    if not pnl_series.empty:
        if "cumulative_net_pnl" in pnl_series.columns:
            pnl = pd.to_numeric(pnl_series["cumulative_net_pnl"], errors="coerce").dropna()
        else:
            pnl = pd.to_numeric(pnl_series.iloc[:, 0], errors="coerce").dropna()

        if not pnl.empty:
            ax3.fill_between(pnl.index, pnl, where=(pnl >= 0), color="#2ecc71", alpha=0.3)
            ax3.fill_between(pnl.index, pnl, where=(pnl < 0), color="#e74c3c", alpha=0.3)
            ax3.plot(pnl.index, pnl, color="#2c3e50", linewidth=1.0)

            rolling_max = pnl.cummax()
            drawdown = pnl - rolling_max
            ax3_dd = ax3.twinx()
            ax3_dd.fill_between(pnl.index, drawdown, color="#e74c3c", alpha=0.12)
            ax3_dd.set_ylabel("Drawdown (KRW)", fontsize=9, color="#e74c3c")
            ax3_dd.tick_params(axis="y", labelcolor="#e74c3c")
    else:
        ax3.text(
            0.5,
            0.5,
            "No pnl_series.csv",
            transform=ax3.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color="dimgray",
        )

    ax3.axhline(0, color="gray", linewidth=0.5, linestyle="--")
    ax3.set_ylabel("Cumulative Net PnL (KRW)")
    ax3.set_title("Equity Curve & Drawdown", fontsize=10)
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x / 1e6:.1f}M"))
    ax3.grid(True, alpha=0.2, linestyle="--")

    # ── Panel 4: Per-fill cost breakdown ──
    ax4 = axes[3]
    if not pnl_entries.empty and "timestamp" in pnl_entries.columns:
        ts = pnl_entries["timestamp"]
        commission = pd.to_numeric(pnl_entries.get("commission_cost", 0.0), errors="coerce").fillna(0.0)
        slippage = pd.to_numeric(pnl_entries.get("slippage_cost", 0.0), errors="coerce").fillna(0.0)
        impact = pd.to_numeric(pnl_entries.get("impact_cost", 0.0), errors="coerce").fillna(0.0)

        bar_width_ms = 10_000
        ax4.bar(ts, commission, width=bar_width_ms, label="commission", color="#3498db", alpha=0.7)
        ax4.bar(ts, slippage, width=bar_width_ms, bottom=commission, label="slippage", color="#e67e22", alpha=0.7)
        ax4.bar(ts, impact, width=bar_width_ms, bottom=commission + slippage, label="impact", color="#e74c3c", alpha=0.7)
        ax4.legend(loc="upper left", fontsize=8)
    else:
        ax4.text(
            0.5,
            0.5,
            "No pnl_entries.csv",
            transform=ax4.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color="dimgray",
        )

    ax4.set_ylabel("Cost (KRW)")
    ax4.set_title("Per-Fill Cost Breakdown", fontsize=10)
    ax4.grid(True, alpha=0.2, linestyle="--")

    # format x-axis
    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax4.set_xlabel("Time")
    fig.autofmt_xdate(rotation=30)
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    out_path = run_dir / "plots" / "overview.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# Figure 2: Signal Analysis (3 panels)
# ──────────────────────────────────────────────────────────────────────

def plot_signal_analysis(data: dict, run_dir: Path, show: bool = True) -> Path:
    """Signal quality analysis: distribution, regime breakdown, confidence vs score."""
    out_path = run_dir / "plots" / "signal_analysis.png"

    signals: pd.DataFrame = _to_datetime_column(data.get("signals", pd.DataFrame()))
    if signals.empty or "score" not in signals.columns:
        return _save_placeholder_plot(
            out_path,
            title="Signal Analysis",
            message="No signals.csv",
            show=show,
            figsize=(12, 4),
        )

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle("Signal Analysis", fontsize=13, fontweight="bold")

    # ── Panel 1: Score distribution ──
    ax1 = axes[0]
    score = pd.to_numeric(signals["score"], errors="coerce").dropna()
    if not score.empty:
        ax1.hist(score, bins=50, color="#3498db", alpha=0.7, edgecolor="white")
        ax1.axvline(score.mean(), color="#e74c3c", linestyle="--", label=f"mean={score.mean():.3f}")
    ax1.axvline(0, color="gray", linewidth=0.5)
    ax1.set_xlabel("Signal Score")
    ax1.set_ylabel("Count")
    ax1.set_title("Score Distribution")
    if ax1.get_legend_handles_labels()[0]:
        ax1.legend(fontsize=8)

    # ── Panel 2: Regime breakdown (stacked bar) ──
    ax2 = axes[1]
    regime_cols = [c for c in signals.columns if c.startswith("tag_regime_")]
    if regime_cols:
        regime_col = regime_cols[0]
        regime_label = regime_col.replace("tag_regime_", "")
        regime_counts = signals[regime_col].astype(str).value_counts()
        colors = plt.cm.Set2(np.linspace(0, 1, len(regime_counts)))
        ax2.bar(regime_counts.index, regime_counts.values, color=colors, edgecolor="white")
        ax2.set_xlabel(f"Regime ({regime_label})")
        ax2.set_ylabel("Signal Count")
        ax2.set_title(f"Signals by {regime_label.title()} Regime")
        ax2.tick_params(axis="x", rotation=30)
    else:
        ax2.text(0.5, 0.5, "No regime tags", ha="center", va="center", transform=ax2.transAxes)
        ax2.set_title("Regime Breakdown")

    # ── Panel 3: Confidence vs Score scatter ──
    ax3 = axes[2]
    x = pd.to_numeric(signals["score"], errors="coerce")
    y = pd.to_numeric(signals.get("confidence", 0.0), errors="coerce")
    color_col = signals.get("expected_return", x)
    c = pd.to_numeric(color_col, errors="coerce").fillna(0.0)

    sc = ax3.scatter(x, y, c=c, cmap="RdYlGn", s=12, alpha=0.6, edgecolors="none")
    ax3.set_xlabel("Signal Score")
    ax3.set_ylabel("Confidence")
    ax3.set_title("Confidence vs Score (color=expected return)")
    plt.colorbar(sc, ax=ax3, label="Expected Return (bps)", shrink=0.8)
    ax3.axvline(0, color="gray", linewidth=0.5)
    ax3.axhline(0, color="gray", linewidth=0.5)

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# Figure 3: Execution Quality (3 panels)
# ──────────────────────────────────────────────────────────────────────

def plot_execution_quality(data: dict, run_dir: Path, show: bool = True) -> Path:
    """Execution analysis: slippage/impact scatter, latency, fill size distribution."""
    out_path = run_dir / "plots" / "execution_quality.png"
    fills: pd.DataFrame = _to_datetime_column(data.get("fills", pd.DataFrame()))
    if fills.empty:
        return _save_placeholder_plot(
            out_path,
            title="Execution Quality",
            message="No fills.csv",
            show=show,
            figsize=(12, 4),
        )

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle("Execution Quality", fontsize=13, fontweight="bold")

    # ── Panel 1: Slippage vs 시장 충격 ──
    ax1 = axes[0]
    slippage = pd.to_numeric(fills.get("slippage_bps", 0.0), errors="coerce").fillna(0.0)
    impact = pd.to_numeric(fills.get("market_impact_bps", 0.0), errors="coerce").fillna(0.0)
    qty = pd.to_numeric(fills.get("filled_qty", 1.0), errors="coerce").fillna(1.0).abs()
    colors = ["#2ecc71" if str(s).upper() == "BUY" else "#e74c3c" for s in fills.get("side", pd.Series([""] * len(fills)))]
    ax1.scatter(slippage, impact, c=colors, s=np.clip(qty * 0.6, 10, 180), alpha=0.6, edgecolors="black", linewidths=0.3)
    ax1.axhline(0, color="gray", linewidth=0.5)
    ax1.axvline(0, color="gray", linewidth=0.5)
    ax1.set_xlabel("Slippage (bps)")
    ax1.set_ylabel("Market Impact (bps)")
    ax1.set_title("Slippage vs Impact (size=qty)")

    # ── Panel 2: 지연 distribution ──
    ax2 = axes[1]
    latency = pd.to_numeric(fills.get("latency_ms", pd.Series(dtype=float)), errors="coerce").dropna()
    if not latency.empty:
        ax2.hist(latency, bins=30, color="#9b59b6", alpha=0.7, edgecolor="white")
        ax2.axvline(latency.mean(), color="#e74c3c", linestyle="--", label=f"mean={latency.mean():.2f}ms")
    ax2.set_xlabel("Latency (ms)")
    ax2.set_ylabel("Count")
    ax2.set_title("Fill Latency Distribution")
    if ax2.get_legend_handles_labels()[0]:
        ax2.legend(fontsize=8)

    # ── Panel 3: Fill qty & fee over time ──
    ax3 = axes[2]
    fills_sorted = fills.sort_values("timestamp")
    colors_bar = ["#2ecc71" if str(s).upper() == "BUY" else "#e74c3c" for s in fills_sorted.get("side", pd.Series([""] * len(fills_sorted)))]
    qty_sorted = pd.to_numeric(fills_sorted.get("filled_qty", 0), errors="coerce").fillna(0.0)
    ax3.bar(range(len(fills_sorted)), qty_sorted, color=colors_bar, alpha=0.7, edgecolor="white")
    ax3.set_xlabel("Fill Sequence")
    ax3.set_ylabel("Filled Qty")
    ax3.set_title("Fill Sizes (green=BUY, red=SELL)")

    fee_sorted = pd.to_numeric(fills_sorted.get("fee", 0), errors="coerce").fillna(0.0)
    ax3b = ax3.twinx()
    ax3b.plot(range(len(fills_sorted)), fee_sorted.cumsum(), color="#e67e22", linewidth=1.5, label="cumulative fee")
    ax3b.set_ylabel("Cumulative Fee (KRW)", color="#e67e22")
    ax3b.tick_params(axis="y", labelcolor="#e67e22")
    if len(fills_sorted) > 0:
        ax3b.legend(loc="upper left", fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# Figure 4: Summary Dashboard
# ──────────────────────────────────────────────────────────────────────

def plot_summary_dashboard(data: dict, run_dir: Path, show: bool = True) -> Path:
    """Single-page metric summary dashboard."""
    out_path = run_dir / "plots" / "dashboard.png"
    summary: dict = data.get("summary", {})
    if not summary:
        return _save_placeholder_plot(
            out_path,
            title="Performance Dashboard",
            message="No summary.json",
            show=show,
            figsize=(12, 5),
        )

    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    fig.suptitle("Performance Dashboard", fontsize=14, fontweight="bold")

    # ── PnL breakdown (pie) ──
    ax = axes[0, 0]
    costs = {
        "Commission": abs(_summary_float(summary, "total_commission", 0.0)),
        "Slippage": abs(_summary_float(summary, "total_slippage", 0.0)),
        "Impact": abs(_summary_float(summary, "total_impact", 0.0)),
    }
    nonzero = {k: v for k, v in costs.items() if v > 0}
    if nonzero:
        ax.pie(nonzero.values(), labels=nonzero.keys(), autopct="%1.1f%%", colors=["#3498db", "#e67e22", "#e74c3c"], startangle=90)
    else:
        ax.text(0.5, 0.5, "No cost data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Cost Breakdown")

    # ── Risk metrics (bar) ──
    ax = axes[0, 1]
    risk_keys = ["sharpe_ratio", "sortino_ratio"]
    risk_vals = [_summary_float(summary, k, 0.0) for k in risk_keys]
    risk_labels = ["Sharpe", "Sortino"]
    colors = ["#2ecc71" if v > 0 else "#e74c3c" for v in risk_vals]
    ax.barh(risk_labels, risk_vals, color=colors, edgecolor="white", height=0.5)
    for i, v in enumerate(risk_vals):
        ax.text(v + (0.05 if v >= 0 else -0.25), i, f"{v:.2f}", va="center", fontsize=10)
    ax.set_title("Risk Ratios")
    ax.axvline(0, color="gray", linewidth=0.5)

    # ── Execution metrics (bar) ──
    ax = axes[0, 2]
    exec_keys = ["is_bps", "avg_slippage_bps", "avg_market_impact_bps", "timing_score"]
    exec_vals = [_summary_float(summary, k, 0.0) for k in exec_keys]
    exec_labels = ["IS (bps)", "Slippage\n(bps)", "Impact\n(bps)", "Timing\nScore"]
    colors = ["#3498db", "#3498db", "#3498db", "#2ecc71"]
    ax.bar(exec_labels, exec_vals, color=colors, edgecolor="white")
    for i, v in enumerate(exec_vals):
        ax.text(i, v + 0.05, f"{v:.2f}", ha="center", fontsize=9)
    ax.set_title("Execution Quality")
    ax.axhline(0, color="gray", linewidth=0.5)

    # ── Attribution (waterfall) ──
    ax = axes[1, 0]
    attr_keys = ["alpha_contribution", "execution_contribution", "cost_contribution", "timing_contribution"]
    attr_labels = ["Alpha", "Execution", "Cost", "Timing"]
    attr_vals = [_summary_float(summary, k, 0.0) for k in attr_keys]
    cumulative = np.cumsum([0.0] + attr_vals)
    colors = ["#2ecc71" if v >= 0 else "#e74c3c" for v in attr_vals]
    for i, (val, base) in enumerate(zip(attr_vals, cumulative[:-1])):
        ax.bar(i, val, bottom=base, color=colors[i], edgecolor="white", width=0.6)
    ax.set_xticks(range(len(attr_labels)))
    ax.set_xticklabels(attr_labels, fontsize=9)
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.set_title("Attribution Waterfall")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x / 1e6:.1f}M"))

    # ── Key numbers (text) ──
    ax = axes[1, 1]
    ax.axis("off")
    lines = [
        f"Net PnL:          {_summary_float(summary, 'net_pnl', 0):>14,.0f} KRW",
        f"Realized PnL:     {_summary_float(summary, 'total_realized_pnl', 0):>14,.0f} KRW",
        f"Total Cost:       {sum(costs.values()):>14,.0f} KRW",
        f"─────────────────────────────────",
        f"Max Drawdown:     {_summary_float(summary, 'max_drawdown', 0):>14.6f}",
        f"VaR (95%):        {_summary_float(summary, 'var_95', 0):>14,.0f} KRW",
        f"ES (95%):         {_summary_float(summary, 'expected_shortfall_95', 0):>14,.0f} KRW",
        f"─────────────────────────────────",
        f"Fill Rate:        {_summary_float(summary, 'fill_rate', 0):>14.2f}",
        f"Annualized Vol:   {_summary_float(summary, 'annualized_vol', 0):>14,.4f}",
        f"Turnover (ann.):  {_summary_float(summary, 'annualized_turnover', 0):>14.2f}",
        f"Avg Latency:      {_summary_float(summary, 'avg_latency_ms', 0):>14.2f} ms",
    ]
    ax.text(
        0.05,
        0.95,
        "\n".join(lines),
        transform=ax.transAxes,
        fontfamily="monospace",
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#ecf0f1", alpha=0.8),
    )
    ax.set_title("Key Metrics")

    # ── Fills per side (bar) ──
    ax = axes[1, 2]
    fills: pd.DataFrame = data.get("fills", pd.DataFrame())
    if not fills.empty and "side" in fills.columns:
        side_counts = fills["side"].astype(str).str.upper().value_counts()
        side_qty = pd.to_numeric(fills.get("filled_qty", 0), errors="coerce").fillna(0.0)
        side_qty = side_qty.groupby(fills["side"].astype(str).str.upper()).sum()
        x = np.arange(len(side_counts))
        width = 0.35
        ax.bar(
            x - width / 2,
            side_counts.values,
            width,
            label="# fills",
            color=["#2ecc71" if s == "BUY" else "#e74c3c" for s in side_counts.index],
        )
        ax2 = ax.twinx()
        ax2.bar(
            x + width / 2,
            side_qty.reindex(side_counts.index).values,
            width,
            label="total qty",
            alpha=0.5,
            color=["#27ae60" if s == "BUY" else "#c0392b" for s in side_counts.index],
        )
        ax.set_xticks(x)
        ax.set_xticklabels(side_counts.index)
        ax.set_ylabel("Fill Count")
        ax2.set_ylabel("Total Qty")
        ax.legend(loc="upper left", fontsize=8)
        ax2.legend(loc="upper right", fontsize=8)
    else:
        ax.text(0.5, 0.5, "No fills.csv", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Fills by Side")

    plt.tight_layout(rect=[0, 0, 1, 0.95])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# Figure 5: Intraday Cumulative Profit
# ──────────────────────────────────────────────────────────────────────

def _build_intraday_cumulative_profit_series(data: dict) -> pd.Series:
    """Build timestamp-indexed cumulative PnL series from run artifacts."""
    pnl_entries: pd.DataFrame = data.get("pnl_entries", pd.DataFrame())
    pnl_series_df: pd.DataFrame = data.get("pnl_series", pd.DataFrame())

    if not pnl_entries.empty and "timestamp" in pnl_entries.columns:
        entries = pnl_entries.copy().sort_values("timestamp")
        ts = pd.to_datetime(entries["timestamp"], errors="coerce")
        entries = entries.loc[ts.notna()].copy()
        entries["timestamp"] = ts.loc[ts.notna()]
        if entries.empty:
            return pd.Series(dtype=float, name="cumulative_net_pnl")

        if "cumulative_net_pnl" in entries.columns:
            return pd.Series(entries["cumulative_net_pnl"].astype(float).values, index=pd.DatetimeIndex(entries["timestamp"]), name="cumulative_net_pnl")

        if {"realized_pnl", "unrealized_pnl"}.issubset(entries.columns):
            realized = pd.to_numeric(entries["realized_pnl"], errors="coerce").fillna(0.0).cumsum()
            commission = pd.to_numeric(entries.get("commission_cost", 0.0), errors="coerce").fillna(0.0)
            tax = pd.to_numeric(entries.get("tax_cost", 0.0), errors="coerce").fillna(0.0)
            cum_cost = (commission + tax).cumsum()
            unrealized = pd.to_numeric(entries["unrealized_pnl"], errors="coerce").fillna(0.0)
            values = realized - cum_cost + unrealized
            return pd.Series(values.values, index=pd.DatetimeIndex(entries["timestamp"]), name="cumulative_net_pnl")

        if "net_pnl" in entries.columns:
            values = pd.to_numeric(entries["net_pnl"], errors="coerce").fillna(0.0).cumsum()
            return pd.Series(values.values, index=pd.DatetimeIndex(entries["timestamp"]), name="cumulative_net_pnl")

    if not pnl_series_df.empty:
        if "cumulative_net_pnl" in pnl_series_df.columns:
            series = pd.to_numeric(pnl_series_df["cumulative_net_pnl"], errors="coerce")
        else:
            series = pd.to_numeric(pnl_series_df.iloc[:, 0], errors="coerce")
        series = series.dropna()
        series.index = pd.DatetimeIndex(series.index)
        return series.rename("cumulative_net_pnl")

    return pd.Series(dtype=float, name="cumulative_net_pnl")


def _build_intraday_summary_lines(summary: dict[str, Any]) -> list[str]:
    """Build compact intraday key-metrics summary lines for the chart footer."""
    if not summary:
        return ["Summary metrics unavailable"]

    def _fmt_metric(key: str, *, ndigits: int = 2, suffix: str = "") -> str:
        val = _summary_float(summary, key)
        if np.isnan(val):
            return "N/A"
        return f"{_fmt_value(val, ndigits=ndigits)}{suffix}"

    line1 = " | ".join(
        [
            f"Net PnL: {_fmt_metric('net_pnl', ndigits=0, suffix=' KRW')}",
            f"Sharpe: {_fmt_metric('sharpe_ratio', ndigits=2)}",
            f"Max DD: {_fmt_metric('max_drawdown', ndigits=4)}",
            f"Fill Rate: {_fmt_metric('fill_rate', ndigits=2)}",
            f"Cancel Rate: {_fmt_metric('cancel_rate', ndigits=2)}",
        ]
    )

    optional_specs = [
        ("Commission", "total_commission", 0, " KRW"),
        ("Slippage", "total_slippage", 0, " KRW"),
        ("Maker Fill", "maker_fill_ratio", 2, ""),
        ("Avg Latency", "avg_latency_ms", 2, " ms"),
    ]
    optional_metrics = []
    for label, key, ndigits, suffix in optional_specs:
        value = _summary_float(summary, key)
        if np.isnan(value):
            continue
        optional_metrics.append(f"{label}: {_fmt_value(value, ndigits=ndigits)}{suffix}")

    lines = [line1]
    if optional_metrics:
        lines.append(" | ".join(optional_metrics))
    return lines


def plot_intraday_cumulative_profit(data: dict, run_dir: Path, show: bool = True) -> Path:
    """Line chart of intraday cumulative profit over 09:00–16:00."""
    series = _build_intraday_cumulative_profit_series(data).sort_index()
    summary = data.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}

    fig, ax = plt.subplots(figsize=(14, 5))
    ax.set_title("Intraday Cumulative Profit", fontsize=12, fontweight="bold")
    ax.set_xlabel("Time")
    ax.set_ylabel("Cumulative Profit (KRW)")
    ax.grid(True, alpha=0.25, linestyle="--")
    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))

    if not series.empty:
        ax.plot(series.index, series.values, color="#1f77b4", linewidth=1.8)
        session_day = series.index.min().normalize()
    else:
        ax.text(
            0.5,
            0.5,
            "No PnL time series available",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=11,
            color="dimgray",
        )
        session_day = pd.Timestamp.today().normalize()

    session_start = session_day + pd.Timedelta(hours=9)
    session_end = session_day + pd.Timedelta(hours=16)
    ax.set_xlim(session_start, session_end)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(9, 17), interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    metrics_text = "\n".join(_build_intraday_summary_lines(summary))
    fig.tight_layout(rect=[0, 0.15, 1, 1])
    fig.text(
        0.5,
        0.03,
        metrics_text,
        ha="center",
        va="bottom",
        fontsize=8.7,
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#f3f4f6", edgecolor="#d1d5db", alpha=0.92),
    )

    out_path = run_dir / "plots" / "intraday_cumulative_profit.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# Figure 6: Trade Timeline (신규)
# ──────────────────────────────────────────────────────────────────────

def plot_trade_timeline(data: dict, run_dir: Path, show: bool = True) -> Path:
    """Trade-level timeline: market price + fills + signals."""
    fills: pd.DataFrame = _to_datetime_column(data.get("fills", pd.DataFrame()))
    quotes: pd.DataFrame = _to_datetime_column(data.get("market_quotes", pd.DataFrame()))
    signals: pd.DataFrame = _to_datetime_column(data.get("signals", pd.DataFrame()))
    orders: pd.DataFrame = data.get("orders", pd.DataFrame())

    out_path = run_dir / "plots" / "trade_timeline.png"

    if fills.empty and quotes.empty and signals.empty:
        return _save_placeholder_plot(
            out_path,
            title="Trade Timeline",
            message="No fills.csv / market_quotes.csv / signals.csv",
            show=show,
            figsize=(14, 5),
        )

    fig, axes = plt.subplots(2, 1, figsize=(16, 10), sharex=True, gridspec_kw={"height_ratios": [3.0, 1.8]})
    ax1, ax2 = axes

    # Main panel: price + fills
    if not quotes.empty:
        quotes = quotes.sort_values("timestamp")
        if "mid_price" in quotes.columns:
            mid = pd.to_numeric(quotes["mid_price"], errors="coerce")
            if mid.notna().any():
                ax1.plot(quotes["timestamp"], mid, color="#2c3e50", linewidth=1.1, label="mid_price", zorder=2)
        if "best_bid" in quotes.columns:
            bid = pd.to_numeric(quotes["best_bid"], errors="coerce")
            if bid.notna().any():
                ax1.plot(quotes["timestamp"], bid, color="#27ae60", linewidth=0.8, alpha=0.45, label="best_bid", zorder=1)
        if "best_ask" in quotes.columns:
            ask = pd.to_numeric(quotes["best_ask"], errors="coerce")
            if ask.notna().any():
                ax1.plot(quotes["timestamp"], ask, color="#c0392b", linewidth=0.8, alpha=0.45, label="best_ask", zorder=1)

    if not fills.empty and "fill_price" in fills.columns and "side" in fills.columns:
        fills = fills.sort_values("timestamp")
        qty = pd.to_numeric(fills.get("filled_qty", 10.0), errors="coerce").fillna(10.0).abs()
        size = np.clip(qty.values * 0.9, 20, 220)
        buy_mask = fills["side"].astype(str).str.upper() == "BUY"
        sell_mask = fills["side"].astype(str).str.upper() == "SELL"

        if buy_mask.any():
            ax1.scatter(
                fills.loc[buy_mask, "timestamp"],
                pd.to_numeric(fills.loc[buy_mask, "fill_price"], errors="coerce"),
                marker="^",
                color="#2ecc71",
                edgecolors="black",
                linewidths=0.4,
                s=size[buy_mask.values],
                alpha=0.9,
                label="BUY fill",
                zorder=5,
            )
        if sell_mask.any():
            ax1.scatter(
                fills.loc[sell_mask, "timestamp"],
                pd.to_numeric(fills.loc[sell_mask, "fill_price"], errors="coerce"),
                marker="v",
                color="#e74c3c",
                edgecolors="black",
                linewidths=0.4,
                s=size[sell_mask.values],
                alpha=0.9,
                label="SELL fill",
                zorder=5,
            )

        # Optional fill-time vertical markers
        unique_fill_ts = fills["timestamp"].dropna().drop_duplicates()
        max_vlines = 800
        if len(unique_fill_ts) > max_vlines:
            unique_fill_ts = unique_fill_ts.iloc[:: max(1, len(unique_fill_ts) // max_vlines)]
        for ts in unique_fill_ts:
            ax1.axvline(ts, color="#7f8c8d", alpha=0.05, linewidth=0.6, zorder=0)

    arrival_mid_series = _extract_arrival_mid_series(orders, fills)
    if not arrival_mid_series.empty:
        ax1.plot(
            arrival_mid_series["timestamp"],
            arrival_mid_series["arrival_mid"],
            color="#3498db",
            linewidth=1.0,
            linestyle="--",
            alpha=0.85,
            label="arrival_mid",
            zorder=3,
        )

    ax1.set_title("Trade Timeline — Market Price + Fills + Arrival Mid", fontsize=11)
    ax1.set_ylabel("Price (KRW)")
    ax1.grid(True, alpha=0.2, linestyle="--")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    if ax1.get_legend_handles_labels()[0]:
        ax1.legend(loc="upper left", fontsize=8, ncol=2)

    # Secondary panel: signal score (+ confidence)
    if not signals.empty and "score" in signals.columns:
        sig = signals.sort_values("timestamp")
        score = pd.to_numeric(sig["score"], errors="coerce").fillna(0.0)
        ax2.plot(sig["timestamp"], score, color="#1f77b4", linewidth=0.9, label="signal score")
        ax2.fill_between(sig["timestamp"], score, 0.0, color="#1f77b4", alpha=0.18)
        ax2.axhline(0, color="gray", linewidth=0.6, linestyle="--")

        if "confidence" in sig.columns:
            conf = pd.to_numeric(sig["confidence"], errors="coerce")
            ax2b = ax2.twinx()
            ax2b.plot(sig["timestamp"], conf, color="#f39c12", linewidth=0.8, alpha=0.65, label="confidence")
            ax2b.set_ylabel("Confidence", color="#f39c12")
            ax2b.set_ylim(0, 1.1)
            ax2b.tick_params(axis="y", labelcolor="#f39c12")
    else:
        ax2.text(0.5, 0.5, "No signal score data", transform=ax2.transAxes, ha="center", va="center", color="dimgray")

    if not fills.empty and "timestamp" in fills.columns:
        unique_fill_ts = fills["timestamp"].dropna().drop_duplicates()
        max_vlines = 500
        if len(unique_fill_ts) > max_vlines:
            unique_fill_ts = unique_fill_ts.iloc[:: max(1, len(unique_fill_ts) // max_vlines)]
        for ts in unique_fill_ts:
            ax2.axvline(ts, color="#7f8c8d", alpha=0.05, linewidth=0.6)

    ax2.set_title("Signal Timeline", fontsize=10)
    ax2.set_ylabel("Signal Score")
    ax2.set_xlabel("Time")
    ax2.grid(True, alpha=0.2, linestyle="--")
    if ax2.get_legend_handles_labels()[0]:
        ax2.legend(loc="upper left", fontsize=8)

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=30)
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# Figure 7: Equity & Risk (신규)
# ──────────────────────────────────────────────────────────────────────

def plot_equity_risk(data: dict, run_dir: Path, show: bool = True) -> Path:
    """Equity/risk chart: cumulative PnL, underwater drawdown, rolling volatility/sharpe proxy."""
    out_path = run_dir / "plots" / "equity_risk.png"
    summary: dict = data.get("summary", {})

    series = _build_intraday_cumulative_profit_series(data).sort_index()
    if series.empty:
        return _save_placeholder_plot(
            out_path,
            title="Equity & Risk",
            message="No pnl_series.csv or pnl_entries.csv",
            show=show,
            figsize=(14, 6),
        )

    window = _compute_rolling_window(len(series))

    fig, axes = plt.subplots(3, 1, figsize=(16, 11), sharex=True, gridspec_kw={"height_ratios": [2.3, 1.6, 1.6]})
    ax1, ax2, ax3 = axes

    sharpe = _summary_float(summary, "sharpe_ratio")
    max_dd = _summary_float(summary, "max_drawdown")
    ann_vol = _summary_float(summary, "annualized_vol")

    sharpe_s = "N/A" if np.isnan(sharpe) else f"{sharpe:.2f}"
    mdd_s = "N/A" if np.isnan(max_dd) else f"{max_dd:.4f}"
    avol_s = "N/A" if np.isnan(ann_vol) else f"{ann_vol:.4f}"

    fig.suptitle(
        f"Equity & Risk — Sharpe={sharpe_s}  MaxDD={mdd_s}  Annualized Vol={avol_s}",
        fontsize=13,
        fontweight="bold",
    )

    # Panel 1: cumulative PnL / equity curve
    ax1.plot(series.index, series.values, color="#1f77b4", linewidth=1.4, label="cumulative_net_pnl")
    ax1.fill_between(series.index, series.values, 0.0, where=(series.values >= 0), color="#2ecc71", alpha=0.18)
    ax1.fill_between(series.index, series.values, 0.0, where=(series.values < 0), color="#e74c3c", alpha=0.18)
    ax1.axhline(0, color="gray", linewidth=0.7, linestyle="--")
    ax1.set_ylabel("Cumulative PnL (KRW)")
    ax1.set_title("Cumulative PnL / Equity Curve", fontsize=10)
    ax1.grid(True, alpha=0.2, linestyle="--")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax1.legend(loc="upper left", fontsize=8)

    # Panel 2: underwater drawdown (%)
    rolling_max = series.cummax()
    denominator = rolling_max.replace(0.0, np.nan)
    drawdown_pct = (series / denominator - 1.0) * 100.0
    drawdown_pct = drawdown_pct.fillna(0.0)

    ax2.fill_between(series.index, drawdown_pct.values, 0.0, where=(drawdown_pct.values <= 0.0), color="#e74c3c", alpha=0.28)
    ax2.plot(series.index, drawdown_pct.values, color="#c0392b", linewidth=1.0)
    ax2.axhline(0, color="gray", linewidth=0.7, linestyle="--")
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_title("Underwater Drawdown", fontsize=10)
    ax2.grid(True, alpha=0.2, linestyle="--")

    # Panel 3: rolling volatility + rolling sharpe proxy
    step_pnl = series.diff().fillna(0.0)
    rolling_vol = step_pnl.rolling(window).std()
    rolling_sharpe = step_pnl.rolling(window).mean() / rolling_vol.replace(0.0, np.nan)
    rolling_sharpe = rolling_sharpe * np.sqrt(window)

    ax3.plot(series.index, rolling_vol.values, color="#8e44ad", linewidth=1.0, label=f"rolling volatility (window={window})")
    ax3.set_ylabel("Volatility (KRW)")
    ax3.set_title("Rolling Volatility / Sharpe Proxy", fontsize=10)
    ax3.grid(True, alpha=0.2, linestyle="--")

    ax3b = ax3.twinx()
    ax3b.plot(series.index, rolling_sharpe.values, color="#f39c12", linewidth=1.0, alpha=0.85, label="rolling sharpe proxy")
    ax3b.set_ylabel("Sharpe Proxy", color="#f39c12")
    ax3b.tick_params(axis="y", labelcolor="#f39c12")

    handles1, labels1 = ax3.get_legend_handles_labels()
    handles2, labels2 = ax3b.get_legend_handles_labels()
    if handles1 or handles2:
        ax3.legend(handles1 + handles2, labels1 + labels2, loc="upper left", fontsize=8)

    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax3.set_xlabel("Time")
    fig.autofmt_xdate(rotation=30)
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# Figure 8: Realism Dashboard (신규)
# ──────────────────────────────────────────────────────────────────────

def plot_realism_dashboard(data: dict, run_dir: Path, show: bool = True) -> Path:
    """One-page realism diagnostics dashboard from realism_diagnostics.json + summary.json."""
    out_path = run_dir / "plots" / "realism_dashboard.png"

    summary: dict = data.get("summary", {})
    diagnostics: dict = data.get("realism_diagnostics", {})

    if not summary and not diagnostics:
        return _save_placeholder_plot(
            out_path,
            title="Realism Dashboard",
            message="No summary.json / realism_diagnostics.json",
            show=show,
            figsize=(14, 6),
        )

    obs = diagnostics.get("observation_lag", {}) if isinstance(diagnostics, dict) else {}
    dec = diagnostics.get("decision_latency", {}) if isinstance(diagnostics, dict) else {}
    tick = diagnostics.get("tick_time", {}) if isinstance(diagnostics, dict) else {}
    queue = diagnostics.get("queue", {}) if isinstance(diagnostics, dict) else {}
    latency = diagnostics.get("latency", {}) if isinstance(diagnostics, dict) else {}
    cancel = diagnostics.get("cancel_reasons", {}) if isinstance(diagnostics, dict) else {}
    lifecycle = diagnostics.get("lifecycle", {}) if isinstance(diagnostics, dict) else {}

    fig, axes = plt.subplots(3, 2, figsize=(18, 12))
    fig.suptitle(
        f"Realism Diagnostics Dashboard — NetPnL={_summary_float(summary, 'net_pnl', 0):,.0f} KRW  "
        f"CancelRate={_summary_float(summary, 'cancel_rate', 0):.4f}",
        fontsize=14,
        fontweight="bold",
    )

    # 1) observation / decision
    ax = axes[0, 0]
    ax.axis("off")
    obs_lines = [
        f"configured_market_data_delay_ms: {_fmt_value(obs.get('configured_market_data_delay_ms'))}",
        f"avg_observation_staleness_ms: {_fmt_value(obs.get('avg_observation_staleness_ms'))}",
        f"configured_decision_compute_ms: {_fmt_value(dec.get('configured_decision_compute_ms', obs.get('configured_decision_compute_ms')))}",
        f"decision_latency_enabled: {dec.get('decision_latency_enabled', obs.get('decision_latency_enabled', 'N/A'))}",
        f"effective_delay_ms: {_fmt_value(obs.get('effective_delay_ms'))}",
        f"avg_decision_state_age_ms: {_fmt_value(dec.get('avg_decision_state_age_ms', obs.get('avg_decision_state_age_ms')))}",
    ]
    ax.text(
        0.03,
        0.97,
        "Observation / Decision\n\n" + "\n".join(obs_lines),
        transform=ax.transAxes,
        va="top",
        fontsize=10,
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#ecf0f1", alpha=0.95),
    )

    # 2) tick / history
    ax = axes[0, 1]
    ax.axis("off")
    tick_lines = [
        f"resample_interval: {tick.get('resample_interval', obs.get('resample_interval', 'N/A'))}",
        f"canonical_tick_interval_ms: {_fmt_value(tick.get('canonical_tick_interval_ms', obs.get('canonical_tick_interval_ms')))}",
        f"state_history_max_len: {_fmt_value(tick.get('state_history_max_len', obs.get('state_history_max_len')))}",
        f"strategy_runtime_lookback_ticks: {_fmt_value(tick.get('strategy_runtime_lookback_ticks', obs.get('strategy_runtime_lookback_ticks')))}",
        f"history_safety_buffer_ticks: {_fmt_value(tick.get('history_safety_buffer_ticks', obs.get('history_safety_buffer_ticks')))}",
    ]
    ax.text(
        0.03,
        0.97,
        "Tick / History\n\n" + "\n".join(tick_lines),
        transform=ax.transAxes,
        va="top",
        fontsize=10,
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#ecf0f1", alpha=0.95),
    )

    # 3) queue
    ax = axes[1, 0]
    queue_metrics = [
        ("queue_wait_ms", _safe_float(queue.get("queue_wait_ms"), np.nan)),
        ("queue_wait_ticks", _safe_float(queue.get("queue_wait_ticks"), np.nan)),
        ("blocked_miss_count", _safe_float(queue.get("blocked_miss_count"), np.nan)),
        ("ready_but_not_filled_count", _safe_float(queue.get("ready_but_not_filled_count"), np.nan)),
    ]
    queue_valid = [(k, v) for k, v in queue_metrics if not np.isnan(v)]

    if queue_valid:
        labels = [k for k, _ in queue_valid]
        values = [v for _, v in queue_valid]
        ax.barh(labels, values, color=["#3498db", "#1abc9c", "#e67e22", "#9b59b6"][: len(values)], alpha=0.85)
        for i, v in enumerate(values):
            ax.text(v + (0.01 * max(values) if max(values) > 0 else 0.1), i, f"{v:.3f}", va="center", fontsize=9)
    else:
        ax.text(0.5, 0.5, "No queue diagnostics", ha="center", va="center", transform=ax.transAxes)
    ax.set_title(
        f"Queue — model={queue.get('queue_model', 'N/A')}  pos={queue.get('queue_position_assumption', 'N/A')}",
        fontsize=10,
    )
    ax.grid(True, axis="x", alpha=0.2, linestyle="--")

    # 4) cancel reasons
    ax = axes[1, 1]
    shares = {}
    if isinstance(cancel, dict):
        if isinstance(cancel.get("shares"), dict):
            shares = cancel.get("shares", {})
        elif isinstance(cancel.get("counts"), dict):
            counts = cancel.get("counts", {})
            total = sum(_safe_float(v, 0.0) for v in counts.values())
            shares = {k: (_safe_float(v, 0.0) / total if total > 0 else 0.0) for k, v in counts.items()}

    ordered_keys = ["timeout", "adverse_selection", "stale_price", "max_reprices_reached", "micro_event_block", "unknown"]
    vals = [float(shares.get(k, 0.0)) for k in ordered_keys]

    if np.sum(vals) > 0:
        ax.barh(ordered_keys, vals, color="#e74c3c", alpha=0.8)
        for i, v in enumerate(vals):
            ax.text(v + 0.005, i, f"{v:.1%}", va="center", fontsize=9)
        ax.set_xlim(0, max(vals) * 1.25)
    else:
        ax.text(0.5, 0.5, "No cancel reason aggregate", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Cancel Reason Mix (share)", fontsize=10)
    ax.grid(True, axis="x", alpha=0.2, linestyle="--")

    # 5) cancel / latency
    ax = axes[2, 0]
    latency_metrics = [
        ("submit_ms", _safe_float(latency.get("sampled_avg_submit_latency_ms"), np.nan)),
        ("cancel_ms", _safe_float(latency.get("sampled_avg_cancel_latency_ms"), np.nan)),
        ("fill_ms", _safe_float(latency.get("sampled_avg_fill_latency_ms"), np.nan)),
    ]
    latency_valid = [(k, v) for k, v in latency_metrics if not np.isnan(v)]
    if latency_valid:
        labels = [k for k, _ in latency_valid]
        values = [v for _, v in latency_valid]
        ax.bar(labels, values, color=["#3498db", "#e67e22", "#2ecc71"][: len(values)], alpha=0.85)
        for i, v in enumerate(values):
            ax.text(i, v + (0.01 * max(values) if max(values) > 0 else 0.1), f"{v:.3f}", ha="center", fontsize=9)
    else:
        ax.text(0.5, 0.7, "No sampled latency aggregate", ha="center", va="center", transform=ax.transAxes)

    extra_lines = [
        f"cancel_pending_count={int(_safe_float(latency.get('cancel_pending_count'), 0.0))}",
        f"fills_before_cancel_effective={int(_safe_float(latency.get('fills_before_cancel_effective_count'), 0.0))}",
        f"avg_cancel_effective_lag_ms={_fmt_value(latency.get('avg_cancel_effective_lag_ms'))}",
        f"configured_submit/cancel/fill(ms)={_fmt_value(latency.get('configured_order_submit_ms'))}/"
        f"{_fmt_value(latency.get('configured_cancel_ms'))}/"
        f"{_fmt_value(latency.get('sampled_avg_fill_latency_ms'))}",
    ]
    ax.text(
        0.02,
        0.02,
        "\n".join(extra_lines),
        transform=ax.transAxes,
        fontsize=9,
        fontfamily="monospace",
        va="bottom",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#f8f9fa", alpha=0.85),
    )
    ax.set_title("Cancel / Latency", fontsize=10)
    ax.grid(True, axis="y", alpha=0.2, linestyle="--")

    # 6) lifecycle hotspots
    ax = axes[2, 1]
    ax.axis("off")
    lifecycle_lines = [
        f"signal_count: {_fmt_value(lifecycle.get('signal_count', summary.get('signal_count')))}",
        f"parent_order_count: {_fmt_value(lifecycle.get('parent_order_count', summary.get('parent_order_count')))}",
        f"child_order_count: {_fmt_value(lifecycle.get('child_order_count', summary.get('child_order_count')))}",
        f"n_fills: {_fmt_value(lifecycle.get('n_fills', summary.get('n_fills')))}",
        f"cancel_rate: {_fmt_value(lifecycle.get('cancel_rate', summary.get('cancel_rate')))}",
        f"avg_child_lifetime_seconds: {_fmt_value(lifecycle.get('avg_child_lifetime_seconds', summary.get('avg_child_lifetime_seconds')))}",
        f"max_children_per_parent: {_fmt_value(lifecycle.get('max_children_per_parent'))}",
        f"max_cancelled_children_per_parent: {_fmt_value(lifecycle.get('max_cancelled_children_per_parent'))}",
        f"top_parent_by_children: {lifecycle.get('top_parent_by_children', 'N/A')}",
    ]
    ax.text(
        0.03,
        0.97,
        "Lifecycle / Hotspots\n\n" + "\n".join(lifecycle_lines),
        transform=ax.transAxes,
        va="top",
        fontsize=10,
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#ecf0f1", alpha=0.95),
    )

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if show:
        plt.show()
    plt.close(fig)
    return out_path


# ──────────────────────────────────────────────────────────────────────
# 공개 API (for report_builder integration)
# ──────────────────────────────────────────────────────────────────────

def generate_all_plots(run_dir: str | Path, show: bool = False) -> list[Path]:
    """Generate all visualization plots for a backtest run directory.

    Returns list of saved plot file paths.
    """
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    data = load_run(run_dir)

    plotters = [
        plot_overview,
        plot_signal_analysis,
        plot_execution_quality,
        plot_summary_dashboard,
        plot_intraday_cumulative_profit,
        plot_trade_timeline,
        plot_equity_risk,
        plot_realism_dashboard,
    ]

    paths: list[Path] = []
    for plot_fn in plotters:
        try:
            paths.append(plot_fn(data, run_dir, show=show))
        except Exception as exc:  # noqa: BLE001
            print(f"Plot generation failed: {plot_fn.__name__}: {exc}", file=sys.stderr)

    return paths


def generate_report_plots(run_dir: str | Path, show: bool = False) -> list[Path]:
    """Generate the default plot subset used by the backtest report builder."""
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    data = load_run(run_dir)

    plotters = [
        plot_summary_dashboard,
        plot_intraday_cumulative_profit,
        plot_trade_timeline,
    ]

    paths: list[Path] = []
    for plot_fn in plotters:
        try:
            paths.append(plot_fn(data, run_dir, show=show))
        except Exception as exc:  # noqa: BLE001
            print(f"Plot generation failed: {plot_fn.__name__}: {exc}", file=sys.stderr)

    return paths


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize backtest results.")
    parser.add_argument("--run-dir", required=True, help="Path to backtest run directory")
    parser.add_argument("--no-show", action="store_true", help="Save plots without displaying")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        # try as run_id under default output dir
        alt = Path("outputs/backtests") / args.run_dir
        if alt.exists():
            run_dir = alt
        else:
            print(f"Error: {run_dir} not found", file=sys.stderr)
            sys.exit(1)

    paths = generate_all_plots(run_dir, show=not args.no_show)
    print(f"\nGenerated {len(paths)} plots in {run_dir / 'plots'}")


if __name__ == "__main__":
    main()
