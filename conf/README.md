# conf/ — 설정 파일

`src/utils/config.py`의 `load_config()`가 이 디렉토리의 파일을 계층적으로 병합하여 최종 설정을 생성한다.

## Config Stack (자동 병합 순서)

1. `app.yaml` — 앱 이름, env, log_level, timezone
2. `paths.yaml` — data_dir(H0STASP0), registry_dir, jobs_dir, outputs_dir
3. `generation.yaml` — spec_format(v2), backend(template|openai), mode, n_ideas
4. `backtest_base.yaml` — initial_cash, seed, fee_model, impact_model, slicing, placement
5. `backtest_worker.yaml` — latencies_ms sweep, review_gate_required
6. `workers.yaml` — poll_interval, once flag

이후 `--profile`로 지정한 프로필 YAML이 병합되고, `--config`로 지정한 명시적 override가 최종 적용된다.

## 프로필

| 프로필 | env | backend | mode | 특징 |
|--------|-----|---------|------|------|
| `dev` | dev | template | mock | auto_approve, DEBUG log |
| `smoke` | dev | template | mock | latencies [0, 100], once: true |
| `prod` | prod | openai | live | WARNING log, static_review_required |

```bash
PYTHONPATH=src python scripts/backtest.py --spec ... --profile dev
```

## advanced/ — 실험/레거시 설정

Config stack에 포함되지 않는 참조용 파일들.

| 파일 | 용도 |
|------|------|
| `backtest_core.yaml` | qlib-style 상세 설정 템플릿 (PipelineRunner 직접 로드) |
| `baseline.yaml` | 실험 baseline 설정 (005930, KRX fee, linear impact) |
| `baseline_mini.yaml` | 빠른 반복용 축소 baseline |
| `env_config.yaml` | RL 환경 설정 (legacy, 미사용) |
| `train_config.yaml` | RL 학습 설정 (legacy, 미사용) |
| `EXPERIMENT_PROTOCOL.md` | 실험 수행 규칙 |

## 주의사항

- `paths.yaml`의 `data_dir`은 실제 H0STASP0 데이터 위치를 가리켜야 함
- `${VAR}` 및 `${VAR:-default}` 환경변수 확장 지원
