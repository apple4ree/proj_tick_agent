"""
state_builder.py
----------------
원시 LOB/체결 DataFrame을 상위 레이어가 사용하는 MarketState 객체로
변환하는 통합 Layer 0 빌더.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Type

import pandas as pd

from .cleaning import CleaningStats, DataCleaner
from .feature_pipeline import FeaturePipeline
from .ingestion import DataIngester
from .market_calendar import MarketCalendar, SessionType
from .market_state import LOBLevel, LOBSnapshot, MarketState
from .synchronization import DataSynchronizer

# ------------------------------------------------------------------
# Supported resample resolutions (current phase)
# ------------------------------------------------------------------
# Only these two are officially supported.
#   - "1s"   : default public baseline
#   - "500ms": the only realism-oriented resolution in the current phase
# Other sub-second values (100ms, 250ms, …) are NOT supported.
SUPPORTED_RESAMPLE_FREQS: frozenset[str] = frozenset({"1s", "500ms"})


def validate_resample_freq(freq: str | None) -> None:
    """Raise ``ValueError`` if *freq* is not a supported resample resolution.

    ``None`` is accepted (means "no resample").
    """
    if freq is None:
        return
    if freq not in SUPPORTED_RESAMPLE_FREQS:
        raise ValueError(
            f"Unsupported resample frequency '{freq}'. "
            f"Supported values for the current phase: {sorted(SUPPORTED_RESAMPLE_FREQS)}"
        )


@dataclass
class StateBuildResult:
    """Layer 0 시장 상태 빌드 실행 결과 묶음."""

    states: list[MarketState]
    cleaned_df: pd.DataFrame
    cleaning_stats: CleaningStats
    n_input_rows: int
    n_clean_rows: int
    n_states: int
    resample_freq: str | None = None


class MarketStateBuilder:
    """
    원시 LOB/체결 입력으로부터 MarketState 시퀀스를 생성한다.

    빌더는 다음 단계를 하나로 묶는다.
      ingestion -> cleaning -> synchronization -> session mask -> feature calc

    매개변수
    ----------
    data_dir : str | Path | None
        DataIngester의 루트 디렉터리. 호출자가 DataFrame을 직접 넘기는 경우
        생략할 수 있다.
    cleaner : DataCleaner | None
        사용할 데이터 정제기.
    synchronizer : DataSynchronizer | None
        사용할 동기화기.
    calendar : MarketCalendar | None
        사용할 거래 캘린더.
    feature_pipeline : FeaturePipeline | None
        피처 계산 파이프라인.
    trade_lookback : int
        각 MarketState에 연결할 과거 체결 행의 최대 개수.
    resample_freq : str | None
        최종 상태 생성에 사용할 기본 리샘플 주기.
    ingester_cls : type[DataIngester]
        디스크에서 로드할 때 사용할 데이터 적재기 구현체.
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        cleaner: DataCleaner | None = None,
        synchronizer: DataSynchronizer | None = None,
        calendar: MarketCalendar | None = None,
        feature_pipeline: FeaturePipeline | None = None,
        trade_lookback: int = 100,
        resample_freq: str | None = None,
        ingester_cls: Type[DataIngester] = DataIngester,
    ) -> None:
        validate_resample_freq(resample_freq)
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.ingester = ingester_cls(self.data_dir) if self.data_dir is not None else None
        self.cleaner = cleaner or DataCleaner()
        self.synchronizer = synchronizer or DataSynchronizer(resample_freq=resample_freq)
        self.calendar = calendar or MarketCalendar()
        self.feature_pipeline = feature_pipeline or FeaturePipeline()
        self.trade_lookback = trade_lookback
        self.resample_freq = resample_freq

    def build_from_symbol_date(
        self,
        symbol: str,
        date: str,
        trades_df: pd.DataFrame | None = None,
        resample_freq: str | None = None,
    ) -> StateBuildResult:
        """
        디스크에서 원시 LOB 데이터를 읽어 MarketState 객체를 생성한다.
        """
        if self.ingester is None:
            raise ValueError("data_dir was not provided; cannot load symbol/date inputs")

        lob_df = self.ingester.load_raw_csv(symbol, date)
        return self.build_from_dataframes(
            lob_df=lob_df,
            symbol=symbol,
            trades_df=trades_df,
            resample_freq=resample_freq,
        )

    def build_states_from_symbol_date(
        self,
        symbol: str,
        date: str,
        trades_df: pd.DataFrame | None = None,
        resample_freq: str | None = None,
    ) -> list[MarketState]:
        return self.build_from_symbol_date(
            symbol=symbol,
            date=date,
            trades_df=trades_df,
            resample_freq=resample_freq,
        ).states

    def build_from_dataframes(
        self,
        lob_df: pd.DataFrame,
        symbol: str | None = None,
        trades_df: pd.DataFrame | None = None,
        resample_freq: str | None = None,
    ) -> StateBuildResult:
        """
        원시 또는 부분 전처리된 LOB/체결 DataFrame을 MarketState 객체로 변환한다.
        """
        validate_resample_freq(resample_freq)
        self.feature_pipeline.reset()   # clear intraday EMA state for each new day
        n_input_rows = len(lob_df)
        if lob_df.empty:
            empty_stats = CleaningStats(0, 0, 0, 0, 0, 0)
            return StateBuildResult(
                states=[],
                cleaned_df=lob_df.copy(),
                cleaning_stats=empty_stats,
                n_input_rows=0,
                n_clean_rows=0,
                n_states=0,
                resample_freq=resample_freq or self.resample_freq,
            )

        clean_df, cleaning_stats = self.cleaner.clean(lob_df)
        working_df = clean_df
        trades_norm = self._normalise_trades(trades_df)

        if trades_norm is not None:
            working_df = self.synchronizer.align_lob_trades(working_df, trades_norm)

        effective_resample = resample_freq or self.resample_freq or self.synchronizer.resample_freq
        if effective_resample:
            working_df = self.synchronizer.resample(working_df, effective_resample)

        working_df = self.synchronizer.correct_clock_drift(working_df)
        session_mask = self.calendar.build_session_mask(working_df, timestamp_col="timestamp")

        resolved_symbol = symbol or str(
            working_df.attrs.get("symbol")
            or lob_df.attrs.get("symbol")
            or "UNKNOWN"
        )

        states: list[MarketState] = []
        for idx, row in working_df.iterrows():
            snapshot = self._row_to_snapshot(row)
            if snapshot is None or not snapshot.is_valid():
                continue

            row_trades = self._slice_relevant_trades(
                trades_norm=trades_norm,
                row=row,
                timestamp=snapshot.timestamp,
            )
            features = self.feature_pipeline.compute(snapshot, row_trades).to_dict()
            session_type = session_mask.session_types[idx]

            states.append(
                MarketState(
                    timestamp=snapshot.timestamp,
                    symbol=resolved_symbol,
                    lob=snapshot,
                    trades=row_trades,
                    tradable=bool(session_mask.tradable[idx]),
                    session=self._session_to_str(session_type),
                    features=features,
                    meta={
                        "clock_drift_flag": bool(row.get("clock_drift_flag", False)),
                        "row_index": int(idx),
                        "resample_freq": effective_resample,
                    },
                )
            )

        return StateBuildResult(
            states=states,
            cleaned_df=working_df.reset_index(drop=True),
            cleaning_stats=cleaning_stats,
            n_input_rows=n_input_rows,
            n_clean_rows=len(clean_df),
            n_states=len(states),
            resample_freq=effective_resample,
        )

    def build_states_from_dataframes(
        self,
        lob_df: pd.DataFrame,
        symbol: str | None = None,
        trades_df: pd.DataFrame | None = None,
        resample_freq: str | None = None,
    ) -> list[MarketState]:
        return self.build_from_dataframes(
            lob_df=lob_df,
            symbol=symbol,
            trades_df=trades_df,
            resample_freq=resample_freq,
        ).states

    def _slice_relevant_trades(
        self,
        trades_norm: pd.DataFrame | None,
        row: pd.Series,
        timestamp: pd.Timestamp,
    ) -> pd.DataFrame | None:
        if trades_norm is not None and not trades_norm.empty:
            relevant = trades_norm[trades_norm["timestamp"] <= timestamp].tail(self.trade_lookback)
            return relevant.reset_index(drop=True) if not relevant.empty else None

        trade_price = row.get("trade_price")
        if pd.isna(trade_price):
            return None

        return pd.DataFrame(
            [
                {
                    "timestamp": timestamp,
                    "price": float(trade_price),
                    "volume": float(row.get("trade_volume", 0.0) or 0.0),
                    "side": row.get("trade_side"),
                }
            ]
        )

    @staticmethod
    def _normalise_trades(trades_df: pd.DataFrame | None) -> pd.DataFrame | None:
        if trades_df is None or trades_df.empty:
            return None

        trades = trades_df.copy()
        rename_map: dict[str, str] = {}
        if "trade_price" in trades.columns and "price" not in trades.columns:
            rename_map["trade_price"] = "price"
        if "trade_volume" in trades.columns and "volume" not in trades.columns:
            rename_map["trade_volume"] = "volume"
        if "trade_side" in trades.columns and "side" not in trades.columns:
            rename_map["trade_side"] = "side"
        if rename_map:
            trades = trades.rename(columns=rename_map)

        if "timestamp" not in trades.columns:
            raise KeyError("trades_df must contain a 'timestamp' column")

        trades["timestamp"] = pd.to_datetime(trades["timestamp"])
        if "price" in trades.columns:
            trades["price"] = pd.to_numeric(trades["price"], errors="coerce")
        if "volume" in trades.columns:
            trades["volume"] = pd.to_numeric(trades["volume"], errors="coerce").fillna(0.0)

        return trades.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _row_to_snapshot(row: pd.Series) -> LOBSnapshot | None:
        timestamp = row.get("timestamp")
        if pd.isna(timestamp):
            return None

        bid_levels: list[LOBLevel] = []
        ask_levels: list[LOBLevel] = []

        for level in range(1, 11):
            bid_price = pd.to_numeric(row.get(f"BIDP{level}", 0), errors="coerce")
            bid_volume = pd.to_numeric(row.get(f"BIDP_RSQN{level}", 0), errors="coerce")
            if pd.notna(bid_price) and bid_price > 0:
                bid_levels.append(
                    LOBLevel(
                        price=float(bid_price),
                        volume=max(0, int(bid_volume)) if pd.notna(bid_volume) else 0,
                    )
                )

            ask_price = pd.to_numeric(row.get(f"ASKP{level}", 0), errors="coerce")
            ask_volume = pd.to_numeric(row.get(f"ASKP_RSQN{level}", 0), errors="coerce")
            if pd.notna(ask_price) and ask_price > 0:
                ask_levels.append(
                    LOBLevel(
                        price=float(ask_price),
                        volume=max(0, int(ask_volume)) if pd.notna(ask_volume) else 0,
                    )
                )

        return LOBSnapshot(
            timestamp=pd.Timestamp(timestamp),
            bid_levels=bid_levels,
            ask_levels=ask_levels,
            last_trade_price=(
                float(row["trade_price"])
                if "trade_price" in row.index and pd.notna(row["trade_price"])
                else None
            ),
            last_trade_volume=(
                int(row["trade_volume"])
                if "trade_volume" in row.index and pd.notna(row["trade_volume"])
                else None
            ),
        )

    @staticmethod
    def _session_to_str(session_type: SessionType) -> str:
        mapping = {
            SessionType.REGULAR: "regular",
            SessionType.PRE_MARKET: "pre",
            SessionType.POST_MARKET: "post",
            SessionType.HALTED: "halted",
            SessionType.CLOSED: "closed",
            SessionType.VI_TRIGGERED: "vi_triggered",
        }
        return mapping.get(session_type, "closed")
