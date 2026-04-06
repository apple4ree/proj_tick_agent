"""
strategy_loop/date_ranges.py
-----------------------------
IS/OOS 날짜 범위를 담는 데이터클래스.

사용 예:
    ranges = DateRanges(
        is_start="20260305", is_end="20260311",
        oos_start="20260312", oos_end="20260312",
    )

    # 단일 날짜 (OOS 없음)
    ranges = DateRanges.from_single_day("20260313")
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DateRanges:
    is_start: str               # YYYYMMDD — IS 시작일
    is_end: str                 # YYYYMMDD — IS 종료일
    oos_start: str | None = None  # YYYYMMDD — OOS 시작일 (None이면 OOS 없음)
    oos_end: str | None = None    # YYYYMMDD — OOS 종료일

    @property
    def has_oos(self) -> bool:
        return self.oos_start is not None and self.oos_end is not None

    @classmethod
    def from_single_day(cls, date: str) -> "DateRanges":
        """OOS 없이 단일 날짜만으로 구성 (하위 호환용)."""
        return cls(is_start=date, is_end=date)
