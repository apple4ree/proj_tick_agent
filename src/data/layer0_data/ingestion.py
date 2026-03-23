"""
ingestion.py
------------
KIS H0STASP0(한국거래소 LOB) CSV 파일을 적재하는 모듈.

지원하는 디렉터리 레이아웃:
    1) <data_dir>/<SYMBOL>/<YYYYMMDD>/*.csv
    2) <data_dir>/<YYYYMMDD>/<SYMBOL>.csv   (open-trading-api 실시간 내보내기)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Optional

import pandas as pd

from .market_state import LOBLevel, LOBSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 컬럼 이름 상수(KIS H0STASP0 형식)
# ---------------------------------------------------------------------------

_TIME_COLS = ("STCK_CNTG_HOUR", "BSOP_HOUR")   # HHMMSS 문자열
_DATE_COL = "BSOP_DATE"        # YYYYMMDD 문자열
_SYMBOL_COL = "MKSC_SHRN_ISCD"
_SESSION_COL = "HOUR_CLS_CODE"  # '0' = 정규장
_RECV_TS_KST_COL = "recv_ts_kst"

_ASKP_COLS = [f"ASKP{i}" for i in range(1, 11)]
_BIDP_COLS = [f"BIDP{i}" for i in range(1, 11)]
_ASKV_COLS = [f"ASKP_RSQN{i}" for i in range(1, 11)]
_BIDV_COLS = [f"BIDP_RSQN{i}" for i in range(1, 11)]
_ASKV_ICDC_COLS = [f"ASKP_RSQN_ICDC{i}" for i in range(1, 11)]
_BIDV_ICDC_COLS = [f"BIDP_RSQN_ICDC{i}" for i in range(1, 11)]

_NUMERIC_COLS = (
    _ASKP_COLS + _BIDP_COLS
    + _ASKV_COLS + _BIDV_COLS
    + _ASKV_ICDC_COLS + _BIDV_ICDC_COLS
)


@dataclass
class TickRecord:
    """
    단일 원시 틱 이벤트를 감싸는 얇은 래퍼.

    LOBSnapshot 같은 더 풍부한 도메인 객체로 바꾸기 전 단계에서 사용한다.
    """
    timestamp: pd.Timestamp
    symbol: str
    record_type: str   # 'lob' | 'trade' | 'index'
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DataIngester
# ---------------------------------------------------------------------------

class DataIngester:
    """
    원시 KIS H0STASP0 LOB CSV를 읽어 구조화된 객체로 변환한다.

    반환 형태는 DataFrame, LOBSnapshot 리스트, TickRecord 제너레이터다.
    """

    def __init__(
        self,
        data_dir: str | Path,
        schema_version: str = "h0stasp0_v1",
        layout: str = "auto",
    ) -> None:
        self.data_dir = Path(data_dir)
        self.schema_version = schema_version
        self.layout = layout

        if not self.data_dir.exists():
            raise FileNotFoundError(f"data_dir does not exist: {self.data_dir}")

    # ------------------------------------------------------------------
    # 탐색
    # ------------------------------------------------------------------

    def list_symbols(self) -> list[str]:
        """data_dir에서 찾은 심볼 하위 디렉터리 목록을 정렬해 반환한다."""
        layout = self._detect_layout()
        if layout == "symbol_date":
            return sorted(
                p.name for p in self.data_dir.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            )

        symbols: set[str] = set()
        for date_dir in self.data_dir.iterdir():
            if not date_dir.is_dir() or date_dir.name.startswith("."):
                continue
            for csv_path in date_dir.glob("*.csv"):
                symbols.add(csv_path.stem)
        return sorted(symbols)

    def list_dates(self, symbol: str) -> list[str]:
        """주어진 심볼에 대한 날짜 디렉터리 이름 목록을 정렬해 반환한다."""
        layout = self._detect_layout()
        if layout == "symbol_date":
            symbol_dir = self.data_dir / symbol
            if not symbol_dir.exists():
                raise FileNotFoundError(f"Symbol directory not found: {symbol_dir}")
            return sorted(
                p.name for p in symbol_dir.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            )

        dates = sorted(
            date_dir.name
            for date_dir in self.data_dir.iterdir()
            if date_dir.is_dir()
            and not date_dir.name.startswith(".")
            and (date_dir / f"{symbol}.csv").exists()
        )
        if not dates:
            raise FileNotFoundError(f"No date files found for symbol {symbol} under {self.data_dir}")
        return dates

    # ------------------------------------------------------------------
    # 원시 CSV 로딩
    # ------------------------------------------------------------------

    def load_raw_csv(self, symbol: str, date: str) -> pd.DataFrame:
        """
        (symbol, date)에 해당하는 모든 CSV를 읽어 정렬된 단일 DataFrame으로 반환한다.

        파일이 없으면 기대 컬럼을 가진 빈 DataFrame을 반환한다.
        """
        csv_files = self._resolve_csv_paths(symbol, date)
        if not csv_files:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for csv_path in csv_files:
            try:
                df = pd.read_csv(csv_path, dtype=str, low_memory=False)
                frames.append(df)
                logger.debug("Loaded %s (%d rows)", csv_path.name, len(df))
            except Exception as exc:
                logger.error("Failed to read %s: %s", csv_path, exc)

        if not frames:
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)

        # ------------------------------------------------------------------
        # 타임스탬프 파싱
        # ------------------------------------------------------------------
        df = self._parse_timestamp(df, fallback_date=date)

        # ------------------------------------------------------------------
        # 수치형 컬럼 변환
        # ------------------------------------------------------------------
        present_numeric = [c for c in _NUMERIC_COLS if c in df.columns]
        for col in present_numeric:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # ------------------------------------------------------------------
        # 타임스탬프 기준 정렬
        # ------------------------------------------------------------------
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp").reset_index(drop=True)

        # ------------------------------------------------------------------
        # 메타데이터 부착
        # ------------------------------------------------------------------
        df.attrs["schema_version"] = self.schema_version
        df.attrs["symbol"] = symbol
        df.attrs["date"] = date

        return df

    # ------------------------------------------------------------------
    # LOBSnapshot 변환
    # ------------------------------------------------------------------

    def load_lob_snapshots(self, symbol: str, date: str) -> list[LOBSnapshot]:
        """원시 CSV 행을 LOBSnapshot 객체 목록으로 변환한다."""
        df = self.load_raw_csv(symbol, date)
        if df.empty:
            return []
        return self._df_to_snapshots(df)

    # ------------------------------------------------------------------
    # 반복자
    # ------------------------------------------------------------------

    def iter_dates(
        self,
        symbol: str,
        dates: Optional[list[str]] = None,
    ) -> Generator[tuple[str, pd.DataFrame], None, None]:
        """
        사용 가능한 날짜(또는 요청된 날짜)에 대해 (date_str, DataFrame) 쌍을 생성한다.

        로드에 실패한 날짜는 로그만 남기고 건너뛴다.
        """
        available = self.list_dates(symbol)
        selected = dates if dates is not None else available

        for date in selected:
            if date not in available:
                logger.warning("Date %s not available for symbol %s", date, symbol)
                continue
            try:
                df = self.load_raw_csv(symbol, date)
                yield date, df
            except Exception as exc:
                logger.error(
                    "Error loading %s / %s: %s", symbol, date, exc, exc_info=True
                )

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    def _parse_timestamp(self, df: pd.DataFrame, fallback_date: str | None = None) -> pd.DataFrame:
        """
        가능한 정보 중 가장 좋은 소스를 사용해 timezone-naive pd.Timestamp를 만든다.

        파싱되지 않는 행은 경고 후 제거한다.
        """
        timestamps = None

        if _RECV_TS_KST_COL in df.columns:
            timestamps = pd.to_datetime(df[_RECV_TS_KST_COL], errors="coerce")
            if getattr(timestamps.dt, "tz", None) is not None:
                timestamps = timestamps.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)

        if timestamps is None or timestamps.isna().all():
            time_col = next((col for col in _TIME_COLS if col in df.columns), None)
            date_source = None
            if _DATE_COL in df.columns:
                date_source = df[_DATE_COL].astype(str).str.strip().str.zfill(8)
            elif fallback_date is not None:
                fallback = str(fallback_date).replace("-", "").strip()
                date_source = pd.Series([fallback] * len(df), index=df.index, dtype=str)

            if date_source is not None and time_col is not None:
                time_str = df[time_col].astype(str).str.strip().str.zfill(6)
                combined = date_source + time_str
                timestamps = pd.to_datetime(combined, format="%Y%m%d%H%M%S", errors="coerce")

        if timestamps is None:
            logger.warning("Timestamp columns not found; skipping parse")
            return df

        n_bad = timestamps.isna().sum()
        if n_bad > 0:
            logger.warning("Could not parse %d timestamp(s); those rows will be dropped", n_bad)

        df = df.copy()
        df["timestamp"] = timestamps
        df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
        return df

    def _resolve_csv_paths(self, symbol: str, date: str) -> list[Path]:
        layout = self._detect_layout()
        if layout == "symbol_date":
            date_dir = self.data_dir / symbol / date
            if not date_dir.exists():
                logger.warning("Date directory not found: %s", date_dir)
                return []
            csv_files = sorted(date_dir.glob("*.csv"))
            if not csv_files:
                logger.warning("No CSV files found in %s", date_dir)
            return csv_files

        csv_path = self.data_dir / date / f"{symbol}.csv"
        if not csv_path.exists():
            logger.warning("CSV file not found: %s", csv_path)
            return []
        return [csv_path]

    def _detect_layout(self) -> str:
        if self.layout != "auto":
            return self.layout

        entries = [entry for entry in self.data_dir.iterdir() if not entry.name.startswith(".")]
        if not entries:
            return "symbol_date"

        if any(entry.is_dir() and re.fullmatch(r"\d{8}", entry.name) for entry in entries):
            return "date_symbol_file"
        return "symbol_date"

    @staticmethod
    def _row_to_snapshot(row: pd.Series) -> Optional[LOBSnapshot]:
        """Convert a single DataFrame row to a LOBSnapshot."""
        ts = row.get("timestamp")
        if pd.isna(ts):
            return None

        bid_levels: list[LOBLevel] = []
        ask_levels: list[LOBLevel] = []

        for i in range(1, 11):
            bp = row.get(f"BIDP{i}", 0)
            bv = row.get(f"BIDP_RSQN{i}", 0)
            try:
                bp_f = float(bp)
                bv_i = int(float(bv)) if not pd.isna(bv) else 0
                if bp_f > 0:
                    bid_levels.append(LOBLevel(price=bp_f, volume=max(0, bv_i)))
            except (TypeError, ValueError):
                pass

            ap = row.get(f"ASKP{i}", 0)
            av = row.get(f"ASKP_RSQN{i}", 0)
            try:
                ap_f = float(ap)
                av_i = int(float(av)) if not pd.isna(av) else 0
                if ap_f > 0:
                    ask_levels.append(LOBLevel(price=ap_f, volume=max(0, av_i)))
            except (TypeError, ValueError):
                pass

        return LOBSnapshot(
            timestamp=pd.Timestamp(ts),
            bid_levels=bid_levels,
            ask_levels=ask_levels,
        )

    def _df_to_snapshots(self, df: pd.DataFrame) -> list[LOBSnapshot]:
        snapshots: list[LOBSnapshot] = []
        for _, row in df.iterrows():
            snap = self._row_to_snapshot(row)
            if snap is not None:
                snapshots.append(snap)
        return snapshots


class H0STASP0DataIngester(DataIngester):
    """Ingester tuned for open-trading-api realtime H0STASP0 exports."""

    def __init__(
        self,
        data_dir: str | Path,
        schema_version: str = "h0stasp0_realtime_v1",
        layout: str = "date_symbol_file",
    ) -> None:
        super().__init__(
            data_dir=data_dir,
            schema_version=schema_version,
            layout=layout,
        )
