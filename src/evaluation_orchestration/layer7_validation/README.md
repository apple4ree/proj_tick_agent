# layer7_validation/ — 백테스트 파이프라인 (Layer 7)

백테스트의 핵심 조립 지점이다. PipelineRunner가 Layer 0~6 전체를 오케스트레이션하여 단일 종목 백테스트를 실행한다.

## 핵심 역할

- `PipelineRunner`: 7-Layer 시뮬레이션 루프 실행 (signal → target → order → fill → PnL)
  - `observed_state` / `true_state` 분리: 전략은 지연된 시장 데이터, 체결은 실시간 데이터 사용
- `BacktestConfig`: 설정 파싱/검증/직렬화 (flat + nested qlib-style 지원)
  - `market_data_delay_ms`: 관측 지연 설정 (0 = 기존 동작 유지)
  - `decision_compute_ms`: 전략 판단 지연 (0 = 즉시 결정)
- `FillSimulator`: ChildOrder 체결 위임 (matching + impact + fee + bookkeeper)
- `queue_models/`: 큐 모델 인터페이스 (6종, QueueModel 프로토콜)
- `ReportBuilder`: Layer 6 메트릭 조립 + 결과 저장 (JSON/CSV/plot)
- `ComponentFactory`: config에서 컴포넌트(fee model, slicer 등) 인스턴스화
- `ReproducibilityManager`: seed, config hash, code version 추적

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `pipeline_runner.py` | `PipelineRunner` | MarketState[] → BacktestResult (전체 루프) |
| `backtest_config.py` | `BacktestConfig`, `BacktestResult` | 설정 + 결과 데이터 클래스 |
| `fill_simulator.py` | `FillSimulator` | 체결 시뮬레이션 위임 (parent overfill 방지 포함) |
| `report_builder.py` | `ReportBuilder` | 리포트 생성 + 디스크 저장 (summary.json, realism_diagnostics.json, CSV, plots) |
| `component_factory.py` | `ComponentFactory` | config → 컴포넌트 인스턴스 (fee/impact/latency/slicer/placement) |
| `reproducibility.py` | `ReproducibilityManager` | seed 설정, config hash, DataFrame hash, git version |

## PipelineRunner 실행 루프

```python
for state in states:
    true_state = state                    # 실제 시장 상태 (체결/교환소 측)
    observed_state = lookup(delay_ms)     # 지연된 관측 상태 (전략 측)

    # 1. 마이크로 이벤트 처리 (VI, halt)          → true_state
    # 2. 미체결 주문 관리 (cancel/replace 판단)    → observed_state
    # 3. Signal 생성 (Strategy.generate_signal)   → observed_state
    # 4. Target delta 계산 (RiskCaps, TurnoverBudget) → observed_state
    # 5. ParentOrder 생성                          → observed_state
    # 6. ChildOrder 분할 + 배치                    → observed_state
    # 7. 체결 시뮬레이션 (FillSimulator)            → true_state
    # 8. 계좌 갱신 + PnL 기록
```

`market_data_delay_ms=0` 이면 `observed_state == true_state` (기존 동작 보존).

### Decision Latency (Phase 2)

전략이 관측된 시장 데이터를 보고 실제 주문/수정 결정을 내리기까지 걸리는 시간을 모델링한다.

- `decision_compute_ms`: 전략 계산 시간 (ms). 0 = 즉시 결정.
- **effective state lookup delay = `market_data_delay_ms + decision_compute_ms`**
- observation lag이 *어떤 데이터를 보는가*, decision latency는 *보고 나서 얼마나 걸리는가*
- 적용 범위: signal 생성, target 계산, parent order 생성, child slicing, cancel/replace 판단 모두
- venue latency semantics (nested `latency`)는 decision path와 분리된다:
  - `order_submit_ms`: child가 venue 도착 전에는 queue/fill 대상 아님
  - `cancel_ms`: cancel decision 직후에도 effective 시점 전까지 fill 가능
  - `order_ack_ms`: 현재 phase에서는 reporting/status용 (fill gating에는 미사용)
- flat `latency_ms`는 backward-compat shorthand이며 `latency is None`일 때만 사용된다
  (`submit/ack/cancel = 0.3/0.7/0.2 × latency_ms`)
- `latency`가 명시된 순간(profile-only / partial / full) flat alias는 완전히 비활성화된다
- alias는 `market_data_delay_ms`를 파생하지 않는다
- fill path는 여전히 `true_state` 기준

타이밍 체인:
```
t_true (현재 시뮬레이션 시점)
  → observed_state = lookup(t_true - market_data_delay_ms - decision_compute_ms)
  → 전략 판단 (signal, order, cancel/replace)
  → order submission → venue arrival (order_submit_ms)
  → (optional) cancel request → cancel effective (cancel_ms)
  → fill (true_state 기준, venue arrival 이후 only)
  → ack latency(order_ack_ms)는 reporting/status aggregate로 기록
```

replace exception (intentional minimal model):
- replace decision 시 기존 child는 즉시 cancel 처리
- replacement child는 새 submit lifecycle(submit/arrival metadata)로 시작
- staged replace venue workflow는 현재 scope 밖(deferred)

### Bounded State-History Retention (Phase 2)

per-symbol `_state_history`는 아래를 모두 반영한 최소 horizon + safety buffer만큼만 유지한다.
- effective delay window (`market_data_delay_ms + decision_compute_ms`)
- strategy/runtime lookback (`LagExpr`, `RollingExpr`, `PersistExpr`)

매 tick마다 초과분을 pruning하여 메모리 증가를 방지한다.
특히 `500ms` resolution과 universe backtest에서 중요하다.

### 공식 지원 해상도 (current phase)

| 해상도 | 용도 |
|--------|------|
| `1s`   | 기본 공개 baseline. 소규모 sub-second lag(< 1000ms)는 동일 상태로 수렴할 수 있음. |
| `500ms`| 현재 phase의 유일한 realism-oriented resolution. 200ms 이상 lag에서 실제 stale-state 관측 가능. |

다른 sub-second 값(`100ms`, `250ms` 등)은 지원하지 않으며, 입력 시 `ValueError`로 거부된다.

### observed_state vs true_state

- `observed_state`: `true_state.timestamp - (market_data_delay_ms + decision_compute_ms)` 이하의 가장 최근 실제 historical state lookup 결과. 타임스탬프만 바꾸는 것이 아님.
- `true_state`: fill/exchange-side에서 사용하는 실제 현재 시장 상태.

### runtime_v2 lag stacking

`runtime_v2`의 `LagExpr`, `RollingExpr`, `PersistExpr`는 observation lag 위에 누적된다:

```
effective_lookback = observation_delay + strategy_lag_steps × resample_interval
```

### Observation-lag 리포팅

결과 metadata(`observation_lag`)에서 아래 값을 확인할 수 있다:
- `configured_market_data_delay_ms`: config에서 설정한 관측 지연 값
- `configured_decision_compute_ms`: config에서 설정한 전략 판단 지연 값
- `decision_latency_enabled`: decision latency 활성 여부
- `effective_delay_ms`: 실질 lookup delay (관측 지연 + 판단 지연)
- `resample_interval`: 사용한 resample 해상도
- `canonical_tick_interval_ms`: 한 tick의 wall-clock 지속 시간 (ms)
- `avg_observation_staleness_ms`: 실제 평균 관측 지연 (ms)
- `avg_decision_state_age_ms`: decision path에서 사용된 state age 평균(ms)
- `state_history_max_len`: per-symbol state history 최대 보유량
- `strategy_runtime_lookback_ticks`: 전략 AST에서 추론된 lookback ticks
- `history_safety_buffer_ticks`: history pruning safety buffer ticks

### Tick-time semantics

PipelineRunner는 canonical tick interval을 resample interval에서 유도한다:
- `1s` → 1000.0 ms
- `500ms` → 500.0 ms

모든 tick 기반 파라미터(`cancel_after_ticks`, `holding_ticks`, `cooldown_ticks`,
`LagExpr.steps`, `RollingExpr.window` 등)는 이 canonical tick을 한 단위로 해석된다.

`latency_ms`와 tick interval은 완전히 별개이다:
- `latency_ms`: 주문 제출-확인 지연 (ms, 절대 시간)
- `tick_interval_ms`: resample step 지속 시간 (ms, 데이터 cadence)

cross-resolution 비교 시 tick 기반 파라미터는 자동 정규화되지 않는다.
공정 비교가 필요하면 benchmark/experiment에서 명시적으로 rescale해야 한다.
(예: `cooldown_ticks=30` at 1s → `cooldown_ticks=60` at 500ms)

## Queue-Aware Passive Fill

**FillSimulator가 queue-position semantics의 단일 owner이다.**

Queue 모델은 `queue_models/` 패키지에 명시적 인터페이스(`QueueModel`)로 정의된다.
FillSimulator가 모델을 선택·오케스트레이션하며, MatchingEngine(layer5)은 순수
매칭(price/qty/exchange-model)만 수행한다. Queue gate를 통과한 주문만
MatchingEngine으로 넘어간다.

### Queue 모델 인터페이스 (QueueModel)

```
new_order(child, state)              # 큐 초기화
advance_trade(child, trade_qty)      # 체결에 의한 큐 소진 (공통)
advance_depth(unexplained_drop)      # 모델별 depth 변동 큐 소진
ready_to_match(child, state) → bool  # 게이트 통과 여부
cap_fill(child, state, qty) → int    # 후처리 할당 (pro_rata only)
```

### 지원 모델 (6종)

| 모델 | 유형 | Queue advancement | Fill allocation |
|------|------|-------------------|-----------------|
| `none` | — | 없음 (gate 비활성) | MatchingEngine 결과 그대로 |
| `price_time` | Gate-only | trade-only (depth drop 무시) | MatchingEngine 결과 그대로 |
| `risk_adverse` | Gate-only | trade-only (depth drop 무시) | MatchingEngine 결과 그대로 |
| `prob_queue` | Gate-only | trade + depth-drop × (1−q) | MatchingEngine 결과 그대로 |
| `random` | Gate-only | trade + stochastic depth-drop | MatchingEngine 결과 그대로 |
| `pro_rata` | Gate+Allocation | trade-only (conservative gate) | size-proportional cap 적용 |

### 공통 동작

- 초기화: 주문 진입 시점의 해당 가격 레벨 displayed qty를 `queue_ahead_qty`로 저장
- 트리거: `queue_ahead_qty <= 0`이 되어야 기존 matching path로 체결 가능
- 적용 제외: market/aggressive(시장가성)/IOC/FOK 주문은 기존 동작 유지
- `random` 모델은 `rng_seed`로 재현 가능한 stochastic behavior 보장

이 설계로 queue semantics가 한 곳에서만 적용되며, 중복 적용(double-count) 위험이 제거된다.

주의: 이 구현은 passive fill 과대평가를 줄이기 위한 최소 L2 근사치이며, full L3/MBO 재구성이나 venue-specific OMS 시뮬레이터가 아니다.

## BacktestResult 산출물

- summary.json: 핵심 성과 메트릭 + compact realism fields
  - resample_interval, canonical_tick_interval_ms
  - configured_market_data_delay_ms, avg_observation_staleness_ms
  - configured_decision_compute_ms, decision_latency_enabled, effective_delay_ms
  - queue_model, queue_position_assumption
  - state_history_max_len, strategy_runtime_lookback_ticks
  - avg_child_lifetime_seconds, cancel_rate
  - configured_order_submit_ms, configured_order_ack_ms, configured_cancel_ms, latency_alias_applied
- realism_diagnostics.json: 상세 realism aggregate artifact
  - observation_lag, decision_latency, tick_time, lifecycle
  - queue, latency, cancel_reasons, timings, config_snapshot
  - decision_latency section includes `avg_decision_state_age_ms` aggregated over decision-evaluated steps (not observation-staleness proxy)
  - queue section includes `queue_wait_ticks`, `queue_wait_ms`, `blocked_miss_count`, `ready_but_not_filled_count`
  - latency section includes configured_order_submit_ms/configured_order_ack_ms/configured_cancel_ms, latency_alias_applied, `order_ack_used_for_fill_gating=false`, `cancel_pending_count`, `fills_before_cancel_effective_count`, `avg_cancel_effective_lag_ms`
  - lifecycle section includes hotspots: `max_children_per_parent`, `max_cancelled_children_per_parent`, `top_parent_by_children`
- config.json: 사용된 BacktestConfig
- pnl_series.csv, pnl_entries.csv: PnL 상세
- signals.csv, orders.csv, fills.csv: 시뮬레이션 아티팩트
- market_quotes.csv: 시장 데이터
- plots/: 5종 시각화 (overview, signal, execution, dashboard, intraday_cumulative_profit)

참고: 위 diagnostics는 해석/검증용 집계이며, queue/matching/fill semantics를 변경하지 않는다.

## 전체 파이프라인에서의 위치

이 모듈이 **백테스트 실행의 최상위 조립 지점**이다. `scripts/backtest.py`와 `BacktestWorker`가 여기의 PipelineRunner를 호출한다.

## 주의사항

- BacktestConfig는 flat(하위 호환)과 nested(qlib-style) 설정 모두 지원
- `ComponentFactory`가 config enum 값에 따라 컴포넌트를 결정적으로 생성
- PipelineRunner는 O(1) running TWAP 계산으로 성능 최적화
- Parent overfill 방지가 FillSimulator에 내장됨

## 관련 문서

- [../layer6_evaluator/README.md](../layer6_evaluator/README.md) — 메트릭 계산
- [../orchestration/README.md](../orchestration/README.md) — Worker가 PipelineRunner를 호출
- [../../../../ADR.md](../../../../ADR.md) — ADR-007(PipelineRunner 분해)
