"""
HFT / Market-Making Trading Experiment Diagnostic Visualizer.

Generates publication-quality 3-panel figures (Price, Inventory, PnL)
from pandas DataFrames or proj_rl_agent output directories.
"""

import json
import os
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Column name mapping – edit here when column names differ across datasets
# ---------------------------------------------------------------------------
COLUMN_MAP: Dict[str, str] = {
    # time
    "timestamp": "timestamp",       # or "t"
    # market quotes
    "best_bid": "best_bid",
    "best_ask": "best_ask",
    "midprice": "midprice",
    # agent quotes
    "agent_bid": "agent_bid",
    "agent_ask": "agent_ask",
    # inventory & pnl
    "inventory": "inventory",
    "pnl": "pnl",
    "realized_pnl": "realized_pnl",
    "unrealized_pnl": "unrealized_pnl",
    # fill events
    "buy_fill_price": "buy_fill_price",
    "sell_fill_price": "sell_fill_price",
    "buy_fill_size": "buy_fill_size",
    "sell_fill_size": "sell_fill_size",
    "buy_fill_volume": "buy_fill_volume",
    "sell_fill_volume": "sell_fill_volume",
    # scenario
    "scenario": "scenario",
}

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------
COLORS = {
    "best_bid":       "#6BAED6",   # light blue
    "best_ask":       "#FB6A4A",   # light red
    "midprice":       "#333333",   # dark grey
    "agent_bid":      "#08519C",   # deep blue
    "agent_ask":      "#A50F15",   # deep crimson
    "buy_fill":       "#2CA02C",   # green
    "sell_fill":      "#D62728",   # red-orange
    "inventory":      "#6A51A3",   # purple
    "buy_vol_bar":    "#2CA02C",
    "sell_vol_bar":   "#D62728",
    "total_pnl":      "#08306B",   # dark blue
    "realized_pnl":   "#2CA02C",   # green
    "unrealized_pnl": "#FF7F0E",   # orange
    "zero_line":      "#999999",
}

FONT_SIZES = {
    "suptitle": 13,
    "title": 11,
    "label": 10,
    "tick": 8.5,
    "legend": 8,
}

# ---------------------------------------------------------------------------
# Helper: resolve column name from COLUMN_MAP, with fallback search
# ---------------------------------------------------------------------------

def _col(df: pd.DataFrame, key: str) -> Optional[str]:
    """Return the actual column name in *df* for logical key, or None."""
    name = COLUMN_MAP.get(key, key)
    if name in df.columns:
        return name
    # common aliases
    aliases = {
        "timestamp": ["t", "time", "ts", "datetime", "date"],
        "pnl": ["total_pnl", "net_pnl", "cumulative_net_pnl"],
        "midprice": ["mid", "mid_price"],
        "best_bid": ["bid", "bid_price", "market_bid"],
        "best_ask": ["ask", "ask_price", "market_ask"],
    }
    for alias in aliases.get(key, []):
        if alias in df.columns:
            return alias
    return None


def _has(df: pd.DataFrame, key: str) -> bool:
    return _col(df, key) is not None


def _get(df: pd.DataFrame, key: str) -> Optional[pd.Series]:
    c = _col(df, key)
    return df[c] if c is not None else None


# ---------------------------------------------------------------------------
# Data loading from proj_rl_agent output directory
# ---------------------------------------------------------------------------

def load_experiment_dir(
    result_dir: Union[str, Path],
    scenario_name: Optional[str] = None,
) -> pd.DataFrame:
    """Load CSVs from a proj_rl_agent result directory and merge into a
    single DataFrame suitable for ``plot_trading_diagnostics``.

    Expected files (all optional):
      - fills.csv
      - pnl_entries.csv
      - pnl_series.csv
      - signals.csv
      - orders.csv
      - summary.json
    """
    result_dir = Path(result_dir)
    if not result_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {result_dir}")

    fills_path   = result_dir / "fills.csv"
    pnl_e_path   = result_dir / "pnl_entries.csv"
    pnl_s_path   = result_dir / "pnl_series.csv"
    signals_path = result_dir / "signals.csv"
    quotes_path  = result_dir / "market_quotes.csv"
    summary_path = result_dir / "summary.json"

    dfs: List[pd.DataFrame] = []
    has_actual_quotes = False

    # --- market quotes (preferred source for bid/ask/mid) ---
    if quotes_path.exists():
        quotes_df = pd.read_csv(quotes_path, parse_dates=["timestamp"])
        rename_map = {}
        if "mid_price" in quotes_df.columns:
            rename_map["mid_price"] = "midprice"
        quotes_df = quotes_df.rename(columns=rename_map)
        keep = ["timestamp"]
        for c in ["best_bid", "best_ask", "midprice"]:
            if c in quotes_df.columns:
                keep.append(c)
        if len(keep) > 1:
            quotes_df = quotes_df[keep].dropna(subset=keep[1:], how="all")
            dfs.append(quotes_df)
            has_actual_quotes = True

    # --- fills ---
    fills_df = None
    if fills_path.exists():
        fills_df = pd.read_csv(fills_path, parse_dates=["timestamp"])
        # aggregate fills per (timestamp, side)
        buy_fills = (
            fills_df[fills_df["side"] == "BUY"]
            .groupby("timestamp")
            .agg(buy_fill_price=("fill_price", "mean"),
                 buy_fill_size=("filled_qty", "sum"))
            .reset_index()
        )
        sell_fills = (
            fills_df[fills_df["side"] == "SELL"]
            .groupby("timestamp")
            .agg(sell_fill_price=("fill_price", "mean"),
                 sell_fill_size=("filled_qty", "sum"))
            .reset_index()
        )
        dfs.extend([buy_fills, sell_fills])

    # --- pnl entries ---
    pnl_entries = None
    if pnl_e_path.exists():
        pnl_entries = pd.read_csv(pnl_e_path, parse_dates=["timestamp"])
        # take last entry per timestamp (cumulative snapshot)
        pnl_entries = pnl_entries.groupby("timestamp").last().reset_index()
        pnl_entries = pnl_entries.rename(columns={
            "net_pnl": "pnl",
        })
        keep_cols = ["timestamp"]
        for c in ["pnl", "realized_pnl", "unrealized_pnl", "total_pnl",
                   "gross_pnl", "total_cost"]:
            if c in pnl_entries.columns:
                keep_cols.append(c)
        pnl_entries = pnl_entries[keep_cols]
        dfs.append(pnl_entries)

    # --- pnl series (fallback) ---
    if pnl_entries is None and pnl_s_path.exists():
        pnl_series = pd.read_csv(pnl_s_path, index_col=0, parse_dates=True)
        pnl_series = pnl_series.reset_index()
        pnl_series.columns = ["timestamp", "pnl"]
        dfs.append(pnl_series)

    # --- signals (for score timeseries) ---
    if signals_path.exists():
        signals = pd.read_csv(signals_path, parse_dates=["timestamp"])
        signals = signals[["timestamp", "score"]].copy()
        signals = signals.groupby("timestamp").last().reset_index()
        dfs.append(signals)

    if not dfs:
        raise ValueError(f"No usable CSV files found in {result_dir}")

    # merge all on timestamp
    merged = dfs[0]
    for other in dfs[1:]:
        merged = pd.merge(merged, other, on="timestamp", how="outer",
                          suffixes=("", "_dup"))
    # drop duplicate columns
    merged = merged[[c for c in merged.columns if not c.endswith("_dup")]]
    merged = merged.sort_values("timestamp").reset_index(drop=True)

    # --- derive inventory from fills ---
    if fills_df is not None and "inventory" not in merged.columns:
        inv_events = []
        for _, row in fills_df.iterrows():
            sign = 1 if row["side"] == "BUY" else -1
            inv_events.append({"timestamp": row["timestamp"],
                               "delta": sign * row["filled_qty"]})
        inv_df = pd.DataFrame(inv_events)
        inv_df = inv_df.groupby("timestamp")["delta"].sum().cumsum()
        inv_df = inv_df.reset_index()
        inv_df.columns = ["timestamp", "inventory"]
        merged = pd.merge(merged, inv_df, on="timestamp", how="outer",
                          suffixes=("", "_dup"))
        merged = merged[[c for c in merged.columns if not c.endswith("_dup")]]
        merged = merged.sort_values("timestamp").reset_index(drop=True)

    # forward-fill inventory & pnl
    for c in ["inventory", "pnl", "realized_pnl", "unrealized_pnl"]:
        if c in merged.columns:
            merged[c] = merged[c].ffill().fillna(0)

    # --- derive midprice proxy from fill prices (only if no actual quotes) ---
    _uses_proxy_midprice = False
    if "midprice" not in merged.columns and not has_actual_quotes:
        if "buy_fill_price" in merged.columns or "sell_fill_price" in merged.columns:
            bp = merged.get("buy_fill_price")
            sp = merged.get("sell_fill_price")
            if bp is not None and sp is not None:
                mid = pd.concat([bp, sp], axis=1).mean(axis=1)
            elif bp is not None:
                mid = bp
            else:
                mid = sp
            merged["midprice"] = mid.interpolate(method="linear").ffill().bfill()
            _uses_proxy_midprice = True

    # scenario label
    if scenario_name is not None:
        merged["scenario"] = scenario_name

    # load summary for metadata
    summary = None
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)

    merged.attrs["summary"] = summary
    merged.attrs["result_dir"] = str(result_dir)
    merged.attrs["uses_proxy_midprice"] = _uses_proxy_midprice
    merged.attrs["has_actual_quotes"] = has_actual_quotes
    return merged


def load_comparison(
    dirs: Dict[str, Union[str, Path]],
) -> pd.DataFrame:
    """Load multiple experiment directories and concat with scenario labels.

    Parameters
    ----------
    dirs : dict mapping scenario_name -> directory_path
    """
    frames = []
    for name, path in dirs.items():
        df = load_experiment_dir(path, scenario_name=name)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

def preprocess_trading_df(
    df: pd.DataFrame,
    max_points: Optional[int] = None,
    start: Optional[Union[str, int]] = None,
    end: Optional[Union[str, int]] = None,
    downsample_rule: Optional[str] = None,
) -> pd.DataFrame:
    """Validate, derive missing columns, and optionally downsample."""
    df = df.copy()

    # --- resolve timestamp ---
    ts_col = _col(df, "timestamp")
    if ts_col is None:
        # try index
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            df.rename(columns={df.columns[0]: "timestamp"}, inplace=True)
        else:
            # assume integer step
            df["timestamp"] = np.arange(len(df))
    else:
        if ts_col != "timestamp":
            df.rename(columns={ts_col: "timestamp"}, inplace=True)

    # convert to datetime if possible
    if not pd.api.types.is_numeric_dtype(df["timestamp"]):
        try:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        except Exception:
            pass

    # --- slice ---
    if start is not None:
        if pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df = df[df["timestamp"] >= pd.Timestamp(start)]
        else:
            df = df[df["timestamp"] >= start]
    if end is not None:
        if pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df = df[df["timestamp"] <= pd.Timestamp(end)]
        else:
            df = df[df["timestamp"] <= end]

    # --- derive midprice ---
    if not _has(df, "midprice"):
        bid = _get(df, "best_bid")
        ask = _get(df, "best_ask")
        if bid is not None and ask is not None:
            df["midprice"] = (bid + ask) / 2.0

    # --- derive fill volumes from sizes ---
    for side in ("buy", "sell"):
        vol_key = f"{side}_fill_volume"
        size_key = f"{side}_fill_size"
        if not _has(df, vol_key) and _has(df, size_key):
            size_col = _col(df, size_key)
            df[COLUMN_MAP.get(vol_key, vol_key)] = df[size_col]

    # --- mark fill events (for preserving after downsample) ---
    fill_mask = pd.Series(False, index=df.index)
    for key in ("buy_fill_price", "sell_fill_price",
                "buy_fill_size", "sell_fill_size"):
        s = _get(df, key)
        if s is not None:
            fill_mask |= s.notna()
    df["_is_fill"] = fill_mask

    # --- downsampling ---
    need_downsample = False
    if max_points and len(df) > max_points:
        need_downsample = True
    if downsample_rule:
        need_downsample = True

    if need_downsample:
        fill_rows = df[df["_is_fill"]].copy()
        non_fill = df[~df["_is_fill"]].copy()

        if downsample_rule and pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            non_fill = non_fill.set_index("timestamp").resample(downsample_rule).last()
            non_fill = non_fill.reset_index()
        elif max_points:
            step = max(1, len(non_fill) // max_points)
            non_fill = non_fill.iloc[::step]

        df = pd.concat([non_fill, fill_rows], ignore_index=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

    df.drop(columns=["_is_fill"], inplace=True, errors="ignore")
    return df


# ---------------------------------------------------------------------------
# Plotting – single scenario
# ---------------------------------------------------------------------------

def plot_trading_diagnostics(
    df: pd.DataFrame,
    title: Optional[str] = None,
    figsize: Tuple[float, float] = (14, 10),
    dpi: int = 150,
    save_path: Optional[str] = None,
    show: bool = True,
    axes: Optional[np.ndarray] = None,
    fig: Optional[plt.Figure] = None,
    _col_idx: int = 0,
    _is_right: bool = False,
) -> Tuple[plt.Figure, np.ndarray]:
    """Plot 3-panel diagnostic figure for a single scenario.

    When *axes* is provided (3-element array), draws into those axes instead of
    creating a new figure (used by the comparison layout).
    """
    standalone = axes is None
    if standalone:
        fig = plt.figure(figsize=figsize, dpi=dpi, constrained_layout=True)
        gs = gridspec.GridSpec(3, 1, figure=fig, height_ratios=[3, 2, 2])
        axes = np.array([fig.add_subplot(gs[i]) for i in range(3)])

    ax_price, ax_inv, ax_pnl = axes[0], axes[1], axes[2]
    t = df["timestamp"]

    # ======================= Panel 1: Price / Quotes =======================
    has_price_data = False

    # market bid / ask
    bid = _get(df, "best_bid")
    ask = _get(df, "best_ask")
    if bid is not None:
        ax_price.plot(t, bid, color=COLORS["best_bid"], lw=0.8, alpha=0.7,
                      label="Best Bid")
        has_price_data = True
    if ask is not None:
        ax_price.plot(t, ask, color=COLORS["best_ask"], lw=0.8, alpha=0.7,
                      label="Best Ask")
        has_price_data = True

    # midprice — distinguish actual vs proxy in legend
    mid = _get(df, "midprice")
    is_proxy = df.attrs.get("uses_proxy_midprice", False)
    if mid is not None:
        mid_label = "Midprice (proxy, from fills)" if is_proxy else "Midprice"
        mid_style = {"lw": 0.8, "alpha": 0.5} if is_proxy else {"lw": 1.0, "alpha": 0.8}
        ax_price.plot(t, mid, color=COLORS["midprice"], ls="--",
                      label=mid_label, **mid_style)
        has_price_data = True

    # agent quotes
    abid = _get(df, "agent_bid")
    aask = _get(df, "agent_ask")
    if abid is not None:
        ax_price.plot(t, abid, color=COLORS["agent_bid"], lw=1.4,
                      label="Agent Bid")
        has_price_data = True
    if aask is not None:
        ax_price.plot(t, aask, color=COLORS["agent_ask"], lw=1.4,
                      label="Agent Ask")
        has_price_data = True

    # fill markers
    _plot_fill_markers(ax_price, df, t)

    if has_price_data:
        ax_price.set_ylabel("Price", fontsize=FONT_SIZES["label"])
    else:
        ax_price.set_ylabel("Fill Price", fontsize=FONT_SIZES["label"])

    ax_price.set_title(title or "Price & Quotes", fontsize=FONT_SIZES["title"],
                       fontweight="semibold")
    ax_price.legend(fontsize=FONT_SIZES["legend"], loc="upper left",
                    framealpha=0.85, ncol=2)
    ax_price.grid(True, alpha=0.3, lw=0.5)
    ax_price.tick_params(labelsize=FONT_SIZES["tick"])

    # =================== Panel 2: Inventory / Fill Volume ==================
    inv = _get(df, "inventory")
    buy_vol = _get(df, "buy_fill_volume")
    sell_vol = _get(df, "sell_fill_volume")
    # fallback to fill sizes
    if buy_vol is None:
        buy_vol = _get(df, "buy_fill_size")
    if sell_vol is None:
        sell_vol = _get(df, "sell_fill_size")

    has_bars = buy_vol is not None or sell_vol is not None

    if inv is not None:
        ax_inv.fill_between(t, 0, inv, color=COLORS["inventory"],
                            alpha=0.18, step="post")
        ax_inv.step(t, inv, where="post", color=COLORS["inventory"],
                    lw=1.3, label="Inventory")

    # separate axis for fill volumes if inventory is present
    if has_bars and inv is not None:
        ax_vol = ax_inv.twinx()
        ax_vol.set_ylabel("Fill Volume", fontsize=FONT_SIZES["label"],
                          color="#555555")
        ax_vol.tick_params(labelsize=FONT_SIZES["tick"], colors="#555555")
    elif has_bars:
        ax_vol = ax_inv
    else:
        ax_vol = None

    if ax_vol is not None:
        bar_w = _bar_width(t)
        if buy_vol is not None:
            mask = buy_vol.notna()
            if mask.any():
                ax_vol.bar(t[mask], buy_vol[mask], width=bar_w,
                           color=COLORS["buy_vol_bar"], alpha=0.6,
                           label="Buy Fill Vol")
        if sell_vol is not None:
            mask = sell_vol.notna()
            if mask.any():
                ax_vol.bar(t[mask], -sell_vol[mask].abs(), width=bar_w,
                           color=COLORS["sell_vol_bar"], alpha=0.6,
                           label="Sell Fill Vol")
        ax_vol.legend(fontsize=FONT_SIZES["legend"], loc="upper right",
                      framealpha=0.85)

    ax_inv.set_ylabel("Inventory", fontsize=FONT_SIZES["label"])
    ax_inv.set_title("Inventory & Fill Volume", fontsize=FONT_SIZES["title"],
                     fontweight="semibold")
    ax_inv.axhline(0, color=COLORS["zero_line"], lw=0.7, ls="-")
    ax_inv.legend(fontsize=FONT_SIZES["legend"], loc="upper left",
                  framealpha=0.85)
    ax_inv.grid(True, alpha=0.3, lw=0.5)
    ax_inv.tick_params(labelsize=FONT_SIZES["tick"])

    # ========================= Panel 3: PnL ================================
    pnl = _get(df, "pnl")
    rpnl = _get(df, "realized_pnl")
    upnl = _get(df, "unrealized_pnl")

    if pnl is not None:
        ax_pnl.plot(t, pnl, color=COLORS["total_pnl"], lw=1.5,
                    label="Total PnL")
    if rpnl is not None:
        ax_pnl.plot(t, rpnl, color=COLORS["realized_pnl"], lw=1.0,
                    alpha=0.85, label="Realized PnL")
    if upnl is not None:
        ax_pnl.plot(t, upnl, color=COLORS["unrealized_pnl"], lw=1.0,
                    alpha=0.85, label="Unrealized PnL")

    ax_pnl.axhline(0, color=COLORS["zero_line"], lw=0.8, ls="-")
    ax_pnl.set_ylabel("PnL", fontsize=FONT_SIZES["label"])
    ax_pnl.set_xlabel("Time", fontsize=FONT_SIZES["label"])
    ax_pnl.set_title("PnL", fontsize=FONT_SIZES["title"],
                      fontweight="semibold")
    ax_pnl.legend(fontsize=FONT_SIZES["legend"], loc="upper left",
                  framealpha=0.85)
    ax_pnl.grid(True, alpha=0.3, lw=0.5)
    ax_pnl.tick_params(labelsize=FONT_SIZES["tick"])

    # shared x axis formatting
    for ax in axes:
        if pd.api.types.is_datetime64_any_dtype(t):
            ax.xaxis.set_major_formatter(
                plt.matplotlib.dates.DateFormatter("%H:%M"))
            fig.autofmt_xdate(rotation=30, ha="right")
        ax.margins(x=0.01)

    # hide x tick labels on upper panels when standalone
    if standalone:
        for ax in axes[:-1]:
            ax.tick_params(labelbottom=False)

    if standalone and save_path:
        _save_figure(fig, save_path, dpi)
    if standalone and show:
        plt.show()

    return fig, axes


# ---------------------------------------------------------------------------
# Plotting – multi-scenario comparison
# ---------------------------------------------------------------------------

def plot_trading_diagnostics_by_scenario(
    df: pd.DataFrame,
    title: Optional[str] = None,
    figsize: Optional[Tuple[float, float]] = None,
    dpi: int = 150,
    save_path: Optional[str] = None,
    show: bool = True,
    layout: str = "auto",
    **preprocess_kw,
) -> Tuple[plt.Figure, np.ndarray]:
    """Automatically split by scenario column and plot comparison figures.

    Parameters
    ----------
    layout : "auto", "columns", or "rows"
        "columns" = side-by-side (3 rows × N cols);
        "rows"    = stacked (3*N rows × 1 col);
        "auto"    = "columns" if <=3 scenarios, else "rows".
    """
    scen_col = _col(df, "scenario")
    if scen_col is None:
        df_proc = preprocess_trading_df(df, **preprocess_kw)
        return plot_trading_diagnostics(df_proc, title=title, figsize=figsize or (14, 10),
                                        dpi=dpi, save_path=save_path, show=show)

    scenarios = df[scen_col].dropna().unique().tolist()
    n = len(scenarios)

    if n == 0:
        df_proc = preprocess_trading_df(df, **preprocess_kw)
        return plot_trading_diagnostics(df_proc, title=title, figsize=figsize or (14, 10),
                                        dpi=dpi, save_path=save_path, show=show)
    if n == 1:
        df_proc = preprocess_trading_df(df, **preprocess_kw)
        return plot_trading_diagnostics(df_proc, title=title or scenarios[0],
                                        figsize=figsize or (14, 10),
                                        dpi=dpi, save_path=save_path, show=show)

    if layout == "auto":
        layout = "columns" if n <= 3 else "rows"

    if layout == "columns":
        if figsize is None:
            figsize = (7 * n, 10)
        fig = plt.figure(figsize=figsize, dpi=dpi, constrained_layout=True)
        gs = gridspec.GridSpec(3, n, figure=fig, height_ratios=[3, 2, 2])

        all_axes = []
        for ci, scen in enumerate(scenarios):
            sub_df = df[df[scen_col] == scen].copy()
            sub_df = preprocess_trading_df(sub_df, **preprocess_kw)
            col_axes = np.array([fig.add_subplot(gs[ri, ci]) for ri in range(3)])

            # share x with first column
            if ci > 0 and all_axes:
                for ri in range(3):
                    col_axes[ri].sharey(all_axes[0][ri])

            plot_trading_diagnostics(
                sub_df, title=str(scen),
                fig=fig, axes=col_axes,
                _col_idx=ci, _is_right=(ci > 0),
                save_path=None, show=False,
            )
            # hide y-labels on right columns for cleanliness
            if ci > 0:
                for ax in col_axes:
                    ax.set_ylabel("")
            all_axes.append(col_axes)

        # share x-axes per row
        for ri in range(3):
            ref = all_axes[0][ri]
            for ci in range(1, n):
                all_axes[ci][ri].sharex(ref)
                all_axes[ci][ri].tick_params(labelbottom=(ri == 2))
            ref.tick_params(labelbottom=(ri == 2))

    else:  # rows layout
        if figsize is None:
            figsize = (14, 4 * n)
        fig, ax_flat = plt.subplots(3 * n, 1, figsize=figsize, dpi=dpi,
                                     constrained_layout=True)
        all_axes = []
        for si, scen in enumerate(scenarios):
            sub_df = df[df[scen_col] == scen].copy()
            sub_df = preprocess_trading_df(sub_df, **preprocess_kw)
            row_axes = ax_flat[si * 3: (si + 1) * 3]
            plot_trading_diagnostics(
                sub_df, title=str(scen),
                fig=fig, axes=row_axes,
                save_path=None, show=False,
            )
            all_axes.append(row_axes)

    if title:
        fig.suptitle(title, fontsize=FONT_SIZES["suptitle"],
                     fontweight="bold", y=1.01)

    if save_path:
        _save_figure(fig, save_path, dpi)
    if show:
        plt.show()

    return fig, np.array(all_axes)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _plot_fill_markers(ax: plt.Axes, df: pd.DataFrame, t: pd.Series):
    """Draw buy/sell fill markers on price axis."""
    bp = _get(df, "buy_fill_price")
    sp = _get(df, "sell_fill_price")
    bs = _get(df, "buy_fill_size")
    ss = _get(df, "sell_fill_size")

    base_size = 40
    max_marker = 200

    # buy fills
    if bp is not None:
        mask = bp.notna()
        if mask.any():
            sizes = np.full(mask.sum(), base_size)
            if bs is not None:
                raw = bs[mask].fillna(1).values.astype(float)
                if raw.max() > 0:
                    sizes = np.clip(base_size + (raw / raw.max()) * (max_marker - base_size),
                                    base_size, max_marker)
            ax.scatter(t[mask], bp[mask], marker="^", s=sizes,
                       color=COLORS["buy_fill"], edgecolors="black",
                       linewidths=0.4, zorder=5, label="Buy Fill")

    # sell fills
    if sp is not None:
        mask = sp.notna()
        if mask.any():
            sizes = np.full(mask.sum(), base_size)
            if ss is not None:
                raw = ss[mask].fillna(1).values.astype(float)
                if raw.max() > 0:
                    sizes = np.clip(base_size + (raw / raw.max()) * (max_marker - base_size),
                                    base_size, max_marker)
            ax.scatter(t[mask], sp[mask], marker="v", s=sizes,
                       color=COLORS["sell_fill"], edgecolors="black",
                       linewidths=0.4, zorder=5, label="Sell Fill")


def _bar_width(t: pd.Series) -> float:
    """Compute a reasonable bar width from time axis."""
    if pd.api.types.is_datetime64_any_dtype(t):
        diffs = t.dropna().diff().dt.total_seconds().dropna()
        if len(diffs) > 0:
            med = diffs.median()
            # return as fraction of day for matplotlib date axis
            return max(med / 86400.0 * 0.8, 1e-5)
        return 0.001
    else:
        diffs = t.diff().dropna()
        if len(diffs) > 0:
            return float(diffs.median()) * 0.8
        return 0.8


def _save_figure(fig: plt.Figure, save_path: str, dpi: int):
    """Save figure as PNG and PDF."""
    base = os.path.splitext(save_path)[0]
    for ext in (".png", ".pdf"):
        out = base + ext
        fig.savefig(out, dpi=dpi, bbox_inches="tight", facecolor="white")
        print(f"Saved: {out}")


# ---------------------------------------------------------------------------
# Convenience: quick plot from result directory
# ---------------------------------------------------------------------------

def plot_from_dir(
    result_dir: Union[str, Path],
    title: Optional[str] = None,
    figsize: Tuple[float, float] = (14, 10),
    dpi: int = 150,
    save_path: Optional[str] = None,
    show: bool = True,
    require_actual_quotes: bool = False,
    **preprocess_kw,
) -> Tuple[plt.Figure, np.ndarray]:
    """One-liner: load an experiment directory and plot diagnostics.

    Parameters
    ----------
    require_actual_quotes : bool
        If True, raise ValueError when actual market quotes are unavailable
        (market_quotes.csv missing). Useful for publication-quality figures
        where proxy midprice from fills is unacceptable.
    """
    df = load_experiment_dir(result_dir)
    if require_actual_quotes and not df.attrs.get("has_actual_quotes", False):
        raise ValueError(
            f"No actual market quotes (market_quotes.csv) found in {result_dir}. "
            "Re-run the backtest to generate market_quotes.csv, or set "
            "require_actual_quotes=False to allow proxy midprice."
        )
    df = preprocess_trading_df(df, **preprocess_kw)
    if title is None:
        summary = df.attrs.get("summary")
        if summary:
            net = summary.get("net_pnl", "")
            n_fills = int(summary.get("n_fills", 0))
            title = f"Backtest  |  Net PnL: {net:,.0f}  |  Fills: {n_fills}"
        else:
            title = Path(result_dir).name
        # Warn in title if proxy is used
        if df.attrs.get("uses_proxy_midprice", False):
            title += "  [proxy midprice]"
    return plot_trading_diagnostics(df, title=title, figsize=figsize,
                                    dpi=dpi, save_path=save_path, show=show)


def plot_comparison_from_dirs(
    dirs: Dict[str, Union[str, Path]],
    title: Optional[str] = None,
    layout: str = "auto",
    figsize: Optional[Tuple[float, float]] = None,
    dpi: int = 150,
    save_path: Optional[str] = None,
    show: bool = True,
    **preprocess_kw,
) -> Tuple[plt.Figure, np.ndarray]:
    """One-liner: load multiple directories and plot side-by-side comparison."""
    df = load_comparison(dirs)
    return plot_trading_diagnostics_by_scenario(
        df, title=title, layout=layout, figsize=figsize,
        dpi=dpi, save_path=save_path, show=show, **preprocess_kw)


# ---------------------------------------------------------------------------
# Sample synthetic data generator (for testing)
# ---------------------------------------------------------------------------

def _make_sample_df(n: int = 500, seed: int = 42,
                    scenario: Optional[str] = None) -> pd.DataFrame:
    """Generate synthetic HFT-like DataFrame for demonstration."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2026-03-12 09:00", periods=n, freq="5s")

    mid = 188000.0 + np.cumsum(rng.normal(0, 5, n))
    spread = rng.uniform(10, 30, n)
    best_bid = mid - spread / 2
    best_ask = mid + spread / 2

    agent_offset = rng.uniform(2, 15, n)
    agent_bid = best_bid - agent_offset
    agent_ask = best_ask + agent_offset

    # random fills
    buy_mask = rng.random(n) < 0.04
    sell_mask = rng.random(n) < 0.04
    buy_fill_price = np.where(buy_mask, best_ask + rng.uniform(-2, 5, n), np.nan)
    sell_fill_price = np.where(sell_mask, best_bid - rng.uniform(-2, 5, n), np.nan)
    buy_fill_size = np.where(buy_mask, rng.integers(1, 300, n), np.nan)
    sell_fill_size = np.where(sell_mask, rng.integers(1, 300, n), np.nan)

    # inventory
    inv = np.zeros(n)
    for i in range(n):
        delta = 0
        if buy_mask[i]:
            delta += buy_fill_size[i]
        if sell_mask[i]:
            delta -= sell_fill_size[i]
        inv[i] = (inv[i - 1] if i > 0 else 0) + delta

    # pnl
    pnl = np.cumsum(rng.normal(50, 800, n))
    realized = np.cumsum(np.where(buy_mask | sell_mask, rng.normal(20, 400, n), 0))
    unrealized = pnl - realized

    data = {
        "timestamp": timestamps,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "midprice": mid,
        "agent_bid": agent_bid,
        "agent_ask": agent_ask,
        "buy_fill_price": buy_fill_price,
        "sell_fill_price": sell_fill_price,
        "buy_fill_size": buy_fill_size,
        "sell_fill_size": sell_fill_size,
        "inventory": inv,
        "pnl": pnl,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
    }
    if scenario is not None:
        data["scenario"] = scenario
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Main – sample usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # ---- Example 1: synthetic single-scenario figure ----
    print("=== Example 1: Single scenario (synthetic data) ===")
    df_single = _make_sample_df(600, seed=42)
    df_single = preprocess_trading_df(df_single, max_points=400)
    plot_trading_diagnostics(
        df_single,
        title="Market-Making Agent — Single Episode",
        save_path="trading_diag_single",
        show=False,
    )

    # ---- Example 2: two-scenario comparison (synthetic) ----
    print("\n=== Example 2: Two-scenario comparison (synthetic data) ===")
    df_a = _make_sample_df(400, seed=1, scenario="Strategy A")
    df_b = _make_sample_df(400, seed=2, scenario="Baseline")
    df_cmp = pd.concat([df_a, df_b], ignore_index=True)
    plot_trading_diagnostics_by_scenario(
        df_cmp,
        title="Strategy A vs Baseline",
        save_path="trading_diag_comparison",
        show=False,
    )

    # ---- Example 3: load from proj_rl_agent output directory ----
    sample_dir = Path(__file__).parent / "outputs" / "backtests"
    if sample_dir.exists():
        runs = sorted(sample_dir.iterdir())
        if runs:
            print(f"\n=== Example 3: Plot from directory {runs[-1].name} ===")
            plot_from_dir(
                runs[-1],
                save_path="trading_diag_from_dir",
                show=False,
            )

            # comparison of two runs if available
            if len(runs) >= 2:
                print(f"\n=== Example 4: Compare two backtest runs ===")
                plot_comparison_from_dirs(
                    {"Run A": runs[-1], "Run B": runs[-2]},
                    title="Backtest Run Comparison",
                    save_path="trading_diag_dir_comparison",
                    show=False,
                )

    # ---- Example 5: compare baseline strategies ----
    baseline_dir = Path(__file__).parent / "outputs" / "baseline_runs"
    if baseline_dir.exists():
        baselines = {d.name: d for d in sorted(baseline_dir.iterdir()) if d.is_dir()}
        if len(baselines) >= 2:
            # pick first two
            picked = dict(list(baselines.items())[:2])
            print(f"\n=== Example 5: Compare baselines: {list(picked.keys())} ===")
            plot_comparison_from_dirs(
                picked,
                title="Baseline Strategy Comparison",
                layout="columns",
                save_path="trading_diag_baseline_cmp",
                show=False,
            )

    print("\nDone.")
