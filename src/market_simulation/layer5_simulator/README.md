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
| `matching_engine.py` | `MatchingEngine` | LOB walk + 순수 매칭 (price/qty/exchange-model), TIF/GTX 강제. 대기열 시뮬레이션은 FillSimulator(layer7) 전담 |
| `impact_model.py` | `LinearImpact`, `SquareRootImpact`, `ZeroImpact`, `SpreadCostModel` | 시장충격 + 스프레드 비용 |
| `fee_model.py` | `KRXFeeModel`, `ZeroFeeModel` | 수수료 + 증권거래세 (KOSPI/KOSDAQ) |
| `latency_model.py` | `LatencyModel`, `LatencyProfile` | 확률적 지연 + 관측 지연 적용 |
| `bookkeeper.py` | `Bookkeeper`, `FillEvent`, `AccountState` | 체결 기록, FIFO PnL, 계좌 상태 |
| `order_book.py` | `OrderBookSimulator` | LOB 쿼리 (best quote, walk book, depth) |
| `micro_events.py` | `MicroEventHandler` | VI/거래정지/세션 변경 감지 |

## 대기열 모델 (6종, FillSimulator 전담)

| 모델 | 유형 | 특성 |
|------|------|------|
| NONE | — | Queue gate 비활성 |
| PRICE_TIME | Gate-only | 표준 FIFO conservative (trade-only advancement) |
| RISK_ADVERSE | Gate-only | 보수적 추정 (trade-only advancement) |
| PROB_QUEUE (기본) | Gate-only | trade + depth-drop partial credit |
| RANDOM | Gate-only | trade + stochastic depth-drop (seed-deterministic) |
| PRO_RATA | Gate+Allocation | conservative gate + size-proportional fill cap |

> **Fill-rule ownership contract:**
> `QueueModel` enum은 `matching_engine.py`에 backward-compat용으로 남아 있지만,
> 대기열 판단 로직(queue initialization / advancement / gate / fill allocation)은
> **FillSimulator** (layer7)가 `queue_models/` 패키지의 명시적 `QueueModel` 인터페이스를
> 통해 단독으로 수행한다. MatchingEngine은 순수 매칭(price/qty/exchange-model)만 담당하며
> queue state를 해석하지 않는다.
>
> 이 계약은 `tests/test_backtest_realism.py::TestFillRuleOwnership`과
> `tests/test_backtest_realism.py::TestMatchingEngineQueueFree`에서 regression test로
> 고정되어 있다. Queue logic을 MatchingEngine에 다시 넣지 말 것.

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
