"""
feature_pipeline.py
-------------------
Microstructure feature computation for LOB snapshots.

Features computed:
  - Spread in bps
  - Order imbalance (full book and level-1 only)
  - Log bid/ask depth
  - Mid price
  - Price impact for a fixed-size order (walk-the-book)
  - Trade flow (signed net volume over recent trades)
  - Volume surprise (short-term vs long-term volume ratio)
"""
from __future__ import annotations

import collections
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .market_state import LOBSnapshot

logger = logging.getLogger(__name__)

_EPS = 1e-9  # small constant to avoid division by zero


# ---------------------------------------------------------------------------
# MicrostructureFeatures
# ---------------------------------------------------------------------------

@dataclass
class MicrostructureFeatures:
    """All microstructure features derived from a single LOB snapshot."""

    spread_bps: float
    order_imbalance: float          # full-book (bid_vol - ask_vol) / (bid_vol + ask_vol)
    depth_imbalance_l1: float       # level-1 only imbalance
    log_bid_depth: float            # log1p(total_bid_depth)
    log_ask_depth: float            # log1p(total_ask_depth)
    mid_price: float
    price_impact_buy_bps: float     # cost to buy `impact_shares` shares (walk-book)
    price_impact_sell_bps: float    # cost to sell `impact_shares` shares (walk-book)
    trade_flow: Optional[float] = None      # net signed volume (recent trades)
    volume_surprise: Optional[float] = None

    # derived temporal features (computed by FeaturePipeline across ticks)
    order_imbalance_ema: float = 0.0        # EMA of order_imbalance (α=0.2, ~5-tick decay)
    order_imbalance_delta: float = 0.0      # order_imbalance[t] − order_imbalance[t−5]
    trade_flow_imbalance_ema: float = 0.0   # EMA of trade_flow_imbalance (α=0.2)
    depth_imbalance_ema: float = 0.0        # EMA of depth_imbalance (α=0.2)
    spread_bps_ema: float = 0.0             # EMA of spread_bps (α=0.1, slow baseline)

    # ------------------------------------------------------------------
    # 변환 도우미
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, float]:
        return {
            "spread_bps": self.spread_bps,
            "order_imbalance": self.order_imbalance,
            "depth_imbalance_l1": self.depth_imbalance_l1,
            "log_bid_depth": self.log_bid_depth,
            "log_ask_depth": self.log_ask_depth,
            "mid_price": self.mid_price,
            "price_impact_buy_bps": self.price_impact_buy_bps,
            "price_impact_sell_bps": self.price_impact_sell_bps,
            "trade_flow": self.trade_flow if self.trade_flow is not None else 0.0,
            "volume_surprise": self.volume_surprise if self.volume_surprise is not None else 0.0,
            "order_imbalance_ema": self.order_imbalance_ema,
            "order_imbalance_delta": self.order_imbalance_delta,
            "trade_flow_imbalance_ema": self.trade_flow_imbalance_ema,
            "depth_imbalance_ema": self.depth_imbalance_ema,
            "spread_bps_ema": self.spread_bps_ema,
        }

    def to_array(self) -> np.ndarray:
        d = self.to_dict()
        return np.array([d[k] for k in self.feature_names()], dtype=np.float32)

    @classmethod
    def feature_names(cls) -> list[str]:
        return [
            "spread_bps",
            "order_imbalance",
            "depth_imbalance_l1",
            "log_bid_depth",
            "log_ask_depth",
            "mid_price",
            "price_impact_buy_bps",
            "price_impact_sell_bps",
            "trade_flow",
            "volume_surprise",
            "order_imbalance_ema",
            "order_imbalance_delta",
            "trade_flow_imbalance_ema",
            "depth_imbalance_ema",
            "spread_bps_ema",
        ]


# ---------------------------------------------------------------------------
# FeaturePipeline
# ---------------------------------------------------------------------------

class FeaturePipeline:
    """
    Computes MicrostructureFeatures for one or many LOBSnapshots.

    매개변수
    ----------
    impact_shares : int
        Number of shares used to measure price impact (walk-the-book).
    trade_window : int
        Number of most recent trades to use when computing trade_flow
        and volume_surprise.
    """

    _ALPHA_FAST: float = 0.2   # ~5-tick EMA (order_imbalance, trade_flow_imbalance, depth_imbalance)
    _ALPHA_SLOW: float = 0.1   # ~10-tick EMA (spread baseline)
    _DELTA_LAG: int = 5        # ticks back for order_imbalance_delta

    def __init__(self, impact_shares: int = 1_000, trade_window: int = 10) -> None:
        self.impact_shares = impact_shares
        self.trade_window = trade_window
        # stateful EMA accumulators — reset at the start of each trading day
        self._oi_ema: Optional[float] = None
        self._tfi_ema: Optional[float] = None
        self._di_ema: Optional[float] = None
        self._spread_ema: Optional[float] = None
        self._oi_history: collections.deque = collections.deque(maxlen=self._DELTA_LAG + 1)

    def reset(self) -> None:
        """Reset intraday EMA state. Call at the beginning of each new trading day."""
        self._oi_ema = None
        self._tfi_ema = None
        self._di_ema = None
        self._spread_ema = None
        self._oi_history.clear()

    # ------------------------------------------------------------------
    # 주요 진입점
    # ------------------------------------------------------------------

    def compute(
        self,
        lob: LOBSnapshot,
        trades: Optional[pd.DataFrame] = None,
    ) -> MicrostructureFeatures:
        """
        Compute all microstructure features for a single LOBSnapshot.

        매개변수
        ----------
        lob : LOBSnapshot
        trades : pd.DataFrame | None
            Recent trade records.  Expected columns: timestamp, price (or
            trade_price), volume (or trade_volume), side (or trade_side).
            Rows should be sorted ascending by timestamp.

        반환값
        -------
        MicrostructureFeatures
        """
        mid = lob.mid_price or 0.0

        # --- Spread ---
        spread_bps = lob.spread_bps or 0.0

        # --- Full-book imbalance ---
        bid_depth = lob.total_bid_depth
        ask_depth = lob.total_ask_depth
        total_depth = bid_depth + ask_depth
        order_imbalance = (bid_depth - ask_depth) / (total_depth + _EPS)

        # --- Level-1 imbalance ---
        b1_vol = lob.bid_levels[0].volume if lob.bid_levels else 0
        a1_vol = lob.ask_levels[0].volume if lob.ask_levels else 0
        l1_total = b1_vol + a1_vol
        depth_imbalance_l1 = (b1_vol - a1_vol) / (l1_total + _EPS)

        # --- Log depth ---
        log_bid_depth = math.log1p(bid_depth)
        log_ask_depth = math.log1p(ask_depth)

        # --- Price impact ---
        price_impact_buy_bps = self.walk_the_book(lob, "buy", self.impact_shares)
        price_impact_sell_bps = self.walk_the_book(lob, "sell", self.impact_shares)

        # --- Trade-based features ---
        trade_flow: Optional[float] = None
        volume_surprise: Optional[float] = None
        if trades is not None and not trades.empty:
            trades_norm = self._normalise_trade_df(trades)
            trade_flow = self.compute_trade_flow(trades_norm, window=self.trade_window)
            volume_surprise = self.compute_volume_surprise(
                trades_norm,
                short_window=self.trade_window,
                long_window=max(self.trade_window * 10, 100),
            )

        # --- Derived temporal features ---
        # EMA update (initialize on first call, then exponential smoothing)
        def _ema(prev: Optional[float], val: float, alpha: float) -> float:
            return val if prev is None else alpha * val + (1.0 - alpha) * prev

        self._oi_ema = _ema(self._oi_ema, order_imbalance, self._ALPHA_FAST)
        self._spread_ema = _ema(self._spread_ema, spread_bps, self._ALPHA_SLOW)

        # depth_imbalance (L1) as proxy — same range as order_imbalance
        self._di_ema = _ema(self._di_ema, depth_imbalance_l1, self._ALPHA_FAST)

        # trade_flow_imbalance from trades (0.0 when unavailable)
        tfi_raw: float = 0.0
        if trades is not None and not trades.empty:
            trades_norm2 = self._normalise_trade_df(trades)
            if "side" in trades_norm2.columns:
                recent_t = trades_norm2.tail(self.trade_window)
                signs = recent_t["side"].apply(
                    lambda s: 1.0 if str(s).lower() in ("buy", "b", "1") else -1.0
                )
                n = len(signs)
                if n > 0:
                    tfi_raw = float(signs.sum() / n)
        self._tfi_ema = _ema(self._tfi_ema, tfi_raw, self._ALPHA_FAST)

        # 5-tick delta of order_imbalance
        self._oi_history.append(order_imbalance)
        if len(self._oi_history) == self._DELTA_LAG + 1:
            oi_delta = order_imbalance - self._oi_history[0]
        else:
            oi_delta = 0.0

        return MicrostructureFeatures(
            spread_bps=spread_bps,
            order_imbalance=order_imbalance,
            depth_imbalance_l1=depth_imbalance_l1,
            log_bid_depth=log_bid_depth,
            log_ask_depth=log_ask_depth,
            mid_price=mid,
            price_impact_buy_bps=price_impact_buy_bps,
            price_impact_sell_bps=price_impact_sell_bps,
            trade_flow=trade_flow,
            volume_surprise=volume_surprise,
            order_imbalance_ema=self._oi_ema,
            order_imbalance_delta=oi_delta,
            trade_flow_imbalance_ema=self._tfi_ema,
            depth_imbalance_ema=self._di_ema,
            spread_bps_ema=self._spread_ema,
        )

    # ------------------------------------------------------------------
    # 호가 소진 기준 가격 충격
    # ------------------------------------------------------------------

    def walk_the_book(
        self,
        lob: LOBSnapshot,
        side: str,
        shares: int,
    ) -> float:
        """
        Simulate a market order for `shares` shares and compute the average
        fill price relative to mid, expressed in bps.

        매개변수
        ----------
        side : str
            'buy'  → walk ask levels (we lift the offer).
            'sell' → walk bid levels (we hit the bid).
        shares : int

        반환값
        -------
        float
            Price impact in basis points.  Positive means cost (adverse move).
            반환값 0.0 when no mid price or book is empty.
        """
        mid = lob.mid_price
        if mid is None or mid == 0.0 or shares <= 0:
            return 0.0

        levels = lob.ask_levels if side == "buy" else lob.bid_levels
        if not levels:
            return 0.0

        remaining = shares
        total_cost = 0.0
        total_filled = 0

        for level in levels:
            if remaining <= 0:
                break
            fill = min(remaining, level.volume)
            total_cost += fill * level.price
            total_filled += fill
            remaining -= fill

        if total_filled == 0:
            return 0.0

        avg_price = total_cost / total_filled
        impact_bps = ((avg_price - mid) / mid) * 10_000.0
        if side == "sell":
            impact_bps = -impact_bps   # selling below mid is also positive cost

        return max(impact_bps, 0.0)

    # ------------------------------------------------------------------
    # Trade flow
    # ------------------------------------------------------------------

    @staticmethod
    def compute_trade_flow(trades: pd.DataFrame, window: int) -> float:
        """
        Net signed volume over the last `window` trades.

        Assumes a 'signed_volume' column exists (positive = buyer-initiated,
        negative = seller-initiated) or a 'side' column with values
        'buy'/'B' or 'sell'/'S'.

        반환값
        -------
        float
            Positive → net buying pressure.  Negative → net selling.
        """
        if trades.empty:
            return 0.0

        recent = trades.tail(window)

        if "signed_volume" in recent.columns:
            return float(recent["signed_volume"].sum())

        # Derive signed volume from side + volume
        if "volume" in recent.columns and "side" in recent.columns:
            vol = recent["volume"].fillna(0).astype(float)
            sign = recent["side"].apply(
                lambda s: 1.0 if str(s).lower() in ("buy", "b", "1") else -1.0
            )
            return float((vol * sign).sum())

        # Fallback: use price changes as a proxy
        if "price" in recent.columns and len(recent) > 1:
            price_diff = recent["price"].diff().fillna(0)
            vol = recent["volume"].fillna(1.0).astype(float) if "volume" in recent.columns else pd.Series(1.0, index=recent.index)
            return float((vol * np.sign(price_diff)).sum())

        return 0.0

    # ------------------------------------------------------------------
    # Volume surprise
    # ------------------------------------------------------------------

    @staticmethod
    def compute_volume_surprise(
        trades: pd.DataFrame,
        short_window: int = 10,
        long_window: int = 100,
    ) -> float:
        """
        Short-term volume / long-term rolling average volume – 1.

        A value of 0 means volume is at its historical average.
        Positive values indicate a volume surge; negative means below average.

        반환값 0.0 when there are insufficient data points.
        """
        if trades.empty or "volume" not in trades.columns:
            return 0.0

        vol = trades["volume"].fillna(0).astype(float)

        if len(vol) < short_window:
            return 0.0

        short_mean = vol.tail(short_window).mean()
        long_mean = vol.tail(long_window).mean() if len(vol) >= long_window else vol.mean()

        if long_mean < _EPS:
            return 0.0

        return float(short_mean / long_mean - 1.0)

    # ------------------------------------------------------------------
    # Batch computation
    # ------------------------------------------------------------------

    def compute_batch(
        self,
        snapshots: list[LOBSnapshot],
        trades_df: Optional[pd.DataFrame] = None,
    ) -> list[MicrostructureFeatures]:
        """
        Compute features for a list of LOBSnapshots.

        When trades_df is provided its rows are split per snapshot timestamp:
        for each snapshot only trades strictly before (or at) that timestamp
        are passed to compute().

        매개변수
        ----------
        snapshots : list[LOBSnapshot]
        trades_df : pd.DataFrame | None
            Full trade history for the day.

        반환값
        -------
        list[MicrostructureFeatures]  (same length as snapshots)
        """
        features: list[MicrostructureFeatures] = []
        trades_norm: Optional[pd.DataFrame] = None

        if trades_df is not None and not trades_df.empty:
            trades_norm = self._normalise_trade_df(trades_df)
            if "timestamp" in trades_norm.columns:
                trades_norm = trades_norm.sort_values("timestamp")

        for snap in snapshots:
            if trades_norm is not None and "timestamp" in trades_norm.columns:
                # Use only trades up to (and including) this snapshot timestamp
                relevant = trades_norm[trades_norm["timestamp"] <= snap.timestamp]
            else:
                relevant = trades_norm

            feat = self.compute(snap, relevant)
            features.append(feat)

        return features

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_trade_df(trades: pd.DataFrame) -> pd.DataFrame:
        """Ensure canonical column names: timestamp, price, volume, side."""
        df = trades.copy()
        rename_map: dict[str, str] = {}
        if "trade_price" in df.columns and "price" not in df.columns:
            rename_map["trade_price"] = "price"
        if "trade_volume" in df.columns and "volume" not in df.columns:
            rename_map["trade_volume"] = "volume"
        if "trade_side" in df.columns and "side" not in df.columns:
            rename_map["trade_side"] = "side"
        if rename_map:
            df = df.rename(columns=rename_map)
        return df
