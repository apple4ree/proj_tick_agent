# tests/

현재 canonical pytest suite. 208개 테스트.

```bash
cd /home/dgu/tick/proj_rl_agent
PYTHONPATH=src pytest tests/ -q
```

## 커버리지 영역

| 파일 | 커버 대상 |
|------|----------|
| `test_pipeline_runner.py` | PipelineRunner 백테스트 실행 |
| `test_backtest_config.py` | BacktestConfig 파라미터 검증 |
| `test_backtest_constraint_context.py` | 백테스트 제약 컨텍스트 |
| `test_component_factory.py` | ComponentFactory 조립 |
| `test_config.py` | 설정 로드/병합/프로필 |
| `test_goal_presets.py` | goal preset 파일 형식 |
| `test_latency_semantics.py` | latency 시간 의미론 |
| `test_layer3_orders.py` | Layer 3 주문 생성 |
| `test_layer4_execution.py` | Layer 4 실행 전술 |
| `test_layer5_fee_impact.py` | 수수료/impact 계산 |
| `test_matching_engine.py` | LOB 매칭 엔진 |
| `test_monitoring_integration.py` | 이벤트 버스 + verifier |
| `test_pnl_ledger_fixes.py` | PnL 원장 계산 |
| `test_queue_position.py` | 대기열 위치 모델 |
| `test_selection_metrics.py` | SelectionScore 계산 |
| `test_short_position_pnl.py` | 숏 포지션 P&L |
| `test_state_builder.py` | MarketStateBuilder |
| `test_v2_execution_hint_integration.py` | 실행 hint 통합 |

## 주의

- `conftest.py`가 `src/`를 `sys.path`에 추가한다.
- 대부분 테스트는 mock/template backend로 실데이터 없이 동작한다.
- 일부 통합 테스트는 `conf/paths.yaml`의 `data_dir` 또는 임시 synthetic CSV를 사용한다.
