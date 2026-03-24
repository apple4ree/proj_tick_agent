# src/market_simulation/ — 시장 시뮬레이션 (Block 4, Layer 5)

ChildOrder를 LOB 기반으로 체결 시뮬레이션하고, latency/impact/fee를 적용하여 FillEvent를 생성한다.

## 핵심 역할

- LOB 기반 주문 매칭 (5종 대기열 모델)
- 시장충격 모델링 (Linear, SquareRoot, Zero)
- KRX 수수료/세금 계산 (매도 시 증권거래세 포함)
- 주문 지연(latency) 확률적 시뮬레이션
- 체결 기록 및 계좌 상태(FIFO P&L) 관리
- VI/거래정지 등 마이크로 이벤트 처리

## 하위 디렉토리

| 디렉토리 | 역할 |
|----------|------|
| `layer5_simulator/` | 체결 엔진, 충격/수수료/지연 모델, 장부기록, LOB 쿼리, 마이크로 이벤트 |

## 전체 파이프라인에서의 위치

Execution Planning(Block 3)이 생성한 ChildOrder를 받아 체결 시뮬레이션을 수행하고, FillEvent를 Evaluation(Block 5)으로 전달한다.

```
ChildOrder + LOB → MatchingEngine → FillEvent
  → ImpactModel, FeeModel, LatencyModel 적용
  → Bookkeeper → 계좌 상태 갱신
```

## 현재 제한사항

- MicroEventHandler의 PRICE_BAND_CHANGE, CIRCUIT_BREAKER는 정의만 되어 있고 로직 최소
- OrderBookSimulator는 fill 후 내부 LOB를 수정하지 않음 (실제 스냅샷을 ground truth로 취급)

## 관련 문서

- [../../PIPELINE.md](../../PIPELINE.md) — Block 4: Market Simulation 상세
- [../../ADR.md](../../ADR.md) — ADR-005(KRX 수수료), ADR-012(대기열 모델)
