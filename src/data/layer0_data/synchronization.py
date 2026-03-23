"""
synchronization.py
------------------
Utilities for aligning and resampling LOB and trade DataFrames.

주요 책임:
  - Merge LOB snapshots with trade records on a shared timestamp axis.
  - Detect and flag clock-drift between consecutive ticks.
  - Resample LOB data to a fixed frequency with proper aggregation rules.
  - Collapse multiple events sharing the same timestamp.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Aggregation spec used when resampling trade columns
_TRADE_AGG: dict[str, str] = {
    "trade_price": "last",
    "trade_volume": "sum",
    "trade_side": "last",
}


class DataSynchronizer:
    """
    Aligns and resamples LOB and trade data onto a common time grid.

    매개변수
    ----------
    resample_freq : str | None
        If set, calls to resample() default to this frequency.
        E.g. '1s', '200ms', '500ms'.
    max_clock_drift_ms : float
        Threshold in milliseconds above which a gap between consecutive
        timestamps is flagged as clock drift.
    merge_rule : str
        Default rule ('first' or 'last') for same-timestamp deduplication.
    """

    def __init__(
        self,
        resample_freq: Optional[str] = None,
        max_clock_drift_ms: float = 500.0,
        merge_rule: str = "last",
    ) -> None:
        if merge_rule not in ("first", "last"):
            raise ValueError("merge_rule must be 'first' or 'last'")
        self.resample_freq = resample_freq
        self.max_clock_drift_ms = max_clock_drift_ms
        self.merge_rule = merge_rule

    # ------------------------------------------------------------------
    # LOB + trades alignment
    # ------------------------------------------------------------------

    def align_lob_trades(
        self,
        lob_df: pd.DataFrame,
        trades_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Merge LOB snapshots (lob_df) with trade records (trades_df) on the
        'timestamp' column using a backward merge-as-of (tolerance =
        max_clock_drift_ms milliseconds).

        After merging, same-timestamp events are collapsed with merge_rule.

        매개변수
        ----------
        lob_df : pd.DataFrame
            LOB snapshot DataFrame.  Must contain 'timestamp' column.
        trades_df : pd.DataFrame
            Trade DataFrame.  Expected columns: timestamp, price/trade_price,
            volume/trade_volume, side/trade_side.

        반환값
        -------
        pd.DataFrame
            Merged DataFrame indexed/sorted by timestamp, LOB columns intact,
            and trade columns suffixed '_trade' when ambiguous.
        """
        if lob_df.empty:
            logger.warning("align_lob_trades: lob_df is empty, returning empty result")
            return pd.DataFrame()

        lob = lob_df.copy().sort_values("timestamp").reset_index(drop=True)

        if trades_df is None or trades_df.empty:
            logger.info("align_lob_trades: no trades provided, returning lob_df unchanged")
            return lob

        trades = trades_df.copy().sort_values("timestamp").reset_index(drop=True)
        trades = self._normalise_trade_columns(trades)

        tolerance = pd.Timedelta(milliseconds=self.max_clock_drift_ms)

        merged = pd.merge_asof(
            lob,
            trades[["timestamp", "trade_price", "trade_volume", "trade_side"]],
            on="timestamp",
            direction="backward",
            tolerance=tolerance,
            suffixes=("", "_trade"),
        )

        merged = self.merge_same_timestamp(merged, rule=self.merge_rule)
        return merged.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Clock drift detection
    # ------------------------------------------------------------------

    def correct_clock_drift(
        self,
        df: pd.DataFrame,
        reference_col: str = "timestamp",
    ) -> pd.DataFrame:
        """
        Detect anomalous gaps between consecutive timestamps and flag them.

        Rows where the gap to the *previous* row exceeds max_clock_drift_ms
        receive clock_drift_flag=True.  Rows are NOT removed.

        매개변수
        ----------
        df : pd.DataFrame
        reference_col : str
            Column containing timestamps.

        반환값
        -------
        pd.DataFrame
            Same DataFrame with an added 'clock_drift_flag' (bool) column.
        """
        if reference_col not in df.columns or df.empty:
            df = df.copy()
            df["clock_drift_flag"] = False
            return df

        df = df.copy().sort_values(reference_col).reset_index(drop=True)

        timestamps = pd.to_datetime(df[reference_col])
        diffs_ms = timestamps.diff().dt.total_seconds().multiply(1_000.0)

        threshold_ms = self.max_clock_drift_ms
        drift_mask = diffs_ms > threshold_ms

        n_drifts = int(drift_mask.sum())
        if n_drifts > 0:
            logger.warning(
                "clock_drift: %d gap(s) exceed %.0f ms (max gap = %.1f ms)",
                n_drifts,
                threshold_ms,
                diffs_ms.max(),
            )

        df["clock_drift_flag"] = drift_mask.fillna(False)
        return df

    # ------------------------------------------------------------------
    # Resampling
    # ------------------------------------------------------------------

    def resample(self, df: pd.DataFrame, freq: str) -> pd.DataFrame:
        """
        Resample LOB data to *freq* (e.g. '1s', '200ms').

        LOB price/volume columns are forward-filled (last observed state).
        Trade columns (trade_price, trade_volume, trade_side) are aggregated:
            trade_price  → last
            trade_volume → sum
            trade_side   → last

        매개변수
        ----------
        df : pd.DataFrame
            Must contain a 'timestamp' column.
        freq : str
            Pandas offset alias.

        반환값
        -------
        pd.DataFrame
            Resampled DataFrame with timestamp as a regular column.
        """
        if df.empty or "timestamp" not in df.columns:
            return df.copy()

        df = df.copy()
        df = df.sort_values("timestamp")
        df = df.set_index(pd.DatetimeIndex(df["timestamp"]))
        df = df.drop(columns=["timestamp"])

        trade_cols = [c for c in _TRADE_AGG if c in df.columns]
        lob_cols = [c for c in df.columns if c not in trade_cols]

        resampled_parts: list[pd.DataFrame] = []

        if lob_cols:
            lob_resampled = df[lob_cols].resample(freq).last().ffill()
            resampled_parts.append(lob_resampled)

        if trade_cols:
            agg_spec = {c: _TRADE_AGG[c] for c in trade_cols}
            trade_resampled = df[trade_cols].resample(freq).agg(agg_spec)
            resampled_parts.append(trade_resampled)

        if not resampled_parts:
            return pd.DataFrame()

        result = pd.concat(resampled_parts, axis=1)
        result = result.reset_index().rename(columns={"index": "timestamp"})
        result.attrs.update(df.attrs)
        return result

    # ------------------------------------------------------------------
    # Same-timestamp deduplication
    # ------------------------------------------------------------------

    def merge_same_timestamp(
        self, df: pd.DataFrame, rule: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Collapse multiple rows sharing the same timestamp.

        매개변수
        ----------
        df : pd.DataFrame
            Must contain a 'timestamp' column.
        rule : str | None
            'first' or 'last'.  Falls back to self.merge_rule if None.

        반환값
        -------
        pd.DataFrame
            DataFrame with unique timestamps.
        """
        if df.empty or "timestamp" not in df.columns:
            return df

        effective_rule = rule if rule is not None else self.merge_rule
        if effective_rule not in ("first", "last"):
            raise ValueError(f"rule must be 'first' or 'last', got {effective_rule!r}")

        n_before = len(df)
        df = df.drop_duplicates(subset=["timestamp"], keep=effective_rule)
        n_after = len(df)

        if n_before != n_after:
            logger.debug(
                "merge_same_timestamp: collapsed %d duplicate timestamp(s)",
                n_before - n_after,
            )

        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_trade_columns(trades: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure the trade DataFrame has canonical column names:
            trade_price, trade_volume, trade_side
        mapping common aliases if needed.
        """
        rename_map: dict[str, str] = {}
        if "price" in trades.columns and "trade_price" not in trades.columns:
            rename_map["price"] = "trade_price"
        if "volume" in trades.columns and "trade_volume" not in trades.columns:
            rename_map["volume"] = "trade_volume"
        if "side" in trades.columns and "trade_side" not in trades.columns:
            rename_map["side"] = "trade_side"

        if rename_map:
            trades = trades.rename(columns=rename_map)

        for col, default in [
            ("trade_price", np.nan),
            ("trade_volume", 0),
            ("trade_side", None),
        ]:
            if col not in trades.columns:
                trades[col] = default

        return trades
