# Architecture

## 시스템 블록

```
data (Layer 0)
  → strategy_loop (LLM code generation + Hard Gate + dist filter)
    → execution_planning (Layer 1~4)
      → market_simulation (Layer 5)
        → evaluation_orchestration (Layer 6~7)
```

## 1) strategy_loop — 코드 전략 탐색 루프

`src/strategy_loop/`

```
research_goal
  → PromptBuilder (code generation messages)
    → OpenAIClient (live / mock)
      → Python strategy code
        → HardGate.validate_code
          → DistributionFilter.check_code_entry_frequency
            → CodeStrategy
              → PipelineRunner (backtest)
                → FeedbackGenerator (LLM feedback)
                  → MemoryStore / RAG memory
                    → 다음 이터레이션
```

### 코드 인터페이스

생성 코드는 아래를 반드시 정의한다.

- 모듈 상수: `UPPER_CASE` 숫자 상수 (Optuna 최적화 대상)
- 함수: `generate_signal(features, position) -> int | None`

반환값 규약:
- `1`: 진입
- `-1`: 청산
- `None`: 유지

### Strategy ABC

`src/strategy_block/strategy/base.py` — `generate_signal(state) → Signal | None`

`CodeStrategy`가 생성 코드 함수를 호출하고 내부 포지션 상태를 관리한다.

## 2) data — Layer 0

KIS H0STASP0 CSV → `DataIngestion` → 정제/동기화 → 피처 계산 → `MarketState`

`BUILTIN_FEATURES` (`src/strategy_block/strategy_compiler/v2/features.py`):
생성 코드에서 참조 가능한 피처 목록.

## 3) execution_planning — Layer 1~4

| Layer | 모듈 | 역할 |
|-------|------|------|
| 1 | `layer1_signal/` | `Signal` 데이터 계약 |
| 2 | `layer2_position/` | Signal → TargetPosition (sizing, risk caps) |
| 3 | `layer3_order/` | TargetPosition → ParentOrder (델타 계산) |
| 4 | `layer4_execution/` | ParentOrder → ChildOrder (슬라이싱/배치/취소) |

## 4) market_simulation — Layer 5

`layer5_simulator/`: ChildOrder → FillEvent

- 다중 대기열 모델 (`queue_models/`)
- KRX 수수료/세금
- 확률적 latency 모델
- FIFO P&L bookkeeper

## 5) evaluation_orchestration — Layer 6~7

| Layer | 모듈 | 역할 |
|-------|------|------|
| 6 | `layer6_evaluator/` | PnL/Risk/Execution/Turnover/Attribution 메트릭 |
| 7 | `layer7_validation/` | `PipelineRunner` — 백테스트 실행 오케스트레이션 |

## 6) monitoring

`InstrumentedPipelineRunner` / `InstrumentedFillSimulator`: 이벤트 버스 + verifier (fee/latency/queue/slippage) + reporter.
