# layer3_order/ — 주문 관리 (Layer 3)

TargetPosition과 현재 포지션의 델타를 계산하여 ParentOrder를 생성하고, 거래소 수준 제약을 적용한다.

## 핵심 역할

- Target vs 현재 포지션 → 델타 계산 → ParentOrder 생성
- 7종 주문 유형, 5종 TIF(Time-in-Force) 정의
- 긴급도 기반 주문 유형 자동 결정
- 틱 크기/로트 크기/가격 밴드 거래소 제약 적용
- 주문 제출 시점 스케줄링 (TWAP/VWAP/POV/IS hint)

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `order_types.py` | `ParentOrder`, `ChildOrder`, `OrderSide/Type/TIF/Status` | 주문 데이터 타입 |
| `delta_compute.py` | `DeltaComputer` | Target - 현재 → ParentOrder (긴급도 = confidence) |
| `order_typing.py` | `OrderTyper` | 긴급도 + 시장상태 → 주문 유형/TIF 자동 결정 |
| `order_constraints.py` | `OrderConstraints` | 틱 크기 반올림, 로트 크기, ±30% 가격 밴드 |
| `order_scheduler.py` | `OrderScheduler`, `SchedulingHint` | 참여율, 회피 시간대, 알고리즘 제안 |

## 주문 유형

| 유형 | 용도 |
|------|------|
| MARKET | 즉시 체결 |
| LIMIT | 지정가 |
| LIMIT_IOC | 즉시 체결 후 잔량 취소 |
| LIMIT_FOK | 전량 체결 아니면 전량 취소 |
| PEG_MID | 중간가 페깅 |
| STOP, STOP_LIMIT | 조건부 주문 |

## 전체 파이프라인에서의 위치

```
TargetPosition (Layer 2) → DeltaComputer → ParentOrder → SlicingPolicy (Layer 4) → ChildOrder
```

## 주의사항

- KRX 기본 가격 밴드: 기준가 ±30%
- 긴급도 > 0.7이면 LIMIT_IOC 또는 MARKET 자동 선택
- DeltaComputer는 세션 종료 시각(15:30 KRX)을 deadline으로 설정

## 관련 문서

- [../layer2_position/README.md](../layer2_position/README.md) — TargetPosition 생성
- [../layer4_execution/README.md](../layer4_execution/README.md) — 주문 분할 및 배치
- [../../../../ADR.md](../../../../ADR.md) — ADR-003(Parent-Child 계층)
