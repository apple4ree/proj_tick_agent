# layer5_simulator/ — 체결 시뮬레이션 (Layer 5)

ChildOrder를 LOB 기반으로 체결 시뮬레이션하고, 시장충격/수수료/지연을 적용하여 FillEvent를 생성한다.

## 핵심 역할

- LOB 기반 주문 매칭 (5종 대기열 모델, 2종 거래소 모델)
- 시장충격 모델링 (Linear, SquareRoot, Zero)
- KRX 수수료/세금 (매수 1.5bps, 매도 19.5bps KOSPI)
- 확률적 latency 시뮬레이션 (colocation/retail/zero 프리셋)
- FIFO 비용 기준 계좌 관리
- VI/거래정지 마이크로 이벤트 감지

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `matching_engine.py` | `MatchingEngine` | LOB walk + 대기열 시뮬레이션, TIF/GTX 강제 |
| `impact_model.py` | `LinearImpact`, `SquareRootImpact`, `ZeroImpact`, `SpreadCostModel` | 시장충격 + 스프레드 비용 |
| `fee_model.py` | `KRXFeeModel`, `ZeroFeeModel` | 수수료 + 증권거래세 (KOSPI/KOSDAQ) |
| `latency_model.py` | `LatencyModel`, `LatencyProfile` | 확률적 지연 + 관측 지연 적용 |
| `bookkeeper.py` | `Bookkeeper`, `FillEvent`, `AccountState` | 체결 기록, FIFO PnL, 계좌 상태 |
| `order_book.py` | `OrderBookSimulator` | LOB 쿼리 (best quote, walk book, depth) |
| `micro_events.py` | `MicroEventHandler` | VI/거래정지/세션 변경 감지 |

## 대기열 모델 (5종)

| 모델 | 특성 |
|------|------|
| PRICE_TIME | 표준 FIFO |
| RISK_ADVERSE | 보수적 추정 |
| PROB_QUEUE (기본) | q² 기반 적당히 낙관적 |
| PRO_RATA | 비례 배분 |
| RANDOM | 균일 랜덤 |

## 수수료 구조 (KRX)

- 매수: 위탁수수료 1.5bps
- 매도: 위탁수수료 1.5bps + 증권거래세 (KOSPI 18bps / KOSDAQ 20bps)
- BUY/SELL 비대칭이 전략에 직접 영향 (SELL이 ~13배 비쌈)

## 전체 파이프라인에서의 위치

```
ChildOrder (Layer 4) + LOBSnapshot
  → MatchingEngine → FillEvent
  → ImpactModel 가격 보정
  → FeeModel 수수료 계산
  → LatencyModel 지연 적용
  → Bookkeeper 계좌 갱신
  → PnLLedger (Layer 6)
```

## 현재 제한사항

- `OrderBookSimulator`는 fill 후 내부 LOB를 수정하지 않음 (실제 스냅샷 = ground truth)
- `MicroEventHandler`: VI/거래정지/세션 변경은 구현됨. PRICE_BAND_CHANGE, CIRCUIT_BREAKER는 정의만 존재
- Production OMS가 아님. 시뮬레이션 전용

## 관련 문서

- [../../execution_planning/layer4_execution/README.md](../../execution_planning/layer4_execution/README.md) — ChildOrder 생성
- [../../evaluation_orchestration/layer6_evaluator/README.md](../../evaluation_orchestration/layer6_evaluator/README.md) — FillEvent 기반 메트릭
- [../../../../ADR.md](../../../../ADR.md) — ADR-005(KRX 수수료), ADR-012(대기열 모델)
