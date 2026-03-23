# 표준 실험 프로토콜

## 개요

전략 생성 파이프라인으로 생성된 전략을 체계적으로 평가합니다.
주요 실험 축: **종목(Universe)** × **Latency**.

## 실험 워크플로우

### 1. 전략 생성 (Job 제출)

```bash
cd /home/dgu/tick/proj_rl_agent

# Shell 런처 (권장)
./scripts/submit_generation_job.sh "Order imbalance alpha"

# 또는 직접 Python
PYTHONPATH=src python scripts/generate_strategy.py \
    --goal "Order imbalance alpha"
```

### 2. 전략 검토

```bash
PYTHONPATH=src python scripts/review_strategy.py \
    strategies/imbalance_momentum_v1.0.json
```

### 3. 단일 종목 백테스트

```bash
# Job 제출 (권장)
./scripts/submit_backtest_job.sh \
    --strategy imbalance_momentum --version 1.0 \
    --symbol 005930 --start-date 2026-03-13

# 또는 직접 실행
PYTHONPATH=src python scripts/backtest.py \
    --spec strategies/imbalance_momentum_v1.0.json \
    --symbol 005930 --start-date 20260313
```

### 4. Universe 백테스트

```bash
PYTHONPATH=src python scripts/backtest_strategy_universe.py \
    --spec strategies/imbalance_momentum_v1.0.json \
    --data-dir /home/dgu/tick/open-trading-api/data/realtime/H0STASP0 \
    --start-date 20260313
```

내부 기본값: 전체 종목, latency sweep [0,50,100,500,1000]ms.

### 5. 결과 집계

```bash
PYTHONPATH=src python scripts/summarize_universe_results.py \
    --results outputs/universe_backtest/imbalance_momentum/universe_results.csv
```

## Baseline 설정 참고

`conf/backtest_base.yaml`에 백테스트 공통 기본 파라미터가 정의되어 있습니다
(config stack의 일부로 자동 로드됨):

| 카테고리 | 파라미터 | 값 | 근거 |
|----------|----------|-----|------|
| **데이터** | symbol | 005930 | 삼성전자 (KRX 대표 종목) |
| | resample | 1s | 1초 캔들 (틱 노이즈 감소) |
| **포트폴리오** | initial_cash | 1억 KRW | 기관 기준 |
| | seed | 42 | 재현성 |
| **수수료** | type | krx | KRX 실제 수수료 구조 |
| **충격** | type | linear | 선형 충격 모델 |
| **분할** | algo | TWAP | 시간 가중 분할 |
| **배치** | style | spread_adaptive | 스프레드 적응형 |

## 결과 해석 메트릭

| 메트릭 | 설명 | 좋은 값 |
|--------|------|---------|
| sharpe_ratio | 위험 조정 수익률 | > 1.0 |
| net_pnl | 총 손익 (KRW) | > 0 |
| fill_rate | 체결률 | > 0.8 |
| n_fills | 체결 수 | > 0 |
| max_drawdown | 최대 낙폭 | < 0.1 |
| is_bps | Implementation Shortfall (bps) | 낮을수록 좋음 |

## 실험 설계 원칙

1. **Universe 평가**: 단일 종목 과적합 방지를 위해 전종목 백테스트
2. **Latency 축**: 0, 50, 100, 500, 1000ms — 실전 배포 판단 기준
3. **동일 시드**: seed=42 고정으로 확률적 요소 통제
4. **통계적 유의성**: Harvey, Liu & Zhu (2016)의 t > 3.0 threshold 적용

## 주의사항

1. **실패 처리**: Universe 백테스트가 전부 실패하면 non-zero exit code로 종료됩니다.
2. **재현성**: 동일 설정으로 동일 결과가 나오는지 확인하세요.
3. **실패 리포트**: `failed_runs.json`에 실패 원인이 저장됩니다.
