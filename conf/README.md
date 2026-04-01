# conf/ — 설정 파일

`src/utils/config.py`의 `load_config()`가 이 디렉토리의 파일을 계층적으로 병합하여 최종 설정을 생성한다.

## Config Stack (자동 병합 순서)

1. `app.yaml` — 앱 이름, env, log_level, timezone
2. `paths.yaml` — data_dir(H0STASP0), outputs_dir
3. `generation.yaml` — 전략 생성 관련 설정
4. `backtest_base.yaml` — initial_cash, seed, fee_model, impact_model, slicing, placement
5. `backtest_worker.yaml` — latencies_ms sweep
6. `workers.yaml` — poll_interval, once flag

이후 `--profile`로 지정한 프로필 YAML이 병합되고, `--config`로 지정한 명시적 override가 최종 적용된다.

## 프로필

| 프로필 | env | 특징 |
|--------|-----|------|
| `dev` | dev | auto_approve, DEBUG log |
| `smoke` | dev | latencies [0, 100], once: true |
| `prod` | prod | WARNING log |

```bash
PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "..." --symbol 005930 --start-date 20260313 \
    --profile smoke --mode mock
```

## advanced/ — 참조용 파일

Config stack에 포함되지 않는 참조용 파일들.

| 파일 | 용도 |
|------|------|
| `backtest_core.yaml` | PipelineRunner 직접 로드용 상세 설정 템플릿 |
| `baseline.yaml` | 실험 baseline 설정 (005930, KRX fee, linear impact) |
| `baseline_mini.yaml` | 빠른 반복용 축소 baseline |
| `env_config.yaml` | RL 환경 설정 (legacy, 미사용) |
| `train_config.yaml` | RL 학습 설정 (legacy, 미사용) |
