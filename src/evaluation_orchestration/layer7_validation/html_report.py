from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

logger = logging.getLogger(__name__)

MAX_QUOTE_POINTS = 10_000
SCATTERGL_THRESHOLD = 5_000


def generate_html_report(run_dir: Path) -> Path | None:
    """Generate an interactive HTML report from backtest artifacts in run_dir.

    Returns the Path to the generated report.html, or None on failure.
    Writes atomically via report.html.tmp -> rename.
    """
    try:
        return _build_report(run_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HTML report generation failed: %s", exc)
        return None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _build_report(run_dir: Path) -> Path:
    run_id = run_dir.name

    # --- load data ------------------------------------------------
    summary: dict = {}
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        with open(summary_path, encoding="utf-8") as fh:
            summary = json.load(fh)

    diagnostics: dict = {}
    diag_path = run_dir / "realism_diagnostics.json"
    if diag_path.exists():
        with open(diag_path, encoding="utf-8") as fh:
            diagnostics = json.load(fh)

    quotes = pd.DataFrame(columns=["timestamp", "best_bid", "best_ask"])
    quotes_path = run_dir / "market_quotes.csv"
    if quotes_path.exists():
        quotes = pd.read_csv(quotes_path, parse_dates=["timestamp"])
        if len(quotes) > MAX_QUOTE_POINTS:
            step = math.ceil(len(quotes) / MAX_QUOTE_POINTS)
            quotes = quotes.iloc[::step]

    fills = pd.DataFrame(columns=["timestamp", "side", "filled_qty", "fill_price"])
    fills_path = run_dir / "fills.csv"
    if fills_path.exists():
        fills = pd.read_csv(fills_path, parse_dates=["timestamp"])

    # pnl_entries for time-of-day analysis
    pnl_entries = pd.DataFrame()
    pnl_entries_path = run_dir / "pnl_entries.csv"
    if pnl_entries_path.exists():
        pnl_entries = pd.read_csv(pnl_entries_path, parse_dates=["timestamp"])

    # quotes_full: undownsampled, for edge analysis
    quotes_full = pd.DataFrame(columns=["timestamp", "mid_price"])
    if quotes_path.exists():
        quotes_full = pd.read_csv(quotes_path, parse_dates=["timestamp"])

    pnl_series: pd.Series | None = None
    pnl_path = run_dir / "pnl_series.csv"
    if pnl_path.exists():
        df_pnl = pd.read_csv(pnl_path, index_col=0, parse_dates=True)
        if "cumulative_net_pnl" in df_pnl.columns:
            pnl_series = df_pnl["cumulative_net_pnl"]

    strategy_info: dict = {}
    strategy_info_path = run_dir / "strategy_info.json"
    if strategy_info_path.exists():
        with open(strategy_info_path, encoding="utf-8") as fh:
            strategy_info = json.load(fh)

    # --- build chart ----------------------------------------------
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True)

    n_quotes = len(quotes)
    ScatterCls = go.Scattergl if n_quotes >= SCATTERGL_THRESHOLD else go.Scatter

    if not quotes.empty:
        fig.add_trace(
            ScatterCls(
                x=quotes["timestamp"],
                y=quotes["best_bid"],
                name="best_bid",
                mode="lines",
                hovertemplate="timestamp: %{x}<br>best_bid: %{y}<extra></extra>",
            ),
            row=1, col=1,
        )
        fig.add_trace(
            ScatterCls(
                x=quotes["timestamp"],
                y=quotes["best_ask"],
                name="best_ask",
                mode="lines",
                hovertemplate="timestamp: %{x}<br>best_ask: %{y}<extra></extra>",
            ),
            row=1, col=1,
        )

    if not fills.empty:
        buys = fills[fills["side"] == "BUY"]
        sells = fills[fills["side"] == "SELL"]
        if not buys.empty:
            fig.add_trace(
                go.Scatter(
                    x=buys["timestamp"],
                    y=buys["fill_price"],
                    name="BUY fills",
                    mode="markers",
                    marker=dict(symbol="triangle-up", color="blue"),
                    customdata=list(zip(buys["filled_qty"], buys["fill_price"])),
                    hovertemplate="timestamp: %{x}<br>side: BUY<br>qty: %{customdata[0]}<br>price: %{customdata[1]}<extra></extra>",
                ),
                row=1, col=1,
            )
        if not sells.empty:
            fig.add_trace(
                go.Scatter(
                    x=sells["timestamp"],
                    y=sells["fill_price"],
                    name="SELL fills",
                    mode="markers",
                    marker=dict(symbol="triangle-down", color="red"),
                    customdata=list(zip(sells["filled_qty"], sells["fill_price"])),
                    hovertemplate="timestamp: %{x}<br>side: SELL<br>qty: %{customdata[0]}<br>price: %{customdata[1]}<extra></extra>",
                ),
                row=1, col=1,
            )

    if pnl_series is not None and len(pnl_series) > 0:
        n_pnl = len(pnl_series)
        PnlScatterCls = go.Scattergl if n_pnl >= SCATTERGL_THRESHOLD else go.Scatter
        fig.add_trace(
            PnlScatterCls(
                x=pnl_series.index,
                y=pnl_series.values,
                name="cumulative_net_pnl",
                mode="lines",
                line_shape="hv",
                hovertemplate="timestamp: %{x}<br>pnl: %{y}<extra></extra>",
            ),
            row=2, col=1,
        )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=True)

    # --- build summary cards --------------------------------------
    metrics = ["net_pnl", "sharpe_ratio", "max_drawdown", "fill_rate", "n_fills"]
    cards_html = _build_cards(summary, metrics)

    # --- build diagnostics table ----------------------------------
    diag_table_html = _build_table(diagnostics, "Realism Diagnostics")
    fill_quality_html = _build_fill_quality_section(fills)
    timeofday_html = _build_timeofday_section(pnl_entries)
    edge_html = _build_edge_analysis_section(fills, quotes_full)
    strategy_code_html = _build_strategy_code_section(strategy_info)
    feedback_html = _build_feedback_section(strategy_info)

    # --- assemble full HTML ---------------------------------------
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Backtest Report — {run_id}</title>
<style>
  body {{ font-family: sans-serif; margin: 1rem 2rem; }}
  h1 {{ font-size: 1.4rem; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 1rem; margin-bottom: 1rem; }}
  .card {{ background: #f4f4f4; border-radius: 6px; padding: 0.75rem 1.25rem; min-width: 140px; }}
  .card-label {{ font-size: 0.75rem; color: #555; }}
  .card-value {{ font-size: 1.1rem; font-weight: bold; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 800px; margin-top: 1rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: left; }}
  th {{ background: #eee; }}
</style>
</head>
<body>
<h1>Backtest Report — {run_id}</h1>
<div class="cards">
{cards_html}
</div>
{chart_html}
{strategy_code_html}
{feedback_html}
{fill_quality_html}
{timeofday_html}
{edge_html}
{diag_table_html}
</body>
</html>"""

    # --- atomic write ---------------------------------------------
    out_path = run_dir / "report.html"
    tmp_path = run_dir / "report.html.tmp"
    tmp_path.write_text(html, encoding="utf-8")
    tmp_path.rename(out_path)
    return out_path


def _build_cards(summary: dict, keys: list[str]) -> str:
    parts = []
    for key in keys:
        value = summary.get(key, "—")
        if isinstance(value, float):
            value_str = f"{value:.4g}"
        else:
            value_str = str(value)
        parts.append(
            f'  <div class="card">'
            f'<div class="card-label">{key}</div>'
            f'<div class="card-value">{value_str}</div>'
            f'</div>'
        )
    return "\n".join(parts)


def _build_table(data: dict, title: str) -> str:
    if not data:
        return ""
    rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in data.items()
    )
    return f"<h2>{title}</h2><table><tr><th>Key</th><th>Value</th></tr>{rows}</table>"


def _build_fill_quality_section(fills: pd.DataFrame) -> str:
    try:
        if fills.empty or "slippage_bps" not in fills.columns:
            return ""

        stats = [
            ("avg_slippage_bps", fills["slippage_bps"].mean()),
            ("avg_latency_ms", fills["latency_ms"].mean()),
            ("total_fee", fills["fee"].sum()),
            ("n_fills", len(fills)),
        ]
        card_parts = []
        for key, value in stats:
            if isinstance(value, float):
                value_str = f"{value:.4g}"
            else:
                value_str = str(value)
            card_parts.append(
                f"  <div class=\"card\">"
                f"<div class=\"card-label\">{key}</div>"
                f"<div class=\"card-value\">{value_str}</div>"
                f"</div>"
            )
        cards_html = "<div class=\"cards\">\n" + "\n".join(card_parts) + "\n</div>"

        buys = fills[fills["side"] == "BUY"]
        sells = fills[fills["side"] == "SELL"]
        fig = make_subplots(rows=1, cols=2)
        fig.add_trace(go.Box(y=buys["slippage_bps"], name="BUY"), row=1, col=1)
        fig.add_trace(go.Box(y=sells["slippage_bps"], name="SELL"), row=1, col=2)
        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        return "<h2>Fill Quality</h2>" + cards_html + chart_html
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fill quality section generation failed: %s", exc)
        return ""


def _build_timeofday_section(pnl_entries: pd.DataFrame) -> str:
    try:
        if (
            pnl_entries.empty
            or "net_pnl" not in pnl_entries.columns
            or "timestamp" not in pnl_entries.columns
        ):
            return ""

        hourly = pnl_entries.groupby(pnl_entries["timestamp"].dt.hour)["net_pnl"].sum()
        colors = ["green" if value >= 0 else "red" for value in hourly.values.tolist()]
        fig = go.Figure(go.Bar(x=hourly.index.tolist(), y=hourly.values.tolist(), marker_color=colors))
        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        return "<h2>Time-of-Day Performance</h2>" + chart_html
    except Exception as exc:  # noqa: BLE001
        logger.warning("Time-of-day section generation failed: %s", exc)
        return ""


def _build_edge_analysis_section(fills: pd.DataFrame, quotes: pd.DataFrame) -> str:
    try:
        if fills.empty or quotes.empty or "mid_price" not in quotes.columns:
            return ""

        fills = fills.sort_values("timestamp").reset_index(drop=True)
        quotes = quotes.sort_values("timestamp").reset_index(drop=True)

        fills = pd.merge_asof(
            fills,
            quotes[["timestamp", "mid_price"]],
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta("2s"),
            suffixes=("", "_q"),
        )
        fills = fills.rename(columns={"mid_price": "mid_at_fill"})

        for horizon in [1, 5, 10, 30]:
            lookup_df = fills[["timestamp"]].copy()
            lookup_df["lookup_ts"] = lookup_df["timestamp"] + pd.Timedelta(seconds=horizon)
            future = pd.merge_asof(
                lookup_df,
                quotes[["timestamp", "mid_price"]],
                left_on="lookup_ts",
                right_on="timestamp",
            )
            fills[f"mid_T{horizon}s"] = future["mid_price"]

        chart_horizons = []
        chart_values = []
        buy_mask = fills["side"] == "BUY"
        sell_mask = fills["side"] == "SELL"
        for horizon in [1, 5, 10, 30]:
            mid_future = fills[f"mid_T{horizon}s"]
            price_change = pd.Series(index=fills.index, dtype=float)
            price_change.loc[buy_mask] = mid_future.loc[buy_mask] - fills.loc[buy_mask, "mid_at_fill"]
            price_change.loc[sell_mask] = fills.loc[sell_mask, "mid_at_fill"] - mid_future.loc[sell_mask]
            avg_bps = price_change.mean() / fills["mid_at_fill"].mean() * 10000
            if pd.notna(avg_bps):
                chart_horizons.append(f"T+{horizon}s")
                chart_values.append(avg_bps)

        if not chart_horizons:
            return ""

        colors = ["green" if value >= 0 else "red" for value in chart_values]
        fig = go.Figure(go.Bar(x=chart_horizons, y=chart_values, marker_color=colors))
        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
        return "<h2>Edge Analysis — Post-Fill Price Movement</h2>" + chart_html
    except Exception as exc:  # noqa: BLE001
        logger.warning("Edge analysis section generation failed: %s", exc)
        return ""


def _build_strategy_code_section(strategy_info: dict) -> str:
    """Strategy code + strategy_text를 collapsible 블록으로 렌더링."""
    if not strategy_info:
        return ""
    parts = []
    iteration = strategy_info.get("iteration")
    if iteration is not None:
        parts.append(f"<p><strong>Iteration:</strong> {iteration}</p>")
    strategy_text = strategy_info.get("strategy_text")
    if strategy_text:
        parts.append(
            f"<h3>Strategy Description</h3>"
            f"<pre style='white-space:pre-wrap;background:#f8f8f8;padding:0.75rem;border-radius:4px'>"
            f"{strategy_text}"
            f"</pre>"
        )
    code = strategy_info.get("code")
    if code:
        parts.append(
            f"<details><summary><strong>Strategy Code</strong></summary>"
            f"<pre style='white-space:pre-wrap;background:#f8f8f8;padding:0.75rem;border-radius:4px'>"
            f"{code}"
            f"</pre></details>"
        )
    if not parts:
        return ""
    return "<h2>Strategy</h2>" + "".join(parts)


def _build_feedback_section(strategy_info: dict) -> str:
    """LLM 피드백 내용을 표시한다."""
    if not strategy_info:
        return ""
    feedback = strategy_info.get("feedback")
    if not feedback or not isinstance(feedback, dict):
        return ""
    rows = []
    for key in ("verdict", "diagnosis_code", "severity", "primary_issue"):
        value = feedback.get(key)
        if value is not None:
            rows.append(f"<tr><td>{key}</td><td>{value}</td></tr>")
    table_html = ""
    if rows:
        table_html = (
            "<table><tr><th>Key</th><th>Value</th></tr>"
            + "".join(rows)
            + "</table>"
        )
    issues = feedback.get("issues") or []
    suggestions = feedback.get("suggestions") or []
    issues_html = ""
    if issues:
        items = "".join(f"<li>{item}</li>" for item in issues)
        issues_html = f"<h3>Issues</h3><ul>{items}</ul>"
    sugg_html = ""
    if suggestions:
        items = "".join(f"<li>{item}</li>" for item in suggestions)
        sugg_html = f"<h3>Suggestions</h3><ul>{items}</ul>"
    content = table_html + issues_html + sugg_html
    if not content:
        return ""
    return "<h2>LLM Feedback</h2>" + content
