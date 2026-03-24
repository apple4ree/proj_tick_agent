# src/execution_planning/ — 실행 계획 (Block 3, Layer 1~4)

Signal을 받아 target position을 계산하고, 주문을 생성/분할/배치하는 실행 계획 블록이다.

## 핵심 역할

- Signal → TargetPosition 변환 (sizing, risk caps)
- Target → ParentOrder 델타 계산
- ParentOrder → ChildOrder 분할 (TWAP/VWAP/POV/Almgren-Chriss)
- ChildOrder 배치 정책 (Aggressive/Passive/SpreadAdaptive)
- 취소/재배치 로직, 안전 guardrail

## 하위 디렉토리

| 디렉토리 | Layer | 역할 |
|----------|-------|------|
| `layer1_signal/` | 1 | Signal 데이터 계약 (score, confidence, expected_return) |
| `layer2_position/` | 2 | Target position, risk caps, exposure, turnover budget |
| `layer3_order/` | 3 | Order types, delta compute, constraints, scheduling |
| `layer4_execution/` | 4 | Slicing, placement, cancel/replace, timing, guardrails |

## 데이터 흐름

```
Signal → TargetBuilder (+ RiskCaps) → TargetPosition
  → DeltaComputer → ParentOrder
  → SlicingPolicy → PlacementPolicy → ChildOrder
```

## 전체 파이프라인에서의 위치

Strategy Block(Block 2)에서 컴파일된 전략이 Signal을 생성하면, 이 블록이 실행 계획을 수립한다. 생성된 ChildOrder는 Market Simulation(Block 4)에서 체결 시뮬레이션된다.

## 현재 제한사항

- `execution_policy`의 일부 필드(placement_mode, cancel_after_ticks, max_reprices)만 downstream에서 hint-level로 소비됨
- Full production OMS 수준의 주문 관리가 아님
- Almgren-Chriss 슬라이서는 η, γ, σ 파라미터 캘리브레이션 필요

## 관련 문서

- [../../PIPELINE.md](../../PIPELINE.md) — Block 3: Execution Planning 상세
- [../../ADR.md](../../ADR.md) — ADR-003(Parent-Child), ADR-004(슬라이싱)
