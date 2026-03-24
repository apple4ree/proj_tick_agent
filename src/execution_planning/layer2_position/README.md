# layer2_position/ — 포지션 관리 (Layer 2)

Signal을 받아 목표 포지션(TargetPosition)을 계산하고, 포트폴리오 수준의 리스크/노출/턴오버 제약을 적용한다.

## 핵심 역할

- Signal → TargetPosition 변환 (3종 sizing 모드)
- 포트폴리오 리스크 제한 (gross/net exposure, concentration, leverage)
- 노출 추적 및 중립화
- 턴오버 예산 관리 및 최소 보유 기간 강제
- 포지션 상태 추적 (FIFO 비용 기준, mark-to-market)

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `target_builder.py` | `TargetBuilder`, `TargetPosition` | Signal → 목표 포지션 (signal_proportional / fixed / Kelly) |
| `risk_caps.py` | `RiskCaps`, `RiskReport` | 총노출/순노출/집중도/레버리지 제한 및 비례 축소 |
| `exposure_controller.py` | `ExposureController` | 노출 추적, HHI 집중도, net 중립화 |
| `turnover_budget.py` | `TurnoverBudget` | 일일 턴오버 예산, 거래비용 추정, 최소 보유 기간 |
| `state_estimator.py` | `PortfolioStateEstimator` | 포지션/현금/PnL 추적, FIFO 실현손익 |

## 전체 파이프라인에서의 위치

```
Signal (Layer 1) → TargetBuilder + RiskCaps → TargetPosition → DeltaComputer (Layer 3)
```

## 주의사항

- RiskCaps는 제한 초과 시 비례 축소(proportional scaling)로 조정
- Kelly sizing은 `|score|`를 edge fraction으로 사용 (half-Kelly)
- TurnoverBudget은 스프레드 기반 거래비용 추정 포함

## 관련 문서

- [../layer1_signal/README.md](../layer1_signal/README.md) — 입력 Signal
- [../layer3_order/README.md](../layer3_order/README.md) — Target → Order 변환
