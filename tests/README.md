# tests/

`tests/`는 현재 canonical pytest suite다. 전체 삭제 대상이 아니며, validation tier와 freeze/docs contract를 포함한 active quality gate를 유지한다. 보관 목적의 테스트 디렉토리는 두지 않는다.

## Validation Tiers

```bash
# Smoke: 빠른 wiring 확인
./scripts/internal/ops/run_validation_tiers.sh smoke

# Stronger: 현재 quality gate용 통합/회귀
./scripts/internal/ops/run_validation_tiers.sh stronger

# 전체 tier
./scripts/internal/ops/run_validation_tiers.sh all
```

### Smoke

- `test_generation_direct_mode.py`
- `test_v2_execution_hint_integration.py`
- `test_backtest_script.py`

### Stronger

- `test_v2_stronger_integration.py`
- `test_pipeline_runner.py`
- `test_v2_phase3.py`
- `test_registry_v2_integration.py`
- `test_backtest_worker.py`

Smoke/Stronger 바깥의 나머지 테스트도 canonical suite의 일부다. tier 스크립트에 없더라도 현재 문서 계약, freeze, reviewer/runtime semantics, selection/promotion regression을 고정한다.

## Canonical Coverage Axes

- Generation / review: direct generation, OpenAI/mock lowering, prompt/schema strictness, review pipeline, repair patching, reviewer hard gates
- Backtest realism: delayed observation, latency, queue semantics, feedback, execution-policy/backtest-context interaction, CLI/runtime orchestration
- Freeze / docs contract: public CLI surface, docs hierarchy, benchmark freeze artifact contract, end-to-end smoke freeze
- Walk-forward / family-aware selection: trial registry/accounting, family index, selection metrics, walk-forward harness and selector
- Promotion / export: deployment contract, promotion gate, bundle export

Focused lower-level guards도 유지한다. 예를 들면 `test_v2_phase2.py`, `test_v2_position_attr.py`, `test_position_attr_validation.py`, `test_visualize_intraday_plot.py`는 active runtime/schema behavior를 직접 고정한다.

## Notes

- `conftest.py`가 `src/`를 `sys.path`에 추가한다.
- 대부분 테스트는 mock/template backend로 실데이터 없이 동작한다.
- 일부 통합 테스트는 `conf/paths.yaml`의 `data_dir` 또는 임시 synthetic CSV를 사용한다.
