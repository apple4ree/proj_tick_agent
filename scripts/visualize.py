"""
visualize.py
------------
Backtest 결과 시각화 스크립트.

시그널, 체결, PnL, 비용 등을 한눈에 파악할 수 있는 차트를 생성합니다.

사용법:
    cd /home/dgu/tick/proj_rl_agent
    python scripts/visualize.py --run-dir outputs/backtests/<run_id>
    python scripts/visualize.py --run-dir outputs/backtests/<run_id> --no-show  # 파일만 저장
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

# ──────────────────────────────────────────────────────────────────────
# 데이터 로딩
# ──────────────────────────────────────────────────────────────────────

def load_run(run_dir: Path) -> dict[str, pd.DataFrame | dict]:
    """Load all CSV/JSON artifacts from a backtest run directory."""
    data: dict[str, pd.DataFrame | dict] = {}

    signals_path = run_dir / "signals.csv"
    if signals_path.exists():
        df = pd.read_csv(signals_path, parse_dates=["timestamp"])
        data["signals"] = df

    fills_path = run_dir / "fills.csv"
    if fills_path.exists():
        df = pd.read_csv(fills_path, parse_dates=["timestamp"])
        data["fills"] = df

    orders_path = run_dir / "orders.csv"
    if orders_path.exists():
        data["orders"] = pd.read_csv(orders_path)

    pnl_path = run_dir / "pnl_series.csv"
    if pnl_path.exists():
        df = pd.read_csv(pnl_path, index_col=0, parse_dates=True)
        df.columns = ["cumulative_net_pnl"]
        data["pnl_series"] = df

    pnl_entries_path = run_dir / "pnl_entries.csv"
    if pnl_entries_path.exists():
        df = pd.read_csv(pnl_entries_path, parse_dates=["timestamp"])
        data["pnl_entries"] = df

    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        with open(summary_path, encoding="utf-8") as fh:
            data["summary"] = json.load(fh)

    return data


# ──────────────────────────────────────────────────────────────────────
# Figure 1: Main Overview (4 panels)
# ──────────────────────────────────────────────────────────────────────

def plot_overview(data: dict, run_dir: Path, show: bool = True) -> Path:
    """4-panel overview: price+fills, signal score, cumulative PnL, per-fill costs."""
    signals: pd.DataFrame = data.get("signals", pd.DataFrame())
    fills: pd.DataFrame = data.get("fills", pd.DataFrame())
    pnl_series: pd.DataFrame = data.get("pnl_series", pd.DataFrame())
    pnl_entries: pd.DataFrame = data.get("pnl_entries", pd.DataFrame())
    summary: dict = data.get("summary", {})

    fig, axes = plt.subplots(4, 1, figsize=(16, 14), sharex=True,
                             gridspec_kw={"height_ratios": [2.5, 2, 2, 1.5]})
    fig.suptitle(
        f"Backtest Overview — {run_dir.name[:12]}…\n"
        f"Sharpe={summary.get('sharpe_ratio', 'N/A'):.2f}  "
        f"Fills={summary.get('n_fills', 'N/A'):.0f}  "
        f"Net PnL={summary.get('net_pnl', 0):,.0f} KRW",
        fontsize=13, fontweight="bold",
    )

    # ── Panel 1: Fill price + arrival mid ──
    ax1 = axes[0]
    if not fills.empty:
        buys = fills[fills["side"] == "BUY"]
        sells = fills[fills["side"] == "SELL"]

        ax1.scatter(buys["timestamp"], buys["fill_price"],
                    marker="^", s=buys["filled_qty"] * 0.8, color="#2ecc71",
                    alpha=0.8, edgecolors="black", linewidths=0.5, label="BUY fill", zorder=5)
        ax1.scatter(sells["timestamp"], sells["fill_price"],
                    marker="v", s=sells["filled_qty"] * 0.8, color="#e74c3c",
                    alpha=0.8, edgecolors="black", linewidths=0.5, label="SELL fill", zorder=5)

        # connect fill prices with a thin line for price trajectory
        fills_sorted = fills.sort_values("timestamp")
        ax1.plot(fills_sorted["timestamp"], fills_sorted["fill_price"],
                 color="#95a5a6", linewidth=0.8, alpha=0.6, zorder=1)

    # overlay arrival_mid from orders if available
    orders: pd.DataFrame = data.get("orders", pd.DataFrame())
    if not orders.empty and "arrival_mid" in orders.columns:
        # orders don't have timestamps; use fill timestamps matched by order_id
        if not fills.empty and "parent_id" in fills.columns:
            mid_map = dict(zip(orders["order_id"], orders["arrival_mid"]))
            first_fill_per_order = fills.sort_values("timestamp").drop_duplicates("parent_id")
            mid_ts = first_fill_per_order["parent_id"].map(mid_map).dropna()
            valid = mid_ts.index
            if len(valid) > 0:
                ax1.plot(first_fill_per_order.loc[valid, "timestamp"],
                         mid_ts.loc[valid],
                         color="#3498db", linewidth=1.0, alpha=0.5,
                         linestyle="--", label="arrival mid", zorder=2)

    ax1.set_ylabel("Price (KRW)")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.set_title("Fill Prices & Arrival Mid", fontsize=10)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))

    # ── Panel 2: Signal score + confidence ──
    ax2 = axes[1]
    if not signals.empty:
        ax2.fill_between(signals["timestamp"], signals["score"],
                         color="#3498db", alpha=0.3, label="signal score")
        ax2.plot(signals["timestamp"], signals["score"],
                 color="#2980b9", linewidth=0.7)

        # confidence as secondary y-axis
        ax2b = ax2.twinx()
        ax2b.scatter(signals["timestamp"], signals["confidence"],
                     s=4, color="#e67e22", alpha=0.4, label="confidence")
        ax2b.set_ylabel("Confidence", fontsize=9, color="#e67e22")
        ax2b.set_ylim(0, 1.1)
        ax2b.tick_params(axis="y", labelcolor="#e67e22")

        # mark fills on signal timeline
        if not fills.empty:
            for _, f in fills.iterrows():
                color = "#2ecc71" if f["side"] == "BUY" else "#e74c3c"
                ax2.axvline(f["timestamp"], color=color, alpha=0.15, linewidth=0.6)

    ax2.axhline(0, color="gray", linewidth=0.5, linestyle="--")
    ax2.set_ylabel("Signal Score")
    ax2.set_title("Signal Score & Confidence (fills = vertical lines)", fontsize=10)
    ax2.legend(loc="upper left", fontsize=8)

    # ── Panel 3: Cumulative PnL ──
    ax3 = axes[2]
    if not pnl_series.empty:
        pnl = pnl_series["cumulative_net_pnl"]
        ax3.fill_between(pnl.index, pnl, where=(pnl >= 0),
                         color="#2ecc71", alpha=0.3)
        ax3.fill_between(pnl.index, pnl, where=(pnl < 0),
                         color="#e74c3c", alpha=0.3)
        ax3.plot(pnl.index, pnl, color="#2c3e50", linewidth=1.0)

        # drawdown shading
        rolling_max = pnl.cummax()
        drawdown = pnl - rolling_max
        ax3_dd = ax3.twinx()
        ax3_dd.fill_between(pnl.index, drawdown, color="#e74c3c", alpha=0.12)
        ax3_dd.set_ylabel("Drawdown (KRW)", fontsize=9, color="#e74c3c")
        ax3_dd.tick_params(axis="y", labelcolor="#e74c3c")

    ax3.axhline(0, color="gray", linewidth=0.5, linestyle="--")
    ax3.set_ylabel("Cumulative Net PnL (KRW)")
    ax3.set_title("Equity Curve & Drawdown", fontsize=10)
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x / 1e6:.1f}M"))

    # ── Panel 4: Per-fill cost breakdown ──
    ax4 = axes[3]
    if not pnl_entries.empty:
        ts = pnl_entries["timestamp"]
        bar_width_ms = 10_000  # thin bars
        ax4.bar(ts, pnl_entries["commission_cost"], width=bar_width_ms,
                label="commission", color="#3498db", alpha=0.7)
        ax4.bar(ts, pnl_entries["slippage_cost"], width=bar_width_ms,
                bottom=pnl_entries["commission_cost"],
                label="slippage", color="#e67e22", alpha=0.7)
        ax4.bar(ts, pnl_entries["impact_cost"], width=bar_width_ms,
                bottom=pnl_entries["commission_cost"] + pnl_entries["slippage_cost"],
                label="impact", color="#e74c3c", alpha=0.7)

    ax4.set_ylabel("Cost (KRW)")
    ax4.set_title("Per-Fill Cost Breakdown", fontsize=10)
    ax4.legend(loc="upper left", fontsize=8)

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
    signals: pd.DataFrame = data.get("signals", pd.DataFrame())
    if signals.empty:
        print("No signals data — skipping signal analysis plot.")
        return run_dir / "plots" / "signal_analysis.png"

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle("Signal Analysis", fontsize=13, fontweight="bold")

    # ── Panel 1: Score distribution ──
    ax1 = axes[0]
    ax1.hist(signals["score"], bins=50, color="#3498db", alpha=0.7, edgecolor="white")
    ax1.axvline(signals["score"].mean(), color="#e74c3c", linestyle="--",
                label=f"mean={signals['score'].mean():.3f}")
    ax1.axvline(0, color="gray", linewidth=0.5)
    ax1.set_xlabel("Signal Score")
    ax1.set_ylabel("Count")
    ax1.set_title("Score Distribution")
    ax1.legend(fontsize=8)

    # ── Panel 2: Regime breakdown (stacked bar) ──
    ax2 = axes[1]
    regime_cols = [c for c in signals.columns if c.startswith("tag_regime_")]
    if regime_cols:
        # pick the first regime tag for visualization
        regime_col = regime_cols[0]
        regime_label = regime_col.replace("tag_regime_", "")
        regime_counts = signals[regime_col].value_counts()
        colors = plt.cm.Set2(np.linspace(0, 1, len(regime_counts)))
        ax2.bar(regime_counts.index, regime_counts.values, color=colors, edgecolor="white")
        ax2.set_xlabel(f"Regime ({regime_label})")
        ax2.set_ylabel("Signal Count")
        ax2.set_title(f"Signals by {regime_label.title()} Regime")
        ax2.tick_params(axis="x", rotation=30)

        # add all regime breakdowns as text
        if len(regime_cols) > 1:
            text_lines = []
            for rc in regime_cols[1:]:
                label = rc.replace("tag_regime_", "")
                top = signals[rc].value_counts().head(3)
                text_lines.append(f"{label}: " + ", ".join(f"{k}({v})" for k, v in top.items()))
            ax2.text(0.02, 0.98, "\n".join(text_lines),
                     transform=ax2.transAxes, fontsize=7, verticalalignment="top",
                     bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.5))
    else:
        ax2.text(0.5, 0.5, "No regime data", ha="center", va="center",
                 transform=ax2.transAxes)
        ax2.set_title("Regime Breakdown")

    # ── Panel 3: Confidence vs Score scatter ──
    ax3 = axes[2]
    sc = ax3.scatter(signals["score"], signals["confidence"],
                     c=signals["expected_return"], cmap="RdYlGn",
                     s=12, alpha=0.6, edgecolors="none")
    ax3.set_xlabel("Signal Score")
    ax3.set_ylabel("Confidence")
    ax3.set_title("Confidence vs Score (color=expected return)")
    plt.colorbar(sc, ax=ax3, label="Expected Return (bps)", shrink=0.8)
    ax3.axvline(0, color="gray", linewidth=0.5)
    ax3.axhline(0, color="gray", linewidth=0.5)

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    out_path = run_dir / "plots" / "signal_analysis.png"
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
    fills: pd.DataFrame = data.get("fills", pd.DataFrame())
    if fills.empty:
        print("No fills data — skipping execution quality plot.")
        return run_dir / "plots" / "execution_quality.png"

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle("Execution Quality", fontsize=13, fontweight="bold")

    # ── Panel 1: Slippage vs 시장 충격 ──
    ax1 = axes[0]
    colors = ["#2ecc71" if s == "BUY" else "#e74c3c" for s in fills["side"]]
    ax1.scatter(fills["slippage_bps"], fills["market_impact_bps"],
                c=colors, s=fills["filled_qty"] * 0.5, alpha=0.6, edgecolors="black",
                linewidths=0.3)
    ax1.axhline(0, color="gray", linewidth=0.5)
    ax1.axvline(0, color="gray", linewidth=0.5)
    ax1.set_xlabel("Slippage (bps)")
    ax1.set_ylabel("시장 충격 (bps)")
    ax1.set_title("Slippage vs Impact (size=qty)")

    # ── Panel 2: 지연 distribution ──
    ax2 = axes[1]
    ax2.hist(fills["latency_ms"], bins=30, color="#9b59b6", alpha=0.7, edgecolor="white")
    ax2.axvline(fills["latency_ms"].mean(), color="#e74c3c", linestyle="--",
                label=f"mean={fills['latency_ms'].mean():.2f}ms")
    ax2.set_xlabel("지연 (ms)")
    ax2.set_ylabel("Count")
    ax2.set_title("Fill 지연 Distribution")
    ax2.legend(fontsize=8)

    # ── Panel 3: Fill qty & fee over time ──
    ax3 = axes[2]
    fills_sorted = fills.sort_values("timestamp")
    colors_bar = ["#2ecc71" if s == "BUY" else "#e74c3c" for s in fills_sorted["side"]]
    ax3.bar(range(len(fills_sorted)), fills_sorted["filled_qty"],
            color=colors_bar, alpha=0.7, edgecolor="white")
    ax3.set_xlabel("Fill Sequence")
    ax3.set_ylabel("Filled Qty")
    ax3.set_title("Fill Sizes (green=BUY, red=SELL)")

    # fee as secondary axis
    ax3b = ax3.twinx()
    ax3b.plot(range(len(fills_sorted)), fills_sorted["fee"].cumsum(),
              color="#e67e22", linewidth=1.5, label="cumulative fee")
    ax3b.set_ylabel("Cumulative Fee (KRW)", color="#e67e22")
    ax3b.tick_params(axis="y", labelcolor="#e67e22")
    ax3b.legend(loc="upper left", fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    out_path = run_dir / "plots" / "execution_quality.png"
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
    summary: dict = data.get("summary", {})
    if not summary:
        print("No summary data — skipping dashboard.")
        return run_dir / "plots" / "dashboard.png"

    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    fig.suptitle("Performance Dashboard", fontsize=14, fontweight="bold")

    # ── PnL breakdown (pie) ──
    ax = axes[0, 0]
    costs = {
        "Commission": abs(summary.get("total_commission", 0)),
        "Slippage": abs(summary.get("total_slippage", 0)),
        "Impact": abs(summary.get("total_impact", 0)),
    }
    nonzero = {k: v for k, v in costs.items() if v > 0}
    if nonzero:
        ax.pie(nonzero.values(), labels=nonzero.keys(), autopct="%1.1f%%",
               colors=["#3498db", "#e67e22", "#e74c3c"], startangle=90)
    ax.set_title("Cost Breakdown")

    # ── Risk metrics (bar) ──
    ax = axes[0, 1]
    risk_keys = ["sharpe_ratio", "sortino_ratio"]
    risk_vals = [summary.get(k, 0) for k in risk_keys]
    risk_labels = ["Sharpe", "Sortino"]
    colors = ["#2ecc71" if v > 0 else "#e74c3c" for v in risk_vals]
    ax.barh(risk_labels, risk_vals, color=colors, edgecolor="white", height=0.5)
    for i, v in enumerate(risk_vals):
        ax.text(v + 0.1, i, f"{v:.2f}", va="center", fontsize=10)
    ax.set_title("Risk Ratios")
    ax.axvline(0, color="gray", linewidth=0.5)

    # ── Execution metrics (bar) ──
    ax = axes[0, 2]
    exec_keys = ["is_bps", "avg_slippage_bps", "avg_market_impact_bps", "timing_score"]
    exec_vals = [summary.get(k, 0) for k in exec_keys]
    exec_labels = ["IS (bps)", "Slippage\n(bps)", "Impact\n(bps)", "Timing\nScore"]
    colors = ["#3498db"] * 3 + ["#2ecc71"]
    ax.bar(exec_labels, exec_vals, color=colors, edgecolor="white")
    for i, v in enumerate(exec_vals):
        ax.text(i, v + 0.05, f"{v:.2f}", ha="center", fontsize=9)
    ax.set_title("Execution Quality")
    ax.axhline(0, color="gray", linewidth=0.5)

    # ── Attribution (waterfall) ──
    ax = axes[1, 0]
    attr_keys = ["alpha_contribution", "execution_contribution",
                 "cost_contribution", "timing_contribution"]
    attr_labels = ["Alpha", "Execution", "Cost", "Timing"]
    attr_vals = [summary.get(k, 0) for k in attr_keys]
    cumulative = np.cumsum([0] + attr_vals)
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
        f"Net PnL:          {summary.get('net_pnl', 0):>14,.0f} KRW",
        f"Realized PnL:     {summary.get('total_realized_pnl', 0):>14,.0f} KRW",
        f"Total Cost:       {sum(costs.values()):>14,.0f} KRW",
        f"─────────────────────────────────",
        f"Max Drawdown:     {summary.get('max_drawdown', 0):>14.6f}",
        f"VaR (95%):        {summary.get('var_95', 0):>14,.0f} KRW",
        f"ES (95%):         {summary.get('expected_shortfall_95', 0):>14,.0f} KRW",
        f"─────────────────────────────────",
        f"Fill Rate:        {summary.get('fill_rate', 0):>14.2f}",
        f"Annualized Vol:   {summary.get('annualized_vol', 0):>14,.0f}",
        f"Turnover (ann.):  {summary.get('annualized_turnover', 0):>14.2f}",
        f"Avg 지연:      {summary.get('avg_latency_ms', 0):>14.2f} ms",
    ]
    ax.text(0.05, 0.95, "\n".join(lines), transform=ax.transAxes,
            fontfamily="monospace", fontsize=10, verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#ecf0f1", alpha=0.8))
    ax.set_title("Key Metrics")

    # ── Fills per side (bar) ──
    ax = axes[1, 2]
    fills: pd.DataFrame = data.get("fills", pd.DataFrame())
    if not fills.empty:
        side_counts = fills["side"].value_counts()
        side_qty = fills.groupby("side")["filled_qty"].sum()
        x = np.arange(len(side_counts))
        width = 0.35
        ax.bar(x - width / 2, side_counts.values, width, label="# fills",
               color=["#2ecc71" if s == "BUY" else "#e74c3c" for s in side_counts.index])
        ax2 = ax.twinx()
        ax2.bar(x + width / 2, side_qty.reindex(side_counts.index).values, width,
                label="total qty", alpha=0.5,
                color=["#27ae60" if s == "BUY" else "#c0392b" for s in side_counts.index])
        ax.set_xticks(x)
        ax.set_xticklabels(side_counts.index)
        ax.set_ylabel("Fill Count")
        ax2.set_ylabel("Total Qty")
        ax.legend(loc="upper left", fontsize=8)
        ax2.legend(loc="upper right", fontsize=8)
    ax.set_title("Fills by Side")

    plt.tight_layout(rect=[0, 0, 1, 0.95])

    out_path = run_dir / "plots" / "dashboard.png"
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
            return pd.Series(
                entries["cumulative_net_pnl"].astype(float).values,
                index=pd.DatetimeIndex(entries["timestamp"]),
                name="cumulative_net_pnl",
            )

        if {"realized_pnl", "unrealized_pnl"}.issubset(entries.columns):
            realized = entries["realized_pnl"].fillna(0.0).astype(float).cumsum()
            commission = entries.get("commission_cost", 0.0)
            tax = entries.get("tax_cost", 0.0)
            commission_s = pd.Series(commission, index=entries.index).fillna(0.0).astype(float)
            tax_s = pd.Series(tax, index=entries.index).fillna(0.0).astype(float)
            cum_cost = (commission_s + tax_s).cumsum()
            unrealized = entries["unrealized_pnl"].fillna(0.0).astype(float)
            values = realized - cum_cost + unrealized
            return pd.Series(
                values.values,
                index=pd.DatetimeIndex(entries["timestamp"]),
                name="cumulative_net_pnl",
            )

        if "net_pnl" in entries.columns:
            values = entries["net_pnl"].fillna(0.0).astype(float).cumsum()
            return pd.Series(
                values.values,
                index=pd.DatetimeIndex(entries["timestamp"]),
                name="cumulative_net_pnl",
            )

    if not pnl_series_df.empty:
        if "cumulative_net_pnl" in pnl_series_df.columns:
            series = pnl_series_df["cumulative_net_pnl"]
        else:
            series = pnl_series_df.iloc[:, 0]
        series = series.astype(float)
        series.index = pd.DatetimeIndex(series.index)
        return series.rename("cumulative_net_pnl")

    return pd.Series(dtype=float, name="cumulative_net_pnl")


def plot_intraday_cumulative_profit(data: dict, run_dir: Path, show: bool = True) -> Path:
    """Line chart of intraday cumulative profit over 09:00–16:00."""
    series = _build_intraday_cumulative_profit_series(data).sort_index()

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

    plt.tight_layout()

    out_path = run_dir / "plots" / "intraday_cumulative_profit.png"
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

    반환값 list of saved plot file paths.
    """
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    data = load_run(run_dir)
    paths = []

    paths.append(plot_overview(data, run_dir, show=show))
    paths.append(plot_signal_analysis(data, run_dir, show=show))
    paths.append(plot_execution_quality(data, run_dir, show=show))
    paths.append(plot_summary_dashboard(data, run_dir, show=show))
    paths.append(plot_intraday_cumulative_profit(data, run_dir, show=show))

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
