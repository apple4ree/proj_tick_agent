"""
cleaning.py
-----------
Data cleaning utilities for raw LOB DataFrames and LOBSnapshot lists.

순서대로 적용되는 정제 단계:
    1. Fix negative volumes (clamp to 0).
    2. Remove rows with price inversions (best_ask <= best_bid).
    3. Remove rows with zero bid or ask depth.
    4. Deduplicate timestamps.
    5. Tag and remove extreme mid-price outliers (rolling z-score).
    6. Remove rows with spread_bps > max_spread_bps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .market_state import LOBLevel, LOBSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CleaningStats
# ---------------------------------------------------------------------------

@dataclass
class CleaningStats:
    """Summary statistics produced by a DataCleaner.clean() call."""
    n_total: int
    n_removed: int
    n_price_inversions: int
    n_zero_volume: int
    n_duplicate_ts: int
    n_outliers: int

    @property
    def removal_rate(self) -> float:
        if self.n_total == 0:
            return 0.0
        return self.n_removed / self.n_total


# ---------------------------------------------------------------------------
# DataCleaner
# ---------------------------------------------------------------------------

class DataCleaner:
    """
    Cleans raw LOB DataFrames produced by DataIngester.

    The DataFrame is expected to contain at minimum the following columns
    (generated automatically by load_raw_csv):
        timestamp, BIDP1, ASKP1, BIDP_RSQN1..10, ASKP_RSQN1..10

    The cleaner also accepts pre-computed helper columns:
        mid_price, spread_bps, best_bid, best_ask, total_bid_depth,
        total_ask_depth  — created internally if missing.
    """

    def __init__(
        self,
        max_spread_bps: float = 500.0,
        min_depth: int = 1,
        outlier_zscore: float = 5.0,
        dedup_strategy: str = "last",
    ) -> None:
        if dedup_strategy not in ("first", "last"):
            raise ValueError("dedup_strategy must be 'first' or 'last'")
        self.max_spread_bps = max_spread_bps
        self.min_depth = min_depth
        self.outlier_zscore = outlier_zscore
        self.dedup_strategy = dedup_strategy

    # ------------------------------------------------------------------
    # 공개 API – DataFrame
    # ------------------------------------------------------------------

    def clean(self, df: pd.DataFrame) -> tuple[pd.DataFrame, CleaningStats]:
        """
        Apply all cleaning steps and return (cleaned_df, stats).
        The input df is NOT modified in-place.
        """
        if df.empty:
            return df.copy(), CleaningStats(0, 0, 0, 0, 0, 0)

        df = df.copy()
        n_total = len(df)

        # --- Ensure helper columns exist ---
        df = self._ensure_helper_cols(df)

        # 1단계 - 음수 잔량 수정(df를 제자리에서 변경)
        df = self.fix_negative_volumes(df)

        # 2단계 - 가격 역전
        price_inv_mask = self._price_inversion_mask(df)
        n_price_inv = int(price_inv_mask.sum())

        # 3단계 - 잔량 0
        zero_vol_mask = self._zero_depth_mask(df)
        n_zero_vol = int((zero_vol_mask & ~price_inv_mask).sum())

        # 지금까지의 결합 마스크(통과한 행만 유지)
        bad_mask = price_inv_mask | zero_vol_mask
        df = df[~bad_mask].copy()

        # 4단계 - 타임스탬프 중복 제거
        n_before_dedup = len(df)
        df = self.merge_same_timestamp(df, rule=self.dedup_strategy)
        n_dup = n_before_dedup - len(df)

        # 5단계 - 이상치 z-점수
        if len(df) > 1 and "mid_price" in df.columns:
            outlier_mask = self.tag_outliers(df, col="mid_price")
            n_outliers = int(outlier_mask.sum())
            df = df[~outlier_mask].copy()
        else:
            n_outliers = 0

        # 6단계 - 과도한 스프레드
        if "spread_bps" in df.columns:
            wide_mask = df["spread_bps"] > self.max_spread_bps
            df = df[~wide_mask].copy()

        n_removed = n_total - len(df)

        stats = CleaningStats(
            n_total=n_total,
            n_removed=n_removed,
            n_price_inversions=n_price_inv,
            n_zero_volume=n_zero_vol,
            n_duplicate_ts=n_dup,
            n_outliers=n_outliers,
        )

        logger.info(
            "Cleaning complete: %d/%d rows kept (%.1f%% removed)",
            len(df), n_total, stats.removal_rate * 100,
        )
        return df.reset_index(drop=True), stats

    # ------------------------------------------------------------------
    # 공개 API – LOBSnapshot list
    # ------------------------------------------------------------------

    def clean_snapshots(
        self, snapshots: list[LOBSnapshot]
    ) -> tuple[list[LOBSnapshot], CleaningStats]:
        """
        Clean a list of LOBSnapshot objects applying the same logical rules
        as clean() for DataFrames.
        """
        n_total = len(snapshots)
        n_price_inv = 0
        n_zero_vol = 0
        n_dup = 0
        kept: list[LOBSnapshot] = []

        seen_timestamps: dict[pd.Timestamp, int] = {}  # ts -> index in kept

        for snap in snapshots:
            # 가격 역전
            bb = snap.best_bid
            ba = snap.best_ask
            if bb is None or ba is None or ba <= bb:
                n_price_inv += 1
                continue

            # 잔량 0
            if snap.total_bid_depth < self.min_depth or snap.total_ask_depth < self.min_depth:
                n_zero_vol += 1
                continue

            # 스프레드 점검
            sbps = snap.spread_bps
            if sbps is not None and sbps > self.max_spread_bps:
                continue

            # 중복 제거
            ts = snap.timestamp
            if ts in seen_timestamps:
                n_dup += 1
                if self.dedup_strategy == "last":
                    kept[seen_timestamps[ts]] = snap   # replace with newer
                # if 'first', just skip
                continue

            seen_timestamps[ts] = len(kept)
            kept.append(snap)

        # 중간가 z-점수 기반 이상치 제거
        if len(kept) > 10:
            mids = np.array([s.mid_price or np.nan for s in kept], dtype=float)
            mu = np.nanmean(mids)
            sigma = np.nanstd(mids)
            n_outliers = 0
            if sigma > 0:
                zscores = np.abs((mids - mu) / sigma)
                outlier_idx = set(np.where(zscores > self.outlier_zscore)[0].tolist())
                n_outliers = len(outlier_idx)
                kept = [s for i, s in enumerate(kept) if i not in outlier_idx]
        else:
            n_outliers = 0

        n_removed = n_total - len(kept)
        stats = CleaningStats(
            n_total=n_total,
            n_removed=n_removed,
            n_price_inversions=n_price_inv,
            n_zero_volume=n_zero_vol,
            n_duplicate_ts=n_dup,
            n_outliers=n_outliers,
        )
        return kept, stats

    # ------------------------------------------------------------------
    # 도우미
    # ------------------------------------------------------------------

    def tag_outliers(
        self,
        df: pd.DataFrame,
        col: str = "mid_price",
        window: int = 100,
    ) -> pd.Series:
        """
        Return a boolean mask where True marks a rolling z-score outlier.

        The rolling window is centred so that the z-score at row i is computed
        from the local neighbourhood rather than just the past.  For simplicity
        we use a backward-looking window (standard for online systems) to avoid
        any lookahead.
        """
        if col not in df.columns or len(df) < 3:
            return pd.Series(False, index=df.index)

        prices = df[col].astype(float)
        roll = prices.rolling(window=window, min_periods=2)
        mu = roll.mean()
        sigma = roll.std(ddof=1)

        # Avoid division by zero
        safe_sigma = sigma.replace(0.0, np.nan)
        zscores = ((prices - mu) / safe_sigma).abs()

        outlier_mask = zscores > self.outlier_zscore
        return outlier_mask.fillna(False)

    @staticmethod
    def fix_negative_volumes(df: pd.DataFrame) -> pd.DataFrame:
        """Clamp negative volume columns to 0."""
        vol_cols = (
            [f"BIDP_RSQN{i}" for i in range(1, 11)]
            + [f"ASKP_RSQN{i}" for i in range(1, 11)]
            + [f"BIDP_RSQN_ICDC{i}" for i in range(1, 11)]
            + [f"ASKP_RSQN_ICDC{i}" for i in range(1, 11)]
        )
        present = [c for c in vol_cols if c in df.columns]
        for col in present:
            df[col] = df[col].clip(lower=0)
        return df

    def report(self, stats: CleaningStats) -> str:
        """Return a human-readable cleaning summary."""
        lines = [
            "=== DataCleaner Report ===",
            f"  Total rows           : {stats.n_total:,}",
            f"  Rows removed         : {stats.n_removed:,}  ({stats.removal_rate * 100:.2f}%)",
            f"  Price inversions     : {stats.n_price_inversions:,}",
            f"  Zero depth           : {stats.n_zero_volume:,}",
            f"  Duplicate timestamps : {stats.n_duplicate_ts:,}",
            f"  Outliers (z-score)   : {stats.n_outliers:,}",
            f"  Rows retained        : {stats.n_total - stats.n_removed:,}",
            "==========================",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_helper_cols(df: pd.DataFrame) -> pd.DataFrame:
        """Compute mid_price, spread_bps etc. if not already present."""
        if "best_bid" not in df.columns and "BIDP1" in df.columns:
            df["best_bid"] = pd.to_numeric(df["BIDP1"], errors="coerce")
        if "best_ask" not in df.columns and "ASKP1" in df.columns:
            df["best_ask"] = pd.to_numeric(df["ASKP1"], errors="coerce")

        if "mid_price" not in df.columns:
            if "best_bid" in df.columns and "best_ask" in df.columns:
                df["mid_price"] = (df["best_bid"] + df["best_ask"]) / 2.0

        if "spread_bps" not in df.columns:
            if "best_bid" in df.columns and "best_ask" in df.columns:
                spread = df["best_ask"] - df["best_bid"]
                mid = df["mid_price"]
                df["spread_bps"] = np.where(
                    mid > 0, (spread / mid) * 10_000.0, np.nan
                )

        if "total_bid_depth" not in df.columns:
            bid_vol_cols = [f"BIDP_RSQN{i}" for i in range(1, 11)]
            present_bv = [c for c in bid_vol_cols if c in df.columns]
            if present_bv:
                df["total_bid_depth"] = df[present_bv].clip(lower=0).sum(axis=1)

        if "total_ask_depth" not in df.columns:
            ask_vol_cols = [f"ASKP_RSQN{i}" for i in range(1, 11)]
            present_av = [c for c in ask_vol_cols if c in df.columns]
            if present_av:
                df["total_ask_depth"] = df[present_av].clip(lower=0).sum(axis=1)

        return df

    @staticmethod
    def _price_inversion_mask(df: pd.DataFrame) -> pd.Series:
        if "best_bid" not in df.columns or "best_ask" not in df.columns:
            return pd.Series(False, index=df.index)
        return (df["best_ask"] <= df["best_bid"]) | df["best_bid"].isna() | df["best_ask"].isna()

    def _zero_depth_mask(self, df: pd.DataFrame) -> pd.Series:
        mask = pd.Series(False, index=df.index)
        if "total_bid_depth" in df.columns:
            mask |= df["total_bid_depth"] < self.min_depth
        if "total_ask_depth" in df.columns:
            mask |= df["total_ask_depth"] < self.min_depth
        return mask

    @staticmethod
    def merge_same_timestamp(df: pd.DataFrame, rule: str = "last") -> pd.DataFrame:
        """Collapse duplicate timestamps using 'first' or 'last' strategy."""
        if "timestamp" not in df.columns:
            return df
        keep_map = {"first": "first", "last": "last"}
        keep = keep_map.get(rule, "last")
        return df.drop_duplicates(subset=["timestamp"], keep=keep)
