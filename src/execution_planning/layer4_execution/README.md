# layer4_execution/ — 실행 전술 (Layer 4)

ParentOrder를 ChildOrder로 분할하고, 배치 가격/유형을 결정하며, 미체결 주문의 취소/재배치를 관리한다.

## 핵심 역할

- 4종 슬라이싱 정책으로 ParentOrder → ChildOrder 분할
- 3종 배치 정책으로 주문 가격/유형 결정
- 미체결 주문 모니터링 + 취소/재배치 결정
- 타이밍 로직 (마감 임박, 스프레드 축소, 거래량 급증 감지)
- 안전 guardrail (최대 자식 크기, slippage 한도, 긴급 청산)

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `slicing_policy.py` | `TWAPSlicer`, `VWAPSlicer`, `POVSlicer`, `AlmgrenChrissSlicer` | 주문 분할 |
| `placement_policy.py` | `AggressivePlacement`, `PassivePlacement`, `SpreadAdaptivePlacement` | 배치 가격/유형 결정 |
| `cancel_replace.py` | `CancelReplaceLogic` | 타임아웃/역선택/stale price 기반 취소·재배치 |
| `timing_logic.py` | `TimingLogic` | 마감 임박, 스프레드 축소, 거래량 급증, imbalance 감지 |
| `safety_guardrails.py` | `SafetyGuardrails` | 최대 자식 크기, slippage 한도, 긴급 청산 |

## 배치 정책

| 정책 | 방식 | 비용 |
|------|------|------|
| Aggressive | 스프레드 건너서 IOC/MARKET | 스프레드 전액 부담, 확실한 체결 |
| Passive | best bid/ask에 LIMIT DAY | 스프레드 수취, 체결 불확실 |
| SpreadAdaptive | 스프레드/imbalance 기반 혼합 | 상황에 따라 동적 결정 |

Signal의 `tags.placement_mode`로 override 가능 (hint-level 소비).

## 취소/재배치 트리거

- **타임아웃**: 기본 30초 초과 미체결
- **역선택**: mid price가 주문 반대 방향으로 5bps 이상 이동
- **Stale price**: 주문가가 best에서 2~4 level 이상 이탈
- **Max reprices**: 재배치 횟수 한도 초과 시 취소만

## Execution Policy 소비 현황

`execution_policy`의 다음 필드가 hint-level로 소비됨:
- `placement_mode` → `resolve_placement_policy()`에서 배치 정책 선택
- `cancel_after_ticks` → CancelReplaceLogic 타임아웃에 참조 가능
- `max_reprices` → CancelReplaceLogic repricing 한도

**전면 반영은 아님.** `adaptation_rules`, `do_not_trade_when` 등은 Compiler(Layer 상위)에서 처리되고, 이 레이어에서는 signal tags를 통해 간접 수신한다.

## Passive Queue Approximation

Layer 7의 `FillSimulator`는 passive 성격의 child limit 주문에 한해 최소 queue-position 모델을 적용한다.

- L2 기반 근사치: 주문 제출 시점의 해당 price level displayed qty를 `queue_ahead_qty`로 초기화
- `risk_adverse`: 동일 가격 체결량만 ahead queue 감소로 인정
- `prob_queue`: 동일 가격 체결량 + 일부 depth 감소를 ahead queue 감소로 인정
- `queue_model=none`: queue gate 비활성화 (기존 즉시 체결 경로 유지)
- aggressive/marketable 주문은 queue gate를 우회하고 기존 체결 경로를 따른다

이 모델은 passive fill 과대평가를 줄이기 위한 최소 L2 근사이며, full L3 재구성/venue-specific OMS를 대체하지 않는다.

## 주의사항

- SafetyGuardrails: 80% 시간 경과 + 50% 잔량 + 200bps 역행 시 긴급 청산(MARKET) 발동
- POV 슬라이서만 `on_fill()` 콜백 필요 (동적 적응)
- Almgren-Chriss는 η, γ, σ 파라미터 필요 (캘리브레이션 리스크)
- 4 슬라이서 × 3 배치정책 = 12가지 조합 가능

## 관련 문서

- [../layer3_order/README.md](../layer3_order/README.md) — ParentOrder 생성
- [../../market_simulation/layer5_simulator/README.md](../../market_simulation/layer5_simulator/README.md) — ChildOrder 체결 시뮬레이션
- [../../../../ADR.md](../../../../ADR.md) — ADR-004(슬라이싱 전략)
