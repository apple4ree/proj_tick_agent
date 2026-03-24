# src/data/ — 데이터 파이프라인 (Block 1, Layer 0)

KIS H0STASP0 원시 틱 데이터를 적재하고, 정제/동기화/피처 계산을 거쳐 `MarketState`를 생성한다.

## 핵심 역할

- KIS CSV에서 10-level LOB 틱 데이터 로드
- 비정상 틱 감지/제거 (가격 역전, 이상치, 과도한 스프레드)
- 다종목 시간 정렬 및 리샘플링
- KRX 세션/VI/공휴일 분류
- 미시구조 피처 계산 (spread, imbalance, depth, impact, trade flow)
- `MarketStateBuilder`로 위 전체를 일괄 오케스트레이션

## 대표 파일 (`layer0_data/`)

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `market_state.py` | `LOBSnapshot`, `MarketState` | 시장 상태 데이터 계약 |
| `ingestion.py` | `DataIngester`, `H0STASP0DataIngester` | CSV 로드 (2가지 디렉토리 레이아웃 지원) |
| `cleaning.py` | `DataCleaner` | 가격 역전/이상치/중복 제거 |
| `synchronization.py` | `DataSynchronizer` | 시간 정렬, 리샘플링, clock drift 보정 |
| `market_calendar.py` | `SessionMask` | KRX 세션(정규/장전/장후/VI/공휴일) 분류 |
| `feature_pipeline.py` | `FeaturePipeline`, `MicrostructureFeatures` | 10종 미시구조 피처 계산 |
| `state_builder.py` | `MarketStateBuilder` | 수집→정제→동기화→캘린더→피처 일괄 실행 |

## 전체 파이프라인에서의 위치

```
KIS H0STASP0 CSV → DataIngester → DataCleaner → DataSynchronizer
  → SessionMask → FeaturePipeline → MarketStateBuilder → MarketState[]
```

`MarketState`는 이후 모든 layer(Signal 생성, 백테스트 시뮬레이션)의 입력이 된다.

## 주의사항

- 실데이터 경로는 `conf/paths.yaml`의 `data_dir`에서 설정
- 두 가지 디렉토리 레이아웃 지원: `<symbol>/<date>/*.csv` 또는 `<date>/<symbol>.csv`
- `MarketState.features`는 dict이므로 전략이 참조하는 피처 이름이 여기서 계산된 이름과 일치해야 함
- 리샘플링 주기는 `resample` 설정으로 조절 (기본 1s, baseline_mini는 10s)

## 관련 문서

- [../../PIPELINE.md](../../PIPELINE.md) — Block 1: Data 상세
- [../../ARCHITECTURE.md](../../ARCHITECTURE.md) — 시스템 블록 개요
