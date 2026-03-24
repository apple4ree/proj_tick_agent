# tests/ — 테스트 스위트

pytest 기반 테스트 모음이다. 파이프라인 전 구간(config → generation → spec → compiler → execution → orchestration)을 커버한다.

## 핵심 역할

- v2 전략 라이프사이클 전체의 단위/통합 테스트
- Validation tier 구분: smoke(빠른 wiring check) vs stronger(통합/회귀)
- Phase별(Phase 2, Phase 3) 기능 검증
- 실데이터 없이 동작하는 mock/template 기반 테스트

## 테스트 범주

### Config / 설정
- `test_config.py` — YAML merge, env 확장, profile override, 경로 해석

### Generation (v2)
- `test_generation_v2.py` — 템플릿 로드, lowering, 컴파일, 리뷰
- `test_generation_direct_mode.py` — direct 모드(queue 우회) 생성

### Spec / Compiler / Reviewer
- `test_strategy_spec_v2.py` — v2 스키마 유효성, 파싱, 직렬화
- `test_compiler_v2.py` — v2 compiler: JSON spec → executable Strategy
- `test_reviewer_v2.py` — 정적 분석, 이슈 감지, 심각도/카테고리

### Backtest 인프라
- `test_backtest_config.py` — BacktestConfig 파싱, yaml/dict 변환
- `test_backtest_script.py` — backtest.py CLI 통합 테스트
- `test_backtest_worker.py` — BacktestWorker, job queue, file locking
- `test_pipeline_runner.py` — PipelineRunner 다종목 백테스트
- `test_component_factory.py` — ComponentFactory 인스턴스화

### Execution Layer (Layer 3~5)
- `test_layer3_orders.py` — 주문 제출, 취소, repricing
- `test_layer4_execution.py` — 체결 시뮬, placement
- `test_layer5_fee_impact.py` — 수수료, 시장충격
- `test_matching_engine.py` — 가격-시간 우선, 부분 체결

### State / PnL
- `test_state_builder.py` — 포지션 상태 추적, PnL 계산
- `test_pnl_ledger_fixes.py` — PnL 원장 정확도, 엣지 케이스

### Orchestration
- `test_orchestration.py` — job queue, worker, 결과 추적
- `test_registry_v2_integration.py` — v2 registry, 버전 관리

### Phase별 v2 테스트
- `test_v2_phase2.py` — Phase 2 (regime, lag/rolling/persist)
- `test_v2_phase3.py` — Phase 3 (state, degradation)
- `test_v2_position_attr.py` — position attribute (unrealized_pnl_bps, holding_ticks)
- `test_v2_execution_hint_integration.py` — execution hint 통합
- `test_v2_stronger_integration.py` — 다종목 × 다 latency 통합

### 시각화
- `test_visualize_intraday_plot.py` — 차트 생성 검증
- `test_experiment_tracker_metrics.py` — Sharpe, MDD, attribution 계산

## Smoke vs Stronger 구분

```bash
# Smoke: 빠른 wiring check (help 출력, 짧은 unit test)
./scripts/run_validation_tiers.sh smoke

# Stronger: 통합/회귀 검증 (fill, latency, impact 실제 발생)
./scripts/run_validation_tiers.sh stronger
```

## 실데이터 의존 여부

- 대부분 테스트는 mock/template backend로 실데이터 없이 동작
- `test_backtest_script.py`, `test_v2_stronger_integration.py` 등 일부 통합 테스트는 `conf/paths.yaml`의 data_dir에 실 데이터가 있어야 동작

## 주의사항

- `conftest.py`가 `src/`를 sys.path에 추가
- `pytest.ini`에서 테스트 설정 관리
- Phase 2/3 테스트는 해당 AST 노드(Lag, Rolling, Persist, StateVar 등)에 의존

## 관련 문서

- [../README.md](../README.md) — Validation Tiers 설명
- [../conf/EXPERIMENT_PROTOCOL.md](../conf/EXPERIMENT_PROTOCOL.md) — 실험 검증 정책
