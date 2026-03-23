"""
market_state.py
---------------
Layer 0의 핵심 데이터 계약을 정의한다. LOBLevel, LOBSnapshot, MarketState는
상위 레이어가 공통으로 소비하는 기본 출력이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class LOBLevel:
    """호가창의 단일 가격 레벨."""
    price: float
    volume: int

    def __post_init__(self) -> None:
        if self.volume < 0:
            raise ValueError(f"LOBLevel volume must be non-negative, got {self.volume}")


@dataclass
class LOBSnapshot:
    """
    특정 시점의 호가창 스냅샷.

    bid_levels와 ask_levels는 최우선 호가(index 0)부터 불리한 방향의 호가
    (더 큰 index) 순으로 정렬된다. 각 측은 최대 10레벨까지 가지며
    KIS H0STASP0 깊이와 맞춘다.
    """
    timestamp: pd.Timestamp
    bid_levels: list[LOBLevel]
    ask_levels: list[LOBLevel]
    last_trade_price: Optional[float] = None
    last_trade_volume: Optional[int] = None

    def __post_init__(self) -> None:
        if len(self.bid_levels) > 10:
            raise ValueError("bid_levels may not exceed 10 levels")
        if len(self.ask_levels) > 10:
            raise ValueError("ask_levels may not exceed 10 levels")

    # ------------------------------------------------------------------
    # 최우선 호가
    # ------------------------------------------------------------------

    @property
    def best_bid(self) -> Optional[float]:
        """가장 높은 매수호가(index 0)."""
        return self.bid_levels[0].price if self.bid_levels else None

    @property
    def best_ask(self) -> Optional[float]:
        """가장 낮은 매도호가(index 0)."""
        return self.ask_levels[0].price if self.ask_levels else None

    # ------------------------------------------------------------------
    # 중간가 / 스프레드
    # ------------------------------------------------------------------

    @property
    def mid_price(self) -> Optional[float]:
        """단순 중간가: (best_bid + best_ask) / 2."""
        bb = self.best_bid
        ba = self.best_ask
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    @property
    def spread(self) -> Optional[float]:
        """절대 bid-ask 스프레드."""
        bb = self.best_bid
        ba = self.best_ask
        if bb is None or ba is None:
            return None
        return ba - bb

    @property
    def spread_bps(self) -> Optional[float]:
        """중간가 대비 베이시스포인트 단위 스프레드."""
        s = self.spread
        mid = self.mid_price
        if s is None or mid is None or mid == 0.0:
            return None
        return (s / mid) * 10_000.0

    # ------------------------------------------------------------------
    # 잔량
    # ------------------------------------------------------------------

    @property
    def total_bid_depth(self) -> int:
        """모든 레벨의 매수 잔량 합."""
        return sum(lvl.volume for lvl in self.bid_levels)

    @property
    def total_ask_depth(self) -> int:
        """모든 레벨의 매도 잔량 합."""
        return sum(lvl.volume for lvl in self.ask_levels)

    @property
    def order_imbalance(self) -> Optional[float]:
        """
        전체 호가창 기준 정규화 주문 불균형:
            (bid_vol - ask_vol) / (bid_vol + ask_vol)
        양측 잔량이 모두 비어 있으면 None을 반환한다.
        """
        bid_vol = self.total_bid_depth
        ask_vol = self.total_ask_depth
        denom = bid_vol + ask_vol
        if denom == 0:
            return None
        return (bid_vol - ask_vol) / denom

    # ------------------------------------------------------------------
    # 도우미
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """호가 역전이 없으면 True를 반환한다."""
        bb = self.best_bid
        ba = self.best_ask
        if bb is None or ba is None:
            return False
        return ba > bb


@dataclass
class MarketState:
    """
    Layer 0의 중심 출력.

    LOB 스냅샷, 최근 체결, 미리 계산된 미시구조 피처, 세션/거래 가능 메타데이터를
    하나의 객체로 묶어 상위 레이어에 전달한다.
    """
    timestamp: pd.Timestamp
    symbol: str
    lob: LOBSnapshot
    trades: Optional[pd.DataFrame] = None   # columns: timestamp, price, volume, side
    tradable: bool = True
    session: str = "regular"                # 'regular' | 'pre' | 'post' | 'halted' | 'closed'
    features: dict[str, float] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # LOBSnapshot 편의 접근자
    # ------------------------------------------------------------------

    @property
    def mid(self) -> Optional[float]:
        return self.lob.mid_price

    @property
    def spread(self) -> Optional[float]:
        return self.lob.spread

    @property
    def spread_bps(self) -> Optional[float]:
        return self.lob.spread_bps

    # ------------------------------------------------------------------
    # 문자열 표현
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"MarketState(symbol={self.symbol!r}, ts={self.timestamp}, "
            f"mid={self.mid}, spread_bps={self.spread_bps:.2f}, "
            f"session={self.session!r}, tradable={self.tradable})"
        )
