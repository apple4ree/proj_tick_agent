# Architecture

## 시스템 블록

```
data (Layer 0)
  → strategy_loop (LLM 생성 + Hard Gate)
    → execution_planning (Layer 1~4)
      → market_simulation (Layer 5)
        → evaluation_orchestration (Layer 6~7)
```

## 1) strategy_loop — LLM 전략 탐색 루프

`src/strategy_loop/`

```
research_goal
  → PromptBuilder
    → OpenAIClient (live / mock)
      → JSON spec
        → HardGate (구조 / 피처명 / 의미 검증)
          → [pass] SimpleSpecStrategy
                     → PipelineRunner (백테스트)
                       → FeedbackGenerator (LLM 피드백)
                         → MemoryStore
                           → 다음 이터레이션
          → [fail] 즉시 재생성
```

### JSON Spec 포맷

```json
{
  "name": "전략명",
  "entry": {
    "side": "long",
    "condition": {"type": "comparison", "feature": "order_imbalance", "op": ">", "threshold": 0.1},
    "size": 10
  },
  "exit": {
    "condition": {"type": "any", "conditions": [...]}
  },
  "risk": {"max_position": 100}
}
```

조건 노드 타입: `comparison`, `any`, `all`, `not`  
`comparison`의 left/right: `feature` (BUILTIN_FEATURES), `position_attr` (holding_ticks 등), `const`

### Strategy ABC

`src/strategy_block/strategy/base.py` — `generate_signal(state) → Signal | None`  
`SimpleSpecStrategy`가 구현. `_holding_ticks`, `_in_position` 상태를 내부 관리한다.

## 2) data — Layer 0

KIS H0STASP0 CSV → `DataIngestion` → 정제/동기화 → 피처 계산 → `MarketState`

`BUILTIN_FEATURES` (`src/strategy_block/strategy_compiler/v2/features.py`):
전략 스펙에서 `feature` 키로 참조 가능한 피처 목록.

## 3) execution_planning — Layer 1~4

| Layer | 모듈 | 역할 |
|-------|------|------|
| 1 | `layer1_signal/` | `Signal` 데이터 계약 |
| 2 | `layer2_position/` | Signal → TargetPosition (sizing, risk caps) |
| 3 | `layer3_order/` | TargetPosition → ParentOrder (델타 계산) |
| 4 | `layer4_execution/` | ParentOrder → ChildOrder (슬라이싱/배치/취소) |

## 4) market_simulation — Layer 5

`layer5_simulator/`: ChildOrder → FillEvent

- 5종 대기열 모델 (`queue_models/`)
- KRX 수수료/세금 (KOSPI 매수 1.5bps, 매도 19.5bps)
- 확률적 latency 모델
- FIFO P&L bookkeeper

## 5) evaluation_orchestration — Layer 6~7

| Layer | 모듈 | 역할 |
|-------|------|------|
| 6 | `layer6_evaluator/` | PnL/Risk/Execution/Turnover/Attribution 메트릭 |
| 7 | `layer7_validation/` | `PipelineRunner` — 백테스트 실행 오케스트레이션 |

`PipelineRunner`는 `observed_state`(delayed)와 `true_state`를 분리해 observation lag를 구현한다.  
`positions_history`는 60틱마다 1회 샘플링 (성능 최적화).

## 6) monitoring

`InstrumentedPipelineRunner` / `InstrumentedFillSimulator`: 이벤트 버스 + verifier (fee/latency/queue/slippage) + reporter.

## 백테스트 데이터 플로우

```
spec
  → SimpleSpecStrategy.generate_signal(observed_state)
    → Signal → TargetPosition → ParentOrder → ChildOrder
      → FillSimulator(true_state) → FillEvent
        → Bookkeeper (P&L 누적)
          → ReportBuilder → summary.json / CSVs / plots
```

`market_data_delay_ms=0`이면 `observed_state == true_state` (zero-cost fast path).
