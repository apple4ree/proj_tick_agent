# strategy_compiler/ — v2 Spec 컴파일러

StrategySpecV2 JSON을 실행 가능한 Strategy 객체(CompiledStrategyV2)로 변환한다. **Interpreter-style** 실행 모델을 사용한다.

## 핵심 역할

- StrategySpecV2 → CompiledStrategyV2(Strategy ABC 구현체) 컴파일
- 매 틱마다 AST 노드를 evaluate하여 signal 생성 (transpile이 아닌 interpret)
- 심볼별 런타임 상태(RuntimeStateV2) 관리
- Phase 1~3 전체 실행 의미론 구현

## 대표 파일 (`v2/`)

| 파일 | 역할 |
|------|------|
| `compiler_v2.py` | `CompiledStrategyV2` — Strategy 인터페이스 구현, signal 생성 로직 |
| `runtime_v2.py` | `RuntimeStateV2` — 심볼별 상태, `evaluate_bool()` / `evaluate_float()` AST 평가 |
| `features.py` | `BUILTIN_FEATURES`, `extract_builtin_features()` — MarketState에서 피처 추출 |

## 실행 모델 (generate_signal)

```
MarketState 입력
  1. 빌트인 피처 추출
  2. 피처 히스토리 기록 (lag/rolling/persist용)
  3. [포지션 보유 중] Exit-first 경로:
     - Exit 규칙 평가 (진입 gate가 exit을 차단하지 않음)
     - Exit 시: 포지션 flatten, state event 발화, exit signal 반환
     - 유지 시: trailing price 업데이트
  4. [포지션 미보유] Entry 경로:
     - Gate 1: do_not_trade_when
     - Gate 2: Preconditions
     - Gate 3: State guards (block_entry)
     - Gate 4: Regime 라우팅
     - Degradation rules 적용 (strength_scale, max_pos_scale, allow_entry)
     - Entry policy 평가 → 최고 strength 선택 → entry signal 반환
```

## Runtime State (RuntimeStateV2)

심볼별로 유지되는 상태:
- `position_side`, `position_size`, `entry_price`, `entry_tick`
- `cooldown_until`, `trailing_high`, `trailing_low`
- `state_vars` (Phase 3): 전략 정의 런타임 변수
- `feature_history`: deque 버퍼 (lag/rolling 평가용)
- `persist_history`: PersistExpr별 조건 히스토리

## Execution Hint 연동

CompiledStrategyV2가 생성하는 Signal의 `tags`에 execution hint를 포함:
- `placement_mode`: passive_join / aggressive_cross / adaptive
- `cancel_after_ticks`, `max_reprices`

이 hint는 Layer 4(Execution)에서 **부분적으로** 소비됨. 전면 반영이 아닌 hint-level.

## 주의사항

- Interpreter-style이므로 매 틱마다 AST 전체를 traverse. 성능은 AST 깊이에 비례
- Exit은 entry gate와 독립적으로 평가됨 (exit-first 보장)
- 새 AST 노드 추가 시 `evaluate_bool()` / `evaluate_float()`에 분기 추가 필요
- `reset()`으로 모든 심볼의 런타임 상태 초기화

## 관련 문서

- [../strategy_specs/README.md](../strategy_specs/README.md) — AST 노드 정의
- [../strategy/README.md](../strategy/) — Strategy ABC 인터페이스
- [../../execution_planning/README.md](../../execution_planning/README.md) — execution hint 소비처
