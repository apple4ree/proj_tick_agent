# tests/

pytest 기반 테스트 모음. Smoke(wiring check)와 Stronger(통합/회귀)로 구분한다.

## Validation Tiers

```bash
# Smoke: CLI help + 짧은 unit test
./scripts/internal/ops/run_validation_tiers.sh smoke

# Stronger: fill/latency/impact 통합/회귀 검증
./scripts/internal/ops/run_validation_tiers.sh stronger

# 전체
./scripts/internal/ops/run_validation_tiers.sh all
```

## Smoke 테스트

| 파일 | 커버리지 |
|------|---------|
| `test_generation_direct_mode.py` | direct 모드 생성 |
| `test_v2_execution_hint_integration.py` | execution hint 통합 |
| `test_backtest_script.py` | backtest.py CLI 통합 |

## Stronger 테스트

| 파일 | 커버리지 |
|------|---------|
| `test_v2_stronger_integration.py` | 다종목 × 다 latency 통합 |
| `test_pipeline_runner.py` | PipelineRunner 다종목 백테스트 |
| `test_v2_phase3.py` | Phase 3 (state, degradation) |
| `test_registry_v2_integration.py` | v2 registry, 버전 관리 |
| `test_backtest_worker.py` | BacktestWorker, job queue |

## 기타 테스트

- Config: `test_config.py`
- Generation: `test_generation_v2.py`
- Spec/Compiler/Reviewer: `test_strategy_spec_v2.py`, `test_compiler_v2.py`, `test_reviewer_v2.py`
- Execution (Layer 3~5): `test_layer3_orders.py`, `test_layer4_execution.py`, `test_layer5_fee_impact.py`, `test_matching_engine.py`
- Queue position: `test_queue_position.py`
- State/PnL: `test_state_builder.py`, `test_pnl_ledger_fixes.py`
- Orchestration: `test_orchestration.py`
- Phase 2: `test_v2_phase2.py`, `test_v2_position_attr.py`
- Visualization: `test_visualize_intraday_plot.py`, `test_experiment_tracker_metrics.py`

## 주의사항

- `conftest.py`가 `src/`를 sys.path에 추가
- 대부분 테스트는 mock/template backend로 실데이터 없이 동작
- 일부 통합 테스트는 `conf/paths.yaml`의 data_dir에 실 데이터 필요
