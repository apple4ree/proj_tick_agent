"""
market_calendar.py
------------------
한국거래소(KRX) 시장 캘린더와 세션 분류를 정의한다.

세션 시간(한국 표준시, UTC+9):
    장전     : 08:00 - 09:00
    정규장   : 09:00 - 15:30
    장후     : 15:30 - 16:00  (동시호가/시간외)
    장종료   : 그 외 모든 시간

세션 마스크를 만들 때 DataFrame에 선택적 `vi_col`이 있으면
변동성 완화 장치(VI)도 함께 반영한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, time
from enum import Enum, auto
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SessionType
# ---------------------------------------------------------------------------

class SessionType(Enum):
    REGULAR = auto()
    PRE_MARKET = auto()
    POST_MARKET = auto()
    HALTED = auto()
    CLOSED = auto()
    VI_TRIGGERED = auto()


# ---------------------------------------------------------------------------
# SessionMask
# ---------------------------------------------------------------------------

@dataclass
class SessionMask:
    """
    DatetimeIndex와 행별 세션 분류 배열을 함께 보관한다.

    session_types  : array of SessionType values (dtype=object)
    tradable       : 불리언 배열. 정규장이면서 휴장이 아닌 행에서만 True
    """
    timestamps: pd.DatetimeIndex
    session_types: np.ndarray   # shape (N,), dtype=object  (SessionType values)
    tradable: np.ndarray        # shape (N,), dtype=bool

    def filter_regular(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        session == REGULAR 이고 tradable == True 인 행만 남긴 df 사본을 반환한다.

        df의 행 순서는 self.timestamps와 동일하다고 가정한다.
        """
        mask = (self.session_types == SessionType.REGULAR) & self.tradable
        return df.iloc[mask].reset_index(drop=True)

    def get_session(self, ts: pd.Timestamp) -> SessionType:
        """정확한 인덱스 일치로 특정 타임스탬프의 세션 유형을 조회한다."""
        try:
            idx = self.timestamps.get_loc(ts)
            return self.session_types[idx]
        except KeyError:
            return SessionType.CLOSED


# ---------------------------------------------------------------------------
# MarketCalendar
# ---------------------------------------------------------------------------

class MarketCalendar:
    """
    세션 분류와 휴장일 인식을 포함한 KRX 거래 캘린더.

    모든 시간은 한국 표준시(UTC+9)로 해석한다. 이 캘린더는 시간대 변환을
    수행하지 않으므로, 호출자는 타임스탬프를 KST 기준 naive 값으로 주거나
    미리 변환해 두어야 한다.
    """

    REGULAR_OPEN: time = time(9, 0)
    REGULAR_CLOSE: time = time(15, 30)
    PRE_MARKET_OPEN: time = time(8, 0)
    PRE_MARKET_CLOSE: time = time(9, 0)
    POST_MARKET_OPEN: time = time(15, 30)
    POST_MARKET_CLOSE: time = time(16, 0)

    # 알려진 KRX 휴장일(yyyy-mm-dd). 필요하면 확장한다.
    _KNOWN_HOLIDAYS: frozenset[date] = frozenset({
        # --- 2024 ---
        date(2024, 1, 1),    # New Year's Day
        date(2024, 2, 9),    # Lunar New Year
        date(2024, 2, 12),   # Lunar New Year holiday
        date(2024, 3, 1),    # Independence Movement Day
        date(2024, 4, 10),   # Parliamentary election
        date(2024, 5, 5),    # Children's Day
        date(2024, 5, 6),    # Substitute holiday
        date(2024, 5, 15),   # Buddha's Birthday
        date(2024, 6, 6),    # Memorial Day
        date(2024, 8, 15),   # Liberation Day
        date(2024, 9, 16),   # Chuseok
        date(2024, 9, 17),   # Chuseok
        date(2024, 9, 18),   # Chuseok
        date(2024, 10, 3),   # National Foundation Day
        date(2024, 10, 9),   # Hangul Day
        date(2024, 12, 25),  # Christmas
        date(2024, 12, 31),  # Year-end market close
        # --- 2025 ---
        date(2025, 1, 1),    # New Year's Day
        date(2025, 1, 28),   # Lunar New Year Eve
        date(2025, 1, 29),   # Lunar New Year
        date(2025, 1, 30),   # Lunar New Year holiday
        date(2025, 3, 1),    # Independence Movement Day
        date(2025, 5, 5),    # Children's Day
        date(2025, 5, 6),    # Buddha's Birthday (observed)
        date(2025, 6, 6),    # Memorial Day
        date(2025, 8, 15),   # Liberation Day
        date(2025, 10, 3),   # National Foundation Day
        date(2025, 10, 6),   # Chuseok
        date(2025, 10, 7),   # Chuseok
        date(2025, 10, 8),   # Chuseok
        date(2025, 10, 9),   # Hangul Day
        date(2025, 12, 25),  # Christmas
        date(2025, 12, 31),  # Year-end market close
        # --- 2026 ---
        date(2026, 1, 1),    # New Year's Day
        date(2026, 2, 16),   # Lunar New Year Eve
        date(2026, 2, 17),   # Lunar New Year
        date(2026, 2, 18),   # Lunar New Year holiday
        date(2026, 3, 2),    # Independence Movement Day (observed)
        date(2026, 5, 5),    # Children's Day
        date(2026, 5, 25),   # Buddha's Birthday
        date(2026, 6, 6),    # Memorial Day (Saturday – effective next weekday varies)
        date(2026, 8, 17),   # Liberation Day (observed)
        date(2026, 9, 24),   # Chuseok
        date(2026, 9, 25),   # Chuseok
        date(2026, 12, 25),  # Christmas
        date(2026, 12, 31),  # Year-end market close
    })

    def __init__(self, extra_holidays: Optional[set[date]] = None) -> None:
        """
        매개변수
        ----------
        extra_holidays : set[date] | None
            기본 휴장일 목록에 추가로 합칠 휴장일 집합.
        """
        self.holidays: set[date] = set(self._KNOWN_HOLIDAYS)
        if extra_holidays:
            self.holidays.update(extra_holidays)

    # ------------------------------------------------------------------
    # 일 단위 조회
    # ------------------------------------------------------------------

    def is_trading_day(self, d: date) -> bool:
        """d가 평일이면서 KRX 휴장일이 아니면 True를 반환한다."""
        return d.weekday() < 5 and d not in self.holidays

    # ------------------------------------------------------------------
    # 타임스탬프 단위 조회
    # ------------------------------------------------------------------

    def get_session_type(self, ts: pd.Timestamp) -> SessionType:
        """단일 타임스탬프를 SessionType으로 분류한다."""
        d = ts.date()
        if not self.is_trading_day(d):
            return SessionType.CLOSED

        t = ts.time()

        if self.PRE_MARKET_OPEN <= t < self.PRE_MARKET_CLOSE:
            return SessionType.PRE_MARKET
        if self.REGULAR_OPEN <= t < self.REGULAR_CLOSE:
            return SessionType.REGULAR
        if self.POST_MARKET_OPEN <= t < self.POST_MARKET_CLOSE:
            return SessionType.POST_MARKET
        return SessionType.CLOSED

    def is_tradable(self, ts: pd.Timestamp) -> bool:
        """
        타임스탬프가 휴장이 아닌 거래일의 정규장에 속할 때만 True를 반환한다.
        """
        return self.get_session_type(ts) == SessionType.REGULAR

    # ------------------------------------------------------------------
    # DataFrame 단위 작업
    # ------------------------------------------------------------------

    def build_session_mask(
        self,
        df: pd.DataFrame,
        timestamp_col: str = "timestamp",
    ) -> SessionMask:
        """
        df의 각 행에 대한 SessionMask를 생성한다.

        매개변수
        ----------
        df : pd.DataFrame
        timestamp_col : str

        반환값
        -------
        SessionMask
        """
        if timestamp_col not in df.columns:
            raise KeyError(f"Column '{timestamp_col}' not found in DataFrame")

        timestamps = pd.DatetimeIndex(df[timestamp_col])
        session_types = np.array(
            [self.get_session_type(ts) for ts in timestamps], dtype=object
        )
        tradable = np.array(
            [st == SessionType.REGULAR for st in session_types], dtype=bool
        )

        return SessionMask(
            timestamps=timestamps,
            session_types=session_types,
            tradable=tradable,
        )

    def filter_regular_hours(
        self,
        df: pd.DataFrame,
        timestamp_col: str = "timestamp",
    ) -> pd.DataFrame:
        """
        휴장이 아닌 거래일의 정규장에 속한 행만 남긴다.

        매개변수
        ----------
        df : pd.DataFrame
        timestamp_col : str

        반환값
        -------
        pd.DataFrame(인덱스 재설정)
        """
        if df.empty or timestamp_col not in df.columns:
            return df.copy()

        mask = self.build_session_mask(df, timestamp_col)
        regular_mask = (mask.session_types == SessionType.REGULAR) & mask.tradable

        filtered = df.iloc[regular_mask].reset_index(drop=True)
        n_removed = len(df) - len(filtered)
        if n_removed > 0:
            logger.debug(
                "filter_regular_hours: removed %d non-regular rows", n_removed
            )
        return filtered

    def get_vi_mask(
        self,
        df: pd.DataFrame,
        vi_col: Optional[str] = None,
    ) -> np.ndarray:
        """
        Return a boolean array where True indicates a VI (Volatility
        Interruption) is active.

        If vi_col is provided and present in df, its boolean/int values are
        used directly.  Otherwise returns an all-False array of length len(df).

        매개변수
        ----------
        df : pd.DataFrame
        vi_col : str | None
            Column name containing VI flags (truthy = VI active).

        반환값
        -------
        np.ndarray  dtype=bool, shape (len(df),)
        """
        if vi_col is not None and vi_col in df.columns:
            return df[vi_col].astype(bool).to_numpy()
        return np.zeros(len(df), dtype=bool)
