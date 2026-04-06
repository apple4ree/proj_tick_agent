"""
scripts/internal/adhoc/visualize.py
-------------------------------------
백테스트 run_dir 에서 표준 플롯 3장을 생성한다.

  generate_report_plots(run_dir, show=False) -> list[str]

생성 파일:
  run_dir/plots/dashboard.png
  run_dir/plots/intraday_cumulative_profit.png
  run_dir/plots/trade_timeline.png
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd


# ── helpers ──────────────────────────────────────────────────────────────────

def _load(run_dir: Path) -> dict:
    """Load all artifact files from a run directory."""
    data = {}

    pnl_path = run_dir / "pnl_series.csv"
    if pnl_path.exists():
        df = pd.read_csv(pnl_path, index_col=0, parse_dates=True)
        df.index.name = "timestamp"
        data["pnl"] = df

    fills_path = run_dir / "fills.csv"
    if fills_path.exists():
        data["fills"] = pd.read_csv(fills_path, parse_dates=["timestamp"])

    signals_path = run_dir / "signals.csv"
    if signals_path.exists():
        data["signals"] = pd.read_csv(signals_path, parse_dates=["timestamp"])

    quotes_path = run_dir / "market_quotes.csv"
    if quotes_path.exists():
        data["quotes"] = pd.read_csv(quotes_path, parse_dates=["timestamp"])

    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        with open(summary_path, encoding="utf-8") as f:
            data["summary"] = json.load(f)

    return data


def _krw(v: float) -> str:
    """Format a KRW value with M/K suffix."""
    if abs(v) >= 1e6:
        return f"{v/1e6:.3f} MKW"
    if abs(v) >= 1e3:
        return f"{v/1e3:.1f} KKW"
    return f"{v:.1f} KRW"


# ── plot 1: dashboard ─────────────────────────────────────────────────────────

def _plot_dashboard(data: dict, out_path: Path, show: bool) -> None:
    s = data.get("summary", {})
    fills = data.get("fills", pd.DataFrame())

    fig = plt.figure(figsize=(16, 9))
    fig.suptitle("Performance Dashboard", fontsize=14, fontweight="bold", y=0.98)

    gs = fig.add_gridspec(2, 3, hspace=0.45, wspace=0.35)

    # ── Cost Breakdown (pie) ──────────────────────────────────────────────────
    ax_pie = fig.add_subplot(gs[0, 0])
    commission = abs(s.get("total_commission", 0))
    slippage = abs(s.get("total_slippage", 0))
    impact = abs(s.get("total_impact", 0))
    sizes = [commission, slippage, impact]
    labels = ["Commission", "Slippage", "Impact"]
    colors = ["#e07b54", "#4c72b0", "#6acc65"]
    non_zero = [(sz, lb, co) for sz, lb, co in zip(sizes, labels, colors) if sz > 0]
    if non_zero:
        sizes_, labels_, colors_ = zip(*non_zero)
        wedges, texts, autotexts = ax_pie.pie(
            sizes_, labels=labels_, colors=colors_,
            autopct="%1.1f%%", startangle=90, pctdistance=0.75,
        )
        for at in autotexts:
            at.set_fontsize(8)
    ax_pie.set_title("Cost Breakdown", fontsize=10)

    # ── Risk Ratios (horizontal bar) ──────────────────────────────────────────
    ax_risk = fig.add_subplot(gs[0, 1])
    ratios = {
        "Sortino": s.get("sortino_ratio", 0),
        "Sharpe": s.get("sharpe_ratio", 0),
    }
    names = list(ratios.keys())
    vals = list(ratios.values())
    colors_risk = ["#4c72b0" if v >= 0 else "#c44e52" for v in vals]
    ax_risk.barh(names, vals, color=colors_risk)
    ax_risk.axvline(0, color="black", linewidth=0.8)
    ax_risk.set_title("Risk Ratios", fontsize=10)
    ax_risk.tick_params(labelsize=8)

    # ── Execution Quality (horizontal bar) ────────────────────────────────────
    ax_exec = fig.add_subplot(gs[0, 2])
    exec_metrics = {
        "IS (bps)": s.get("is_bps", 0),
        "Slippage\n(bps)": s.get("avg_slippage_bps", 0),
        "Impact\n(bps)": s.get("avg_market_impact_bps", 0),
        "Timing\nScore": s.get("timing_score", 0),
    }
    names_e = list(exec_metrics.keys())
    vals_e = list(exec_metrics.values())
    colors_e = ["#4c72b0" if v >= 0 else "#c44e52" for v in vals_e]
    ax_exec.barh(names_e, vals_e, color=colors_e)
    ax_exec.axvline(0, color="black", linewidth=0.8)
    ax_exec.set_title("Execution Quality", fontsize=10)
    ax_exec.tick_params(labelsize=7)

    # ── Attribution Waterfall (bar) ───────────────────────────────────────────
    ax_wf = fig.add_subplot(gs[1, 0])
    attr_names = ["Alpha", "Execution", "Cost", "Timing"]
    attr_vals = [
        s.get("alpha_contribution", 0),
        s.get("execution_contribution", 0),
        s.get("cost_contribution", 0),
        s.get("timing_contribution", 0),
    ]
    colors_wf = ["#4c72b0" if v >= 0 else "#c44e52" for v in attr_vals]
    bars = ax_wf.bar(attr_names, [v / 1e6 for v in attr_vals], color=colors_wf)
    ax_wf.axhline(0, color="black", linewidth=0.8)
    ax_wf.set_title("Attribution Waterfall", fontsize=10)
    ax_wf.set_ylabel("Profit (M KRW)", fontsize=8)
    ax_wf.tick_params(labelsize=8)

    # ── Key Metrics (text table) ──────────────────────────────────────────────
    ax_txt = fig.add_subplot(gs[1, 1])
    ax_txt.axis("off")
    net_pnl = s.get("net_pnl", 0)
    realized = s.get("total_realized_pnl", 0)
    commission_total = s.get("total_commission", 0)
    max_dd = s.get("max_drawdown", 0)
    var95 = s.get("var_95", 0)
    n_fills = int(s.get("n_fills", 0))
    fill_rate = s.get("fill_rate", 0)
    ann_vol = s.get("annualized_vol", 0)
    ann_to = s.get("annualized_turnover", 0)
    avg_lat = s.get("avg_latency_ms", 0)

    lines = [
        f"Net PnL:           {net_pnl/1e3:>12.3f} KKW",
        f"Realized PnL:      {realized/1e3:>12.3f} KKW",
        f"Total Cost:        {commission_total/1e3:>12.3f} KKW",
        "",
        f"Max Drawdown:      {max_dd:>12.6f}",
        f"VaR (95%):         {var95/1e3:>12.3f} KKW",
        "",
        f"Fill Rate:         {fill_rate:>12.2%}  # {n_fills}",
        f"Annualized Vol.:   {ann_vol:>12.3f}",
        f"Turnover (ann.):   {ann_to:>12.2f}",
        f"Avg Latency:       {avg_lat:>9.2f} ms",
    ]
    ax_txt.text(
        0.05, 0.95, "\n".join(lines),
        transform=ax_txt.transAxes,
        fontsize=8, va="top", fontfamily="monospace",
    )
    ax_txt.set_title("Key Metrics", fontsize=10)

    # ── Fills by Side (grouped bar) ───────────────────────────────────────────
    ax_fs = fig.add_subplot(gs[1, 2])
    if not fills.empty and "side" in fills.columns:
        grouped = fills.groupby("side").agg(
            fill_count=("order_id", "count"),
            total_qty=("filled_qty", "sum"),
        ).reindex(["SELL", "BUY"], fill_value=0)
        sides = grouped.index.tolist()
        x = np.arange(len(sides))
        width = 0.35
        ax_fs2 = ax_fs.twinx()
        ax_fs.bar(x - width / 2, grouped["fill_count"], width, label="# fills",
                  color="#4c72b0", alpha=0.8)
        ax_fs2.bar(x + width / 2, grouped["total_qty"], width, label="total qty",
                   color="#dd8452", alpha=0.8)
        ax_fs.set_xticks(x)
        ax_fs.set_xticklabels(sides, fontsize=9)
        ax_fs.set_ylabel("Fill Count", fontsize=8, color="#4c72b0")
        ax_fs2.set_ylabel("Total Qty", fontsize=8, color="#dd8452")
        ax_fs.tick_params(labelsize=8)
        ax_fs2.tick_params(labelsize=8)
        lines1, labels1 = ax_fs.get_legend_handles_labels()
        lines2, labels2 = ax_fs2.get_legend_handles_labels()
        ax_fs.legend(lines1 + lines2, labels1 + labels2, fontsize=7, loc="upper right")
    ax_fs.set_title("Fills by Side", fontsize=10)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)


# ── plot 2: intraday cumulative profit ────────────────────────────────────────

def _plot_intraday_pnl(data: dict, out_path: Path, show: bool) -> None:
    pnl = data.get("pnl")
    if pnl is None or pnl.empty:
        return

    fig, ax = plt.subplots(figsize=(12, 4))
    col = pnl.columns[0]  # cumulative_net_pnl
    ax.plot(pnl.index, pnl[col] / 1e3, linewidth=1.2, color="#4c72b0")
    ax.axhline(0, color="black", linewidth=0.7, linestyle="--", alpha=0.4)
    ax.fill_between(
        pnl.index,
        pnl[col] / 1e3,
        0,
        where=(pnl[col] >= 0),
        alpha=0.15,
        color="#4c72b0",
    )
    ax.fill_between(
        pnl.index,
        pnl[col] / 1e3,
        0,
        where=(pnl[col] < 0),
        alpha=0.15,
        color="#c44e52",
    )
    ax.set_title("Intraday Cumulative Profit", fontsize=12)
    ax.set_xlabel("Time")
    ax.set_ylabel("Cumulative Profit (KKW)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    fig.autofmt_xdate()
    ax.grid(True, alpha=0.3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)


# ── plot 3: trade timeline ────────────────────────────────────────────────────

def _plot_trade_timeline(data: dict, out_path: Path, show: bool) -> None:
    quotes = data.get("quotes")
    fills = data.get("fills", pd.DataFrame())
    signals = data.get("signals", pd.DataFrame())

    if quotes is None or quotes.empty:
        return

    fig, (ax_price, ax_sig) = plt.subplots(
        2, 1, figsize=(14, 8),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=True,
    )
    fig.suptitle("Trade Timeline — Market Price + Fills + Arrival Mid", fontsize=11)

    # ── price panel ───────────────────────────────────────────────────────────
    ax_price.plot(quotes["timestamp"], quotes["mid_price"] / 1e3,
                  color="#555555", linewidth=0.8, label="mid_price", zorder=1)
    ax_price.plot(quotes["timestamp"], quotes["best_bid"] / 1e3,
                  color="#4c72b0", linewidth=0.5, linestyle="--", label="best_bid", alpha=0.6)
    ax_price.plot(quotes["timestamp"], quotes["best_ask"] / 1e3,
                  color="#dd8452", linewidth=0.5, linestyle="--", label="best_ask", alpha=0.6)

    if not fills.empty and "side" in fills.columns:
        buys = fills[fills["side"] == "BUY"]
        sells = fills[fills["side"] == "SELL"]
        if not buys.empty:
            ax_price.scatter(
                buys["timestamp"], buys["fill_price"] / 1e3,
                marker="^", color="#4c72b0", s=60, zorder=3, label="BUY fill",
            )
        if not sells.empty:
            ax_price.scatter(
                sells["timestamp"], sells["fill_price"] / 1e3,
                marker="v", color="#c44e52", s=60, zorder=3, label="SELL fill",
            )

    ax_price.set_ylabel("Price (KKW)", fontsize=9)
    ax_price.legend(fontsize=7, loc="upper left")
    ax_price.grid(True, alpha=0.25)
    ax_price.tick_params(labelsize=8)
    ax_price.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

    # ── signal panel ──────────────────────────────────────────────────────────
    if not signals.empty and "score" in signals.columns:
        ax_sig.step(signals["timestamp"], signals["score"],
                    color="#4c72b0", linewidth=1.0, where="post", label="signal score")
        ax_sig.fill_between(
            signals["timestamp"], signals["score"], 0,
            step="post", alpha=0.2, color="#4c72b0",
        )
        if "confidence" in signals.columns:
            ax_sig2 = ax_sig.twinx()
            ax_sig2.plot(signals["timestamp"], signals["confidence"],
                         color="#dd8452", linewidth=0.8, linestyle=":", alpha=0.7,
                         label="confidence")
            ax_sig2.set_ylabel("Confidence", fontsize=8, color="#dd8452")
            ax_sig2.tick_params(labelsize=7)

    ax_sig.set_ylabel("Signal Score", fontsize=9)
    ax_sig.set_xlabel("Time")
    ax_sig.axhline(0, color="black", linewidth=0.6, alpha=0.4)
    ax_sig.set_ylim(-1.1, 1.1)
    ax_sig.grid(True, alpha=0.25)
    ax_sig.tick_params(labelsize=8)
    ax_sig.set_title("Signal Timeline", fontsize=9)

    ax_sig.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    fig.autofmt_xdate()
    plt.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)


# ── public API ────────────────────────────────────────────────────────────────

def generate_report_plots(run_dir: str | Path, show: bool = False) -> list[str]:
    """Generate the 3 standard backtest plots for *run_dir*.

    Returns a list of absolute path strings for the generated files.
    Missing artifact files are silently skipped (empty plot not saved).
    """
    run_dir = Path(run_dir)
    plots_dir = run_dir / "plots"
    data = _load(run_dir)

    paths: list[str] = []

    dashboard = plots_dir / "dashboard.png"
    _plot_dashboard(data, dashboard, show)
    if dashboard.exists():
        paths.append(str(dashboard))

    intraday = plots_dir / "intraday_cumulative_profit.png"
    _plot_intraday_pnl(data, intraday, show)
    if intraday.exists():
        paths.append(str(intraday))

    timeline = plots_dir / "trade_timeline.png"
    _plot_trade_timeline(data, timeline, show)
    if timeline.exists():
        paths.append(str(timeline))

    return paths


# ── CLI convenience ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python visualize.py <run_dir> [--show]")
        sys.exit(1)
    _show = "--show" in sys.argv
    generated = generate_report_plots(sys.argv[1], show=_show)
    for p in generated:
        print(p)
